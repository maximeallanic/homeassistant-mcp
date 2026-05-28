#!/usr/bin/env python3
"""
Home Assistant MCP Server

A Model Context Protocol server for Home Assistant integration.
Provides tools to interact with Home Assistant API including:
- Getting states of entities
- Calling services
- Managing automations
- Retrieving system info
"""

import asyncio
import json
import logging
import os
import uuid
import time
from typing import Any, Dict, List, Optional, Union, Set
from urllib.parse import urljoin
from datetime import datetime

import aiohttp
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.lowlevel import NotificationOptions
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("homeassistant-mcp")

def _paginate(
    items: list,
    search: Optional[str] = None,
    search_keys: Optional[List[str]] = None,
    domain: Optional[str] = None,
    offset: int = 0,
    limit: int = 0,
) -> Dict[str, Any]:
    """Apply search filter and pagination to a list of dicts.

    Returns {"items": [...], "total": N, "offset": M, "limit": L}.
    """
    if domain:
        items = [
            i for i in items
            if (i.get("entity_id", "") or "").startswith(f"{domain}.")
            or i.get("domain") == domain
        ]
    if search:
        q = search.lower()
        if search_keys:
            items = [
                i for i in items
                if any(q in str(i.get(k, "")).lower() for k in search_keys)
            ]
        else:
            items = [i for i in items if q in str(i).lower()]
    total = len(items)
    if offset > 0:
        items = items[offset:]
    if limit > 0:
        items = items[:limit]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


class HomeAssistantClient:
    """Home Assistant API client with REST and WebSocket support"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_id: int = 0
        self._ws_authenticated: bool = False
        self._ws_url = self._build_ws_url()

    def _build_ws_url(self) -> str:
        """Build WebSocket URL from base HTTP URL"""
        url = self.base_url
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        return url + "/api/websocket"

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._ws_close()
        if self.session:
            await self.session.close()

    # --- WebSocket methods ---

    async def _ws_ensure_connected(self):
        """Ensure WebSocket is connected and authenticated"""
        if self._ws is not None and not self._ws.closed:
            return

        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        logger.info(f"Connecting WebSocket to {self._ws_url}")
        self._ws = await self.session.ws_connect(self._ws_url)
        self._ws_authenticated = False
        self._ws_id = 0

        # Wait for auth_required message
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Expected auth_required, got: {msg.get('type')}")

        # Send auth
        await self._ws.send_json({
            "type": "auth",
            "access_token": self.token,
        })

        # Wait for auth response
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"WebSocket auth failed: {msg}")

        self._ws_authenticated = True
        logger.info("WebSocket authenticated successfully")

    async def _ws_send_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Send a WebSocket command and wait for result"""
        await self._ws_ensure_connected()

        self._ws_id += 1
        command["id"] = self._ws_id

        await self._ws.send_json(command)

        # Read messages until we get the response for our id
        while True:
            msg = await self._ws.receive_json()
            if msg.get("id") == self._ws_id:
                if not msg.get("success", False):
                    error = msg.get("error", {})
                    raise RuntimeError(
                        f"WebSocket command failed: {error.get('code', 'unknown')} - {error.get('message', str(error))}"
                    )
                return msg.get("result")

    async def _ws_close(self):
        """Close WebSocket connection"""
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._ws_authenticated = False

    async def ws_command(self, type: str, data: Dict[str, Any] = None) -> Any:
        """Send a generic WebSocket command."""
        command = {"type": type}
        if data:
            command.update(data)
        return await self._ws_send_command(command)

    async def get_dashboards(self) -> Any:
        return await self.ws_command("lovelace/dashboards/list")

    async def get_dashboard_config(self, url_path: Optional[str] = None) -> Any:
        cmd = {"type": "lovelace/config"}
        if url_path:
            cmd["url_path"] = url_path
        return await self._ws_send_command(cmd)

    async def save_dashboard_config(self, config: Dict, url_path: Optional[str] = None, force: bool = False) -> Any:
        cmd = {"type": "lovelace/config/save", "config": config}
        if url_path:
            cmd["url_path"] = url_path
        if force:
            cmd["force"] = True
        return await self._ws_send_command(cmd)

    async def create_dashboard(self, title: str, url_path: str, icon: Optional[str] = None,
                               show_in_sidebar: bool = True, require_admin: bool = False) -> Any:
        cmd = {"type": "lovelace/dashboards/create", "title": title,
               "url_path": url_path, "mode": "storage",
               "show_in_sidebar": show_in_sidebar, "require_admin": require_admin}
        if icon:
            cmd["icon"] = icon
        return await self._ws_send_command(cmd)

    async def delete_dashboard(self, dashboard_id: str) -> Any:
        return await self._ws_send_command({"type": "lovelace/dashboards/delete", "dashboard_id": dashboard_id})

    async def get_lovelace_resources(self) -> Any:
        return await self.ws_command("lovelace/resources")

    # Built-in HA Lovelace cards (static list, current as of HA 2024+)
    BUILTIN_CARDS = [
        "alarm-panel", "area", "button", "calendar", "camera", "conditional",
        "energy-carbon-consumed-gauge", "energy-date-selection",
        "energy-devices-detail-graph", "energy-devices-graph",
        "energy-distribution", "energy-gas-gauge", "energy-grid-neutrality-gauge",
        "energy-sources-table", "energy-solar-consumed-gauge",
        "energy-solar-graph", "energy-summary", "energy-usage-graph",
        "energy-water-gauge",
        "entities", "entity", "entity-filter", "gauge", "glance", "grid",
        "history-graph", "horizontal-stack", "humidifier", "iframe", "light",
        "logbook", "map", "markdown", "media-control", "picture",
        "picture-elements", "picture-entity", "picture-glance", "plant-status",
        "sensor", "shopping-list", "statistic", "statistics-graph", "thermostat",
        "tile", "todo-list", "vertical-stack", "weather-forecast", "webpage",
    ]

    async def list_lovelace_cards(self) -> Dict[str, Any]:
        """List all available Lovelace cards: built-in, registered resources, and HACS plugins."""
        result: Dict[str, Any] = {
            "builtin_cards": [f"card-{c}" for c in self.BUILTIN_CARDS],
            "registered_resources": [],
            "hacs_plugins": [],
        }
        # Registered Lovelace resources
        try:
            resources = await self.get_lovelace_resources()
            result["registered_resources"] = resources or []
        except Exception as e:
            result["registered_resources_error"] = str(e)
        # HACS installed plugins
        try:
            repos = await self.hacs_list_repositories(categories=["plugin"])
            result["hacs_plugins"] = [
                {
                    "name": r.get("name"),
                    "full_name": r.get("full_name"),
                    "installed": r.get("installed", False),
                    "version_installed": r.get("installed_version") or r.get("version_installed"),
                    "version_available": r.get("available_version") or r.get("last_version"),
                    "description": r.get("description"),
                }
                for r in (repos if isinstance(repos, list) else [])
                if r.get("installed")
            ]
        except Exception as e:
            result["hacs_plugins_error"] = str(e)
        return result

    # --- HACS methods ---

    async def hacs_status(self) -> Any:
        return await self.ws_command("hacs/info")

    async def hacs_list_repositories(self, categories: Optional[List[str]] = None) -> Any:
        data: Dict[str, Any] = {}
        if categories:
            data["categories"] = categories
        return await self.ws_command("hacs/repositories/list", data or None)

    async def hacs_repository_info(self, repository_id: str) -> Any:
        return await self.ws_command("hacs/repository/info", {"repository_id": repository_id})

    async def hacs_download(self, repository_id: str, version: Optional[str] = None) -> Any:
        data: Dict[str, Any] = {"repository": repository_id}
        if version:
            data["version"] = version
        return await self.ws_command("hacs/repository/download", data)

    async def hacs_remove(self, repository_id: str) -> Any:
        return await self.ws_command("hacs/repository/remove", {"repository": repository_id})

    async def hacs_refresh(self, repository_id: str) -> Any:
        return await self.ws_command("hacs/repository/refresh", {"repository": repository_id})

    async def hacs_releases(self, repository_id: str) -> Any:
        return await self.ws_command("hacs/repository/releases", {"repository_id": repository_id})

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make HTTP request to Home Assistant API.

        Automatically returns JSON or plain text based on response Content-Type.
        """
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        url = urljoin(self.base_url, endpoint)

        try:
            async with self.session.request(
                method, url, headers=self.headers, **kwargs
            ) as response:
                response.raise_for_status()
                ct = response.content_type or ""
                if "json" in ct:
                    return await response.json()
                # /api/template and other endpoints return text/plain
                return await response.text()
        except aiohttp.ClientError as e:
            logger.error(f"HTTP request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise
    
    async def get_states(self) -> List[Dict[str, Any]]:
        """Get all entity states"""
        return await self._request("GET", "/api/states")
    
    async def get_state(self, entity_id: str) -> Dict[str, Any]:
        """Get state of specific entity"""
        return await self._request("GET", f"/api/states/{entity_id}")
    
    async def call_service(self, domain: str, service: str, service_data: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Call a Home Assistant service"""
        endpoint = f"/api/services/{domain}/{service}"
        data = service_data or {}
        return await self._request("POST", endpoint, json=data)
    
    async def get_config(self) -> Dict[str, Any]:
        """Get Home Assistant configuration"""
        return await self._request("GET", "/api/config")
    
    async def get_services(self) -> List[Dict[str, Any]]:
        """Get all available services"""
        return await self._request("GET", "/api/services")
    
    async def get_events(self) -> List[Dict[str, Any]]:
        """Get event types"""
        return await self._request("GET", "/api/events")
    
    async def fire_event(self, event_type: str, event_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Fire an event"""
        endpoint = f"/api/events/{event_type}"
        data = event_data or {}
        return await self._request("POST", endpoint, json=data)
    
    async def get_history(self, start_time: Optional[str] = None, end_time: Optional[str] = None, 
                         filter_entity_id: Optional[str] = None, minimal_response: bool = False,
                         no_attributes: bool = False, significant_changes_only: bool = False) -> List[Dict[str, Any]]:
        """Get historical data"""
        if start_time:
            endpoint = f"/api/history/period/{start_time}"
        else:
            endpoint = "/api/history/period"
        
        params = {}
        if end_time:
            params["end_time"] = end_time
        if filter_entity_id:
            params["filter_entity_id"] = filter_entity_id
        if minimal_response:
            params["minimal_response"] = "true"
        if no_attributes:
            params["no_attributes"] = "true"
        if significant_changes_only:
            params["significant_changes_only"] = "true"
        
        return await self._request("GET", endpoint, params=params)
    
    async def get_logbook(self, start_time: Optional[str] = None, end_time: Optional[str] = None,
                         entity: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get logbook entries"""
        if start_time:
            endpoint = f"/api/logbook/{start_time}"
        else:
            endpoint = "/api/logbook"
        
        params = {}
        if end_time:
            params["end_time"] = end_time
        if entity:
            params["entity"] = entity
        
        return await self._request("GET", endpoint, params=params)
    
    async def get_error_log(self) -> str:
        """Get error log"""
        response = await self._request("GET", "/api/error_log")
        return response if isinstance(response, str) else str(response)
    
    async def get_calendars(self) -> List[Dict[str, Any]]:
        """Get list of calendar entities"""
        return await self._request("GET", "/api/calendars")
    
    async def get_calendar_events(self, calendar_id: str, start: str, end: str) -> List[Dict[str, Any]]:
        """Get calendar events for a specific calendar"""
        endpoint = f"/api/calendars/{calendar_id}"
        params = {"start": start, "end": end}
        return await self._request("GET", endpoint, params=params)
    
    async def get_camera_proxy(self, camera_entity_id: str) -> bytes:
        """Get camera image data"""
        endpoint = f"/api/camera_proxy/{camera_entity_id}"
        # This returns binary data, so we need special handling
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        url = urljoin(self.base_url, endpoint)
        async with self.session.get(url, headers=self.headers) as response:
            response.raise_for_status()
            return await response.read()

    async def get_image_proxy(self, image_entity_id: str) -> bytes:
        """Get image entity data (for image.* entities)"""
        endpoint = f"/api/image_proxy/{image_entity_id}"
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        url = urljoin(self.base_url, endpoint)
        async with self.session.get(url, headers=self.headers) as response:
            response.raise_for_status()
            return await response.read()
    
    async def set_state(self, entity_id: str, state: str, attributes: Optional[Dict] = None) -> Dict[str, Any]:
        """Set or update entity state"""
        endpoint = f"/api/states/{entity_id}"
        data = {"state": state}
        if attributes:
            data["attributes"] = attributes
        return await self._request("POST", endpoint, json=data)
    
    async def delete_state(self, entity_id: str) -> Dict[str, Any]:
        """Delete entity state"""
        endpoint = f"/api/states/{entity_id}"
        return await self._request("DELETE", endpoint)
    
    async def render_template(self, template: str) -> str:
        """Render a Home Assistant template"""
        endpoint = "/api/template"
        data = {"template": template}
        result = await self._request("POST", endpoint, json=data)
        return result if isinstance(result, str) else str(result)
    
    async def check_config(self) -> Dict[str, Any]:
        """Check configuration validity"""
        return await self._request("POST", "/api/config/core/check_config")
    
    async def handle_intent(self, intent_name: str, intent_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Handle an intent"""
        endpoint = "/api/intent/handle"
        data = {"name": intent_name}
        if intent_data:
            data["data"] = intent_data
        return await self._request("POST", endpoint, json=data)
    
    async def call_service_with_response(self, domain: str, service: str, service_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Call service and get response data"""
        endpoint = f"/api/services/{domain}/{service}?return_response"
        data = service_data or {}
        return await self._request("POST", endpoint, json=data)

    async def subscribe_to_events(self) -> aiohttp.ClientResponse:
        """Subscribe to Home Assistant event stream"""
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        url = urljoin(self.base_url, "/api/stream")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"}
        
        return await self.session.get(url, headers=headers)

    async def get_automations(
        self,
        include_disabled: bool = True,
        compact: bool = False,
        search: Optional[str] = None,
        state_filter: Optional[str] = None,
        offset: int = 0,
        limit: int = 0,
    ) -> Dict[str, Any]:
        """Get automations via entity registry (lightweight) with filtering and pagination.

        Returns {"items": [...], "total": N, "offset": M, "limit": L}.
        """
        automations: List[Dict[str, Any]] = []

        try:
            registry = await self._ws_send_command({"type": "config/entity_registry/list"})
            auto_entries = [e for e in registry if e.get("entity_id", "").startswith("automation.")]

            for entry in auto_entries:
                is_disabled = bool(entry.get("disabled_by"))
                if is_disabled and not include_disabled:
                    continue

                if compact:
                    automations.append({
                        "entity_id": entry["entity_id"],
                        "friendly_name": entry.get("original_name") or entry.get("name", ""),
                        "unique_id": entry.get("unique_id"),
                        "disabled_by": entry.get("disabled_by"),
                    })
                else:
                    if not is_disabled:
                        try:
                            state = await self._request("GET", f"/api/states/{entry['entity_id']}")
                            automations.append(state)
                            continue
                        except Exception:
                            pass
                    automations.append({
                        "entity_id": entry["entity_id"],
                        "state": "disabled" if is_disabled else "unknown",
                        "attributes": {
                            "id": entry.get("unique_id"),
                            "friendly_name": entry.get("original_name") or entry.get("name", ""),
                            "icon": entry.get("icon"),
                            "disabled_by": entry.get("disabled_by"),
                        },
                    })

        except Exception as e:
            logger.warning(f"WebSocket entity_registry failed, falling back to /api/states: {e}")
            states = await self._request("GET", "/api/states")
            automations = [s for s in states if s["entity_id"].startswith("automation.")]

        # State filter (specific to automations)
        if state_filter:
            sf = state_filter.lower()
            automations = [a for a in automations if a.get("state", "").lower() == sf]

        return _paginate(automations, search=search,
                         search_keys=["entity_id", "friendly_name"],
                         offset=offset, limit=limit)

    async def get_automation_configs(
        self,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 0,
    ) -> Dict[str, Any]:
        """List UI-managed automation configs with filtering and pagination.

        Returns {"items": [...], "total": N, "offset": M, "limit": L}.
        """
        try:
            configs = await self._ws_send_command({"type": "automation/config", "id_list": True})
            if isinstance(configs, list):
                for c in configs:
                    if isinstance(c, dict) and "_automation_id" not in c:
                        c["_config_available"] = True
                return _paginate(configs, search=search, offset=offset, limit=limit)
        except Exception:
            pass

        # Fallback: iterate known automation IDs from the registry
        results: List[Dict[str, Any]] = []
        inventory = await self.get_automations(compact=True)
        for auto in inventory["items"]:
            uid = auto.get("unique_id")
            if not uid:
                continue
            try:
                config = await self._request("GET", f"/api/config/automation/config/{uid}")
                if isinstance(config, dict):
                    config["_automation_id"] = uid
                    config["_entity_id"] = auto.get("entity_id")
                    config["_config_available"] = True
                    results.append(config)
            except Exception:
                results.append({
                    "_automation_id": uid,
                    "_entity_id": auto.get("entity_id"),
                    "_config_available": False,
                    "_note": "YAML-only automation — config not accessible via API",
                })

        return _paginate(results, search=search, offset=offset, limit=limit)
    
    async def get_automation(self, automation_id: str) -> Dict[str, Any]:
        """Get specific automation details including full config (triggers, conditions, actions)"""
        state = await self._request("GET", f"/api/states/{automation_id}")
        # The config endpoint uses the internal id (from attributes), not the entity_id suffix
        internal_id = state.get("attributes", {}).get("id")
        if internal_id:
            try:
                config = await self._request("GET", f"/api/config/automation/config/{internal_id}")
                state["config"] = config
                state["config_available"] = True
            except Exception:
                # Config endpoint fails for YAML-only automations
                state["config_available"] = False
                state["config_note"] = (
                    "Full automation config unavailable via API. "
                    "This automation is likely defined in automations.yaml (not via UI). "
                    "Only partial state attributes are available. "
                    "To see the full YAML, read the automations.yaml file directly on the HA host."
                )
        else:
            state["config_available"] = False
            state["config_note"] = (
                "No internal automation ID found in attributes. "
                "Cannot fetch full config from the API."
            )
        return state
    
    async def toggle_automation(self, automation_id: str) -> List[Dict[str, Any]]:
        """Toggle an automation on/off"""
        return await self.call_service("automation", "toggle", {"entity_id": automation_id})
    
    async def turn_on_automation(self, automation_id: str) -> List[Dict[str, Any]]:
        """Turn on an automation"""
        return await self.call_service("automation", "turn_on", {"entity_id": automation_id})
    
    async def turn_off_automation(self, automation_id: str) -> List[Dict[str, Any]]:
        """Turn off an automation"""
        return await self.call_service("automation", "turn_off", {"entity_id": automation_id})
    
    async def trigger_automation(self, automation_id: str) -> List[Dict[str, Any]]:
        """Manually trigger an automation"""
        return await self.call_service("automation", "trigger", {"entity_id": automation_id})
    
    async def reload_automations(self) -> List[Dict[str, Any]]:
        """Reload all automations"""
        return await self.call_service("automation", "reload")
    
    @staticmethod
    def _normalize_automation_keys(config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize singular to plural keys for HA 2026.x format."""
        for key in ('trigger', 'action', 'condition'):
            if key in config and f'{key}s' not in config:
                config[f'{key}s'] = config.pop(key)
        return config

    async def create_automation(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new automation via REST API.
        Checks for duplicates by ID and alias before creating.
        Also checks disabled automations in the entity registry to prevent
        creating a duplicate that would conflict with a disabled entry.
        config must include an 'id' field used as the automation identifier."""
        automation_id = config.get("id")
        alias = config.get("alias", "")

        # Fetch existing automations (including disabled) to check for duplicates.
        # get_automations() returns a paginated dict {"items": [...], ...}; the
        # automation records live under "items". Iterating the dict directly would
        # yield its string keys and crash with "'str' object has no attribute 'get'".
        existing = await self.get_automations(include_disabled=True)
        existing_items = existing.get("items", []) if isinstance(existing, dict) else existing

        # Check by ID — if the automation_id already exists, update instead
        if automation_id:
            for auto in existing_items:
                existing_id = auto.get("attributes", {}).get("id", "")
                if existing_id == automation_id:
                    disabled_by = auto.get("attributes", {}).get("disabled_by")
                    if disabled_by:
                        logger.warning(
                            f"Automation '{automation_id}' exists but is disabled "
                            f"(disabled_by={disabled_by}). Re-enabling and updating."
                        )
                        # Re-enable the entity before updating
                        try:
                            await self._ws_send_command({
                                "type": "config/entity_registry/update",
                                "entity_id": auto["entity_id"],
                                "disabled_by": None,
                            })
                        except Exception as e:
                            logger.warning(f"Could not re-enable entity: {e}")
                    else:
                        logger.info(f"Automation '{automation_id}' already exists, updating instead of creating")
                    return await self.update_automation(automation_id, config)

        # Check by alias — if an automation with the same name exists, update it
        if alias:
            for auto in existing_items:
                existing_alias = auto.get("attributes", {}).get("friendly_name", "")
                if existing_alias and existing_alias.strip().lower() == alias.strip().lower():
                    existing_id = auto.get("attributes", {}).get("id", "")
                    if existing_id:
                        disabled_by = auto.get("attributes", {}).get("disabled_by")
                        if disabled_by:
                            logger.warning(
                                f"Automation with alias '{alias}' exists but is disabled. "
                                f"Re-enabling and updating."
                            )
                            try:
                                await self._ws_send_command({
                                    "type": "config/entity_registry/update",
                                    "entity_id": auto["entity_id"],
                                    "disabled_by": None,
                                })
                            except Exception as e:
                                logger.warning(f"Could not re-enable entity: {e}")
                        else:
                            logger.info(f"Automation with alias '{alias}' already exists (id: {existing_id}), updating instead of creating")
                        config["id"] = existing_id
                        return await self.update_automation(existing_id, config)

        # No duplicate found — create new
        if not automation_id:
            automation_id = f"auto_{uuid.uuid4().hex[:8]}"
            config["id"] = automation_id
        self._normalize_automation_keys(config)
        endpoint = f"/api/config/automation/config/{automation_id}"
        return await self._request("POST", endpoint, json=config)

    async def update_automation(self, automation_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing automation via REST API.

        HA config endpoint uses POST for both create and update — PUT is not
        supported and silently triggers a reload that overwrites the changes."""
        self._normalize_automation_keys(config)
        config["id"] = automation_id
        endpoint = f"/api/config/automation/config/{automation_id}"
        return await self._request("POST", endpoint, json=config)

    async def delete_automation(self, automation_id: str) -> Dict[str, Any]:
        """Delete an automation via REST API"""
        endpoint = f"/api/config/automation/config/{automation_id}"
        return await self._request("DELETE", endpoint)
    
    async def get_automation_trace(self, automation_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Get automation trace information"""
        if run_id:
            endpoint = f"/api/trace/automation/{automation_id}/get/{run_id}"
        else:
            endpoint = f"/api/trace/automation/{automation_id}"
        return await self._request("GET", endpoint)
    
    async def get_scenes(self, search: Optional[str] = None, offset: int = 0, limit: int = 0) -> Dict[str, Any]:
        """Get all scenes with optional search and pagination."""
        states = await self._request("GET", "/api/states")
        scenes = [s for s in states if s["entity_id"].startswith("scene.")]
        return _paginate(scenes, search=search, search_keys=["entity_id"], offset=offset, limit=limit)
    
    async def activate_scene(self, scene_id: str) -> List[Dict[str, Any]]:
        """Activate a scene"""
        return await self.call_service("scene", "turn_on", {"entity_id": scene_id})
    
    async def create_scene(self, scene_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create a new scene"""
        return await self.call_service("scene", "create", scene_data)

    # Script Management (WebSocket)
    async def get_scripts(self) -> List[Dict[str, Any]]:
        """Get all scripts via entity states"""
        states = await self._request("GET", "/api/states")
        return [s for s in states if s["entity_id"].startswith("script.")]

    async def create_script(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new script via REST API.
        config should include script definition (alias, sequence, etc.).
        script_id is derived from config['id'] or auto-generated."""
        script_id = config.pop("id", None) or config.pop("script_id", None)
        if not script_id:
            script_id = f"script_{uuid.uuid4().hex[:8]}"
        endpoint = f"/api/config/script/config/{script_id}"
        return await self._request("POST", endpoint, json=config)

    async def update_script(self, script_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing script via REST API"""
        endpoint = f"/api/config/script/config/{script_id}"
        return await self._request("POST", endpoint, json=config)

    async def delete_script(self, script_id: str) -> Dict[str, Any]:
        """Delete a script via REST API"""
        endpoint = f"/api/config/script/config/{script_id}"
        return await self._request("DELETE", endpoint)

    async def reload_scripts(self) -> List[Dict[str, Any]]:
        """Reload all scripts"""
        return await self.call_service("script", "reload")

    # Area Management
    async def get_areas(self) -> List[Dict[str, Any]]:
        """Get all areas/zones"""
        return await self._request("GET", "/api/config/area_registry")
    
    async def create_area(self, name: str, aliases: Optional[List[str]] = None) -> Dict[str, Any]:
        """Create a new area"""
        data = {"name": name}
        if aliases:
            data["aliases"] = aliases
        return await self._request("POST", "/api/config/area_registry", json=data)
    
    async def update_area(self, area_id: str, name: str, aliases: Optional[List[str]] = None) -> Dict[str, Any]:
        """Update an existing area"""
        data = {"name": name}
        if aliases:
            data["aliases"] = aliases
        return await self._request("POST", f"/api/config/area_registry/{area_id}", json=data)
    
    async def delete_area(self, area_id: str) -> Dict[str, Any]:
        """Delete an area"""
        return await self._request("DELETE", f"/api/config/area_registry/{area_id}")
    
    # Device Management
    async def get_devices(self, search: Optional[str] = None, offset: int = 0, limit: int = 0) -> Dict[str, Any]:
        """Get all devices with optional search and pagination."""
        devices = await self._ws_send_command({"type": "config/device_registry/list"})
        return _paginate(devices, search=search, search_keys=["name", "name_by_user", "manufacturer", "model"],
                         offset=offset, limit=limit)
    
    async def get_device(self, device_id: str) -> Dict[str, Any]:
        """Get specific device information"""
        result = await self.get_devices()
        for device in result["items"]:
            if device.get("id") == device_id:
                return device
        raise ValueError(f"Device {device_id} not found")
    
    async def update_device(self, device_id: str, name: Optional[str] = None, area_id: Optional[str] = None, 
                           disabled_by: Optional[str] = None) -> Dict[str, Any]:
        """Update device configuration"""
        data = {}
        if name is not None:
            data["name_by_user"] = name
        if area_id is not None:
            data["area_id"] = area_id
        if disabled_by is not None:
            data["disabled_by"] = disabled_by
        return await self._ws_send_command({"type": "config/device_registry/update", "device_id": device_id, **data})
    
    async def get_entities_by_area(self, area_id: str, search: Optional[str] = None,
                                   domain: Optional[str] = None, offset: int = 0, limit: int = 0) -> Dict[str, Any]:
        """Get all entities in a specific area with optional search/domain filter and pagination."""
        entity_registry = await self._ws_send_command({"type": "config/entity_registry/list"})
        device_result = await self.get_devices()
        all_devices = device_result["items"] if isinstance(device_result, dict) else device_result

        area_devices = {d["id"] for d in all_devices if d.get("area_id") == area_id}

        area_entities = [
            e for e in entity_registry
            if e.get("area_id") == area_id or e.get("device_id") in area_devices
        ]
        return _paginate(area_entities, search=search, search_keys=["entity_id", "original_name", "name"],
                         domain=domain, offset=offset, limit=limit)
    
    # System Management
    async def restart_homeassistant(self) -> Dict[str, Any]:
        """Restart Home Assistant"""
        return await self.call_service("homeassistant", "restart")
    
    async def stop_homeassistant(self) -> Dict[str, Any]:
        """Stop Home Assistant"""
        return await self.call_service("homeassistant", "stop")
    
    async def check_config_valid(self) -> Dict[str, Any]:
        """Check if configuration is valid"""
        return await self._request("POST", "/api/config/core/check_config")
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get system health information"""
        try:
            return await self._request("GET", "/api/system_health/info")
        except:
            # Fallback if system_health is not available
            return {"status": "System health API not available"}
    
    async def get_supervisor_info(self) -> Dict[str, Any]:
        """Get supervisor information (if available)"""
        try:
            return await self._request("GET", "/api/hassio/supervisor/info")
        except:
            return {"error": "Supervisor not available (not running Home Assistant OS/Supervised)"}
    
    async def get_system_info(self) -> Dict[str, Any]:
        """Get system information"""
        try:
            return await self._request("GET", "/api/hassio/host/info")
        except:
            # Return basic config info if supervisor not available
            config = await self.get_config()
            return {
                "host_info": "Not available (not running Home Assistant OS)",
                "version": config.get("version"),
                "installation_type": config.get("installation_type", "unknown")
            }
    
    # Integration Management  
    async def get_integrations(self, search: Optional[str] = None, domain: Optional[str] = None,
                               offset: int = 0, limit: int = 0) -> Dict[str, Any]:
        """Get all configured integrations with optional search/domain filter and pagination."""
        entries = await self.ws_command("config_entries/get")
        return _paginate(entries, search=search, search_keys=["title", "domain"],
                         domain=domain, offset=offset, limit=limit)
    
    async def reload_integration(self, integration_domain: str) -> Dict[str, Any]:
        """Reload a specific integration"""
        return await self.call_service(integration_domain, "reload")
    
    async def delete_integration(self, config_entry_id: str) -> Dict[str, Any]:
        """Delete/remove an integration"""
        return await self._request("DELETE", f"/api/config/config_entries/{config_entry_id}")
    
    async def disable_integration(self, config_entry_id: str) -> Dict[str, Any]:
        """Disable an integration"""
        return await self._request("POST", f"/api/config/config_entries/{config_entry_id}/disable")
    
    async def enable_integration(self, config_entry_id: str) -> Dict[str, Any]:
        """Enable an integration"""
        return await self._request("POST", f"/api/config/config_entries/{config_entry_id}/enable")
    
    async def get_integration_info(self, config_entry_id: str) -> Dict[str, Any]:
        """Get information about a specific integration"""
        result = await self.get_integrations()
        for integration in result["items"]:
            if integration.get("entry_id") == config_entry_id:
                return integration
        raise ValueError(f"Integration {config_entry_id} not found")

    async def list_config_entries(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """List config entries, optionally filtered by domain"""
        result = await self.get_integrations(domain=domain)
        return result["items"]

    async def start_integration_flow(self, handler: str) -> Dict[str, Any]:
        """Initiate a config flow for a given integration domain"""
        return await self._request("POST", "/api/config/config_entries/flow",
                                   json={"handler": handler, "show_advanced_options": False})

    async def get_integration_flow(self, flow_id: str) -> Dict[str, Any]:
        """Get the current state of an in-progress config flow"""
        return await self._request("GET", f"/api/config/config_entries/flow/{flow_id}")

    async def submit_integration_flow(self, flow_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Submit form data for a config flow step"""
        return await self._request("POST", f"/api/config/config_entries/flow/{flow_id}", json=data)
    
    # Notification Services
    async def send_notification(self, message: str, title: Optional[str] = None, 
                               target: Optional[str] = None, data: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Send a persistent notification"""
        service_data = {"message": message}
        if title:
            service_data["title"] = title
        if data:
            service_data.update(data)
        
        # Use notify service if target specified, otherwise persistent notification
        if target:
            return await self.call_service("notify", target, service_data)
        else:
            return await self.call_service("persistent_notification", "create", service_data)
    
    async def get_notification_services(self) -> List[str]:
        """Get available notification services"""
        services = await self.get_services()
        notify_services = []
        
        for domain, domain_services in services.items():
            if domain == "notify":
                notify_services.extend(domain_services.keys())
        
        return notify_services
    
    async def dismiss_notification(self, notification_id: str) -> List[Dict[str, Any]]:
        """Dismiss a persistent notification"""
        return await self.call_service("persistent_notification", "dismiss", 
                                     {"notification_id": notification_id})
    
    # Entity Registry Management
    async def get_entity_registry(self, search: Optional[str] = None, domain: Optional[str] = None,
                                  offset: int = 0, limit: int = 0) -> Dict[str, Any]:
        """Get entity registry with optional search/domain filter and pagination."""
        entities = await self._ws_send_command({"type": "config/entity_registry/list"})
        return _paginate(entities, search=search, search_keys=["entity_id", "original_name", "name"],
                         domain=domain, offset=offset, limit=limit)
    
    async def update_entity_registry(self, entity_id: str, name: Optional[str] = None, 
                                   disabled_by: Optional[str] = None, area_id: Optional[str] = None) -> Dict[str, Any]:
        """Update entity registry entry"""
        data = {}
        if name is not None:
            data["name"] = name
        if disabled_by is not None:
            data["disabled_by"] = disabled_by
        if area_id is not None:
            data["area_id"] = area_id
        
        return await self._ws_send_command({"type": "config/entity_registry/update", "entity_id": entity_id, **data})
    
    async def enable_entity(self, entity_id: str) -> Dict[str, Any]:
        """Enable an entity"""
        return await self.update_entity_registry(entity_id, disabled_by=None)
    
    async def disable_entity(self, entity_id: str) -> Dict[str, Any]:
        """Disable an entity"""
        return await self.update_entity_registry(entity_id, disabled_by="user")

class SSESubscription:
    """Represents a single SSE subscription"""
    
    def __init__(self, client_id: str, events: Set[str] = None, entity_id: str = None, domain: str = None):
        self.client_id = client_id
        self.events = events or set()
        self.entity_id = entity_id
        self.domain = domain
        self.created_at = datetime.now()
        self.last_activity = time.time()
    
    def matches_event(self, event_type: str, entity_id: str = None) -> bool:
        """Check if this subscription should receive the event"""
        # Check if event type matches
        if self.events and event_type not in self.events:
            return False
        
        # Check entity filter
        if self.entity_id and entity_id != self.entity_id:
            return False
        
        # Check domain filter
        if self.domain and entity_id and not entity_id.startswith(f"{self.domain}."):
            return False
        
        return True

class SSEManager:
    """Manages SSE connections and subscriptions"""
    
    def __init__(self):
        self.subscriptions: Dict[str, SSESubscription] = {}
        self.active_connections: Dict[str, aiohttp.ClientResponse] = {}
        self.connection_tasks: Dict[str, asyncio.Task] = {}
        self.max_connections = 100
        self.rate_limit = 1000  # requests per minute
        self.rate_counters: Dict[str, List[float]] = {}
        self.ping_interval = 30  # seconds
    
    def add_subscription(self, events: Set[str] = None, entity_id: str = None, domain: str = None) -> str:
        """Add a new subscription and return client ID"""
        if len(self.subscriptions) >= self.max_connections:
            raise ValueError("Maximum connections exceeded")
        
        client_id = str(uuid.uuid4())
        self.subscriptions[client_id] = SSESubscription(client_id, events, entity_id, domain)
        self.rate_counters[client_id] = []
        
        logger.info(f"Added SSE subscription {client_id}")
        return client_id
    
    def remove_subscription(self, client_id: str):
        """Remove a subscription"""
        self.subscriptions.pop(client_id, None)
        self.rate_counters.pop(client_id, None)
        
        # Clean up connection if exists
        if client_id in self.active_connections:
            self.active_connections[client_id].close()
            self.active_connections.pop(client_id, None)
        
        if client_id in self.connection_tasks:
            self.connection_tasks[client_id].cancel()
            self.connection_tasks.pop(client_id, None)
        
        logger.info(f"Removed SSE subscription {client_id}")
    
    def check_rate_limit(self, client_id: str) -> bool:
        """Check if client is within rate limit"""
        now = time.time()
        if client_id not in self.rate_counters:
            self.rate_counters[client_id] = []
        
        # Clean old requests (older than 1 minute)
        self.rate_counters[client_id] = [
            timestamp for timestamp in self.rate_counters[client_id]
            if now - timestamp < 60
        ]
        
        # Check if under limit
        if len(self.rate_counters[client_id]) >= self.rate_limit:
            return False
        
        # Add current request
        self.rate_counters[client_id].append(now)
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current SSE statistics"""
        return {
            "active_connections": len(self.subscriptions),
            "max_connections": self.max_connections,
            "rate_limit_per_minute": self.rate_limit,
            "subscriptions": [
                {
                    "client_id": sub.client_id,
                    "events": list(sub.events),
                    "entity_id": sub.entity_id,
                    "domain": sub.domain,
                    "created_at": sub.created_at.isoformat(),
                }
                for sub in self.subscriptions.values()
            ]
        }
    
    async def start_event_stream(self, client_id: str, ha_url: str, ha_token: str):
        """Start event stream for a client"""
        if client_id not in self.subscriptions:
            return
        
        async def event_stream_task():
            try:
                async with HomeAssistantClient(ha_url, ha_token) as client:
                    response = await client.subscribe_to_events()
                    self.active_connections[client_id] = response
                    
                    async for line in response.content:
                        if client_id not in self.subscriptions:
                            break
                        
                        line = line.decode('utf-8').strip()
                        if not line:
                            continue
                        
                        # Parse SSE format
                        if line.startswith('data: '):
                            try:
                                event_data = json.loads(line[6:])
                                await self._handle_ha_event(client_id, event_data)
                            except json.JSONDecodeError:
                                continue
            except Exception as e:
                logger.error(f"Error in event stream for {client_id}: {e}")
            finally:
                self.remove_subscription(client_id)
        
        self.connection_tasks[client_id] = asyncio.create_task(event_stream_task())
    
    async def _handle_ha_event(self, client_id: str, ha_event: Dict[str, Any]):
        """Handle Home Assistant event and forward if subscription matches"""
        if client_id not in self.subscriptions:
            return
        
        subscription = self.subscriptions[client_id]
        event_type = ha_event.get('event_type', '')
        
        # Extract entity_id from event data if available
        entity_id = None
        if 'data' in ha_event:
            if 'entity_id' in ha_event['data']:
                entity_id = ha_event['data']['entity_id']
            elif 'new_state' in ha_event['data'] and ha_event['data']['new_state']:
                entity_id = ha_event['data']['new_state'].get('entity_id')
        
        if subscription.matches_event(event_type, entity_id):
            # Transform HA event to SSE format
            sse_event = self._transform_to_sse_event(ha_event)
            # In a real implementation, you'd send this to the client
            # For MCP, we'll store recent events that can be retrieved
            logger.info(f"Event for {client_id}: {sse_event['type']}")
    
    def _transform_to_sse_event(self, ha_event: Dict[str, Any]) -> Dict[str, Any]:
        """Transform Home Assistant event to SSE format"""
        event_type = ha_event.get('event_type', '')
        
        if event_type == 'state_changed':
            new_state = ha_event['data'].get('new_state', {})
            return {
                "type": "state_changed",
                "data": {
                    "entity_id": new_state.get('entity_id'),
                    "state": new_state.get('state'),
                    "attributes": new_state.get('attributes', {}),
                    "last_changed": new_state.get('last_changed'),
                    "last_updated": new_state.get('last_updated')
                },
                "timestamp": datetime.now().isoformat()
            }
        elif event_type == 'service_called':
            return {
                "type": "service_called",
                "data": ha_event['data'],
                "timestamp": datetime.now().isoformat()
            }
        elif event_type == 'automation_triggered':
            return {
                "type": "automation_triggered",
                "data": ha_event['data'],
                "timestamp": datetime.now().isoformat()
            }
        elif event_type == 'script_started':
            return {
                "type": "script_executed",
                "data": ha_event['data'],
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "type": event_type,
                "data": ha_event.get('data', {}),
                "timestamp": datetime.now().isoformat()
            }

# Initialize SSE manager
sse_manager = SSEManager()

# Initialize MCP server
server = Server("homeassistant-mcp")

# Configuration
HA_URL = os.getenv("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")

if not HA_TOKEN:
    logger.warning("HA_TOKEN not set. You'll need to provide it via environment variable.")

@server.list_resources()
async def handle_list_resources() -> List[Resource]:
    """List available Home Assistant resources"""
    return [
        Resource(
            uri="homeassistant://states",
            name="All Entity States",
            description="Current state of all Home Assistant entities",
            mimeType="application/json",
        ),
        Resource(
            uri="homeassistant://config",
            name="Home Assistant Configuration",
            description="Home Assistant system configuration",
            mimeType="application/json",
        ),
        Resource(
            uri="homeassistant://services",
            name="Available Services",
            description="List of all available Home Assistant services",
            mimeType="application/json",
        ),
        Resource(
            uri="homeassistant://events",
            name="Event Types",
            description="List of available event types",
            mimeType="application/json",
        ),
        Resource(
            uri="homeassistant://sse",
            name="SSE Events",
            description="Subscribe to real-time Home Assistant events",
            mimeType="text/event-stream",
        )
    ]

@server.read_resource()
async def handle_read_resource(uri: str) -> str:
    """Read Home Assistant resource data"""
    if not HA_TOKEN:
        return json.dumps({"error": "HA_TOKEN not configured"})
    
    try:
        async with HomeAssistantClient(HA_URL, HA_TOKEN) as client:
            if uri == "homeassistant://states":
                data = await client.get_states()
            elif uri == "homeassistant://config":
                data = await client.get_config()
            elif uri == "homeassistant://services":
                data = await client.get_services()
            elif uri == "homeassistant://events":
                data = await client.get_events()
            else:
                return json.dumps({"error": f"Unknown resource: {uri}"})
            
            return json.dumps(data, indent=2)
    
    except Exception as e:
        logger.error(f"Error reading resource {uri}: {e}")
        return json.dumps({"error": str(e)})

@server.list_tools()
async def handle_list_tools() -> List[Tool]:
    """List available Home Assistant tools"""
    return [
        Tool(
            name="get_entity_state",
            description="Get the current state of a Home Assistant entity",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID (e.g., light.living_room, sensor.temperature)"
                    }
                },
                "required": ["entity_id"]
            }
        ),
        Tool(
            name="call_service",
            description="Call a Home Assistant service",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Service domain (e.g., light, switch, automation)"
                    },
                    "service": {
                        "type": "string",
                        "description": "Service name (e.g., turn_on, turn_off, toggle)"
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Target entity ID (optional)"
                    },
                    "service_data": {
                        "type": "object",
                        "description": "Additional service data (optional)"
                    }
                },
                "required": ["domain", "service"]
            }
        ),
        Tool(
            name="search_entities",
            description="Search for entities by name, domain, or state",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (entity name, domain, or state)"
                    },
                    "domain": {
                        "type": "string",
                        "description": "Filter by domain (optional)"
                    },
                    "state": {
                        "type": "string",
                        "description": "Filter by state (optional)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_area_entities",
            description="Get all entities in a specific area",
            inputSchema={
                "type": "object",
                "properties": {
                    "area_name": {
                        "type": "string",
                        "description": "Name of the area"
                    }
                },
                "required": ["area_name"]
            }
        ),
        Tool(
            name="fire_event",
            description="Fire a custom Home Assistant event",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "Type of event to fire"
                    },
                    "event_data": {
                        "type": "object",
                        "description": "Event data payload (optional)"
                    }
                },
                "required": ["event_type"]
            }
        ),
        Tool(
            name="get_history",
            description="Get historical data for entities with advanced filtering options",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of entity IDs to get history for"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time (ISO format, optional)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time (ISO format, optional)"
                    },
                    "minimal_response": {
                        "type": "boolean",
                        "description": "Return minimal response for faster queries",
                        "default": False
                    },
                    "no_attributes": {
                        "type": "boolean",
                        "description": "Skip attributes in response for faster queries",
                        "default": False
                    },
                    "significant_changes_only": {
                        "type": "boolean",
                        "description": "Only return significant state changes",
                        "default": False
                    }
                },
                "required": ["entity_ids"]
            }
        ),
        Tool(
            name="get_logbook",
            description="Get logbook entries (event history)",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "Start time (ISO format, optional)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time (ISO format, optional)"
                    },
                    "entity": {
                        "type": "string",
                        "description": "Filter to specific entity (optional)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="set_state",
            description="Set or update the state of an entity (does not control devices, use call_service for that)",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to set state for"
                    },
                    "state": {
                        "type": "string",
                        "description": "New state value"
                    },
                    "attributes": {
                        "type": "object",
                        "description": "Entity attributes (optional)"
                    }
                },
                "required": ["entity_id", "state"]
            }
        ),
        Tool(
            name="delete_state",
            description="Delete an entity state",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to delete"
                    }
                },
                "required": ["entity_id"]
            }
        ),
        Tool(
            name="render_template",
            description="Render a Home Assistant template",
            inputSchema={
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "description": "Template string to render (e.g., 'The temperature is {{ states(\"sensor.temperature\") }}')"
                    }
                },
                "required": ["template"]
            }
        ),
        Tool(
            name="check_config",
            description="Check Home Assistant configuration validity",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_error_log",
            description="Retrieve Home Assistant error log",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_calendars",
            description="Get list of calendar entities",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_calendar_events",
            description="Get events from a specific calendar",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar entity ID (e.g., calendar.personal)"
                    },
                    "start": {
                        "type": "string",
                        "description": "Start timestamp (ISO format)"
                    },
                    "end": {
                        "type": "string",
                        "description": "End timestamp (ISO format)"
                    }
                },
                "required": ["calendar_id", "start", "end"]
            }
        ),
        Tool(
            name="get_camera_image",
            description="Get image from a camera entity (returns base64 encoded image, auto-resized to max 800px and compressed for efficiency)",
            inputSchema={
                "type": "object",
                "properties": {
                    "camera_entity_id": {
                        "type": "string",
                        "description": "Camera entity ID (e.g., camera.front_door)"
                    },
                    "max_size": {
                        "type": "integer",
                        "description": "Max dimension in pixels (default: 800). Image is resized keeping aspect ratio."
                    },
                    "quality": {
                        "type": "integer",
                        "description": "JPEG quality 1-95 (default: 60). Lower = smaller but blurrier."
                    }
                },
                "required": ["camera_entity_id"]
            }
        ),
        Tool(
            name="get_image_entity",
            description="Get image from an image.* entity (returns base64 encoded image, auto-resized and compressed). Use this for image.* entities, NOT camera.* entities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_entity_id": {
                        "type": "string",
                        "description": "Image entity ID (e.g., image.front_door_person)"
                    },
                    "max_size": {
                        "type": "integer",
                        "description": "Max dimension in pixels (default: 800). Image is resized keeping aspect ratio."
                    },
                    "quality": {
                        "type": "integer",
                        "description": "JPEG quality 1-95 (default: 60). Lower = smaller but blurrier."
                    }
                },
                "required": ["image_entity_id"]
            }
        ),
        Tool(
            name="call_service_with_response",
            description="Call a service that returns response data (like weather forecasts)",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Service domain"
                    },
                    "service": {
                        "type": "string",
                        "description": "Service name"
                    },
                    "service_data": {
                        "type": "object",
                        "description": "Service data payload"
                    }
                },
                "required": ["domain", "service"]
            }
        ),
        Tool(
            name="handle_intent",
            description="Handle a Home Assistant intent",
            inputSchema={
                "type": "object",
                "properties": {
                    "intent_name": {
                        "type": "string",
                        "description": "Intent name (e.g., SetTimer, GetWeather)"
                    },
                    "intent_data": {
                        "type": "object",
                        "description": "Intent data (optional)"
                    }
                },
                "required": ["intent_name"]
            }
        ),
        Tool(
            name="subscribe_events",
            description="Subscribe to real-time Home Assistant events via SSE",
            inputSchema={
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Event types to subscribe to (e.g., ['state_changed', 'service_called'])",
                        "default": []
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Filter events to specific entity (optional)"
                    },
                    "domain": {
                        "type": "string",
                        "description": "Filter events to specific domain (optional)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_sse_stats",
            description="Get current SSE connection statistics",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_automations",
            description="Get list of all automations (uses lightweight registry, not /api/states). Use compact=true for a minimal inventory. Supports search, state filter, and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "compact": {
                        "type": "boolean",
                        "description": "If true, return only entity_id, friendly_name, unique_id, disabled_by (no full state). Much faster and smaller.",
                        "default": False
                    },
                    "search": {
                        "type": "string",
                        "description": "Filter by entity_id or friendly_name (case-insensitive substring match)"
                    },
                    "state": {
                        "type": "string",
                        "description": "Filter by state value (e.g., 'on', 'off', 'disabled')"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Skip first N results (default 0)",
                        "default": 0
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (0 = all, default 0)",
                        "default": 0
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_automation_configs",
            description="Get raw configs (triggers, conditions, actions) for all UI-managed automations. YAML-only automations are flagged. Supports search and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Filter configs by keyword (case-insensitive, searches all fields)"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Skip first N results (default 0)",
                        "default": 0
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (0 = all, default 0)",
                        "default": 0
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_automation",
            description="Get details of a specific automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID (e.g., automation.morning_routine)"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        Tool(
            name="toggle_automation",
            description="Toggle an automation on/off",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to toggle"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        Tool(
            name="turn_on_automation",
            description="Turn on an automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to turn on"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        Tool(
            name="turn_off_automation",
            description="Turn off an automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to turn off"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        Tool(
            name="trigger_automation",
            description="Manually trigger an automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to trigger"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        Tool(
            name="create_automation",
            description="Create a new automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "Automation configuration with alias, triggers, conditions, and actions (HA 2026.x plural format; singular forms are auto-normalized)",
                        "properties": {
                            "alias": {
                                "type": "string",
                                "description": "Human readable name for the automation"
                            },
                            "description": {
                                "type": "string",
                                "description": "Description of what the automation does"
                            },
                            "triggers": {
                                "type": "array",
                                "description": "Trigger configurations (e.g., time, state, event)"
                            },
                            "conditions": {
                                "type": "array",
                                "description": "Condition configurations (optional)"
                            },
                            "actions": {
                                "type": "array",
                                "description": "Actions to perform when triggered"
                            }
                        },
                        "required": ["alias", "triggers", "actions"]
                    }
                },
                "required": ["config"]
            }
        ),
        Tool(
            name="update_automation",
            description="Update an existing automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to update"
                    },
                    "config": {
                        "type": "object",
                        "description": "Updated automation configuration"
                    }
                },
                "required": ["automation_id", "config"]
            }
        ),
        Tool(
            name="delete_automation",
            description="Delete an automation",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to delete"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        Tool(
            name="reload_automations",
            description="Reload all automations from configuration",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_automation_trace",
            description="Get automation execution trace information",
            inputSchema={
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "Automation entity ID to get trace for"
                    },
                    "run_id": {
                        "type": "string",
                        "description": "Specific run ID to get trace for (optional)"
                    }
                },
                "required": ["automation_id"]
            }
        ),
        # Script Management Tools
        Tool(
            name="list_scripts",
            description="Get list of all scripts",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="create_script",
            description="Create a new script via REST API",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_id": {
                        "type": "string",
                        "description": "Unique script identifier (e.g., 'aspirateur_cuisine' becomes script.aspirateur_cuisine)"
                    },
                    "config": {
                        "type": "object",
                        "description": "Script configuration with alias, sequence, mode, etc.",
                        "properties": {
                            "alias": {
                                "type": "string",
                                "description": "Human readable name for the script"
                            },
                            "description": {
                                "type": "string",
                                "description": "Description of what the script does"
                            },
                            "sequence": {
                                "type": "array",
                                "description": "List of actions to execute"
                            },
                            "mode": {
                                "type": "string",
                                "description": "Script execution mode: single, restart, queued, parallel",
                                "enum": ["single", "restart", "queued", "parallel"]
                            },
                            "icon": {
                                "type": "string",
                                "description": "Icon for the script (e.g., mdi:play)"
                            },
                            "fields": {
                                "type": "object",
                                "description": "Input fields/parameters for the script"
                            }
                        },
                        "required": ["alias", "sequence"]
                    }
                },
                "required": ["script_id", "config"]
            }
        ),
        Tool(
            name="update_script",
            description="Update an existing script via REST API",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_id": {
                        "type": "string",
                        "description": "Script object_id (e.g., 'my_script' from script.my_script)"
                    },
                    "config": {
                        "type": "object",
                        "description": "Updated script configuration"
                    }
                },
                "required": ["script_id", "config"]
            }
        ),
        Tool(
            name="delete_script",
            description="Delete a script via REST API",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_id": {
                        "type": "string",
                        "description": "Script object_id to delete (e.g., 'my_script' from script.my_script)"
                    }
                },
                "required": ["script_id"]
            }
        ),
        Tool(
            name="reload_scripts",
            description="Reload all scripts from configuration",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_scenes",
            description="Get list of all scenes. Supports search and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Filter by entity_id (case-insensitive)"},
                    "offset": {"type": "integer", "description": "Skip first N results", "default": 0},
                    "limit": {"type": "integer", "description": "Max results (0=all)", "default": 0}
                },
                "required": []
            }
        ),
        Tool(
            name="activate_scene",
            description="Activate a scene",
            inputSchema={
                "type": "object",
                "properties": {
                    "scene_id": {
                        "type": "string",
                        "description": "Scene entity ID (e.g., scene.movie_time)"
                    }
                },
                "required": ["scene_id"]
            }
        ),
        Tool(
            name="create_scene",
            description="Create a new scene from current entity states",
            inputSchema={
                "type": "object",
                "properties": {
                    "scene_data": {
                        "type": "object",
                        "description": "Scene configuration with scene_id and entities",
                        "properties": {
                            "scene_id": {
                                "type": "string",
                                "description": "ID for the new scene"
                            },
                            "entities": {
                                "type": "object",
                                "description": "Entity states to capture in the scene"
                            }
                        },
                        "required": ["scene_id", "entities"]
                    }
                },
                "required": ["scene_data"]
            }
        ),
        # Area Management Tools
        Tool(
            name="get_areas",
            description="Get list of all areas/zones in Home Assistant",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="create_area",
            description="Create a new area/zone",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the area to create"
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional aliases for the area"
                    }
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="update_area",
            description="Update an existing area",
            inputSchema={
                "type": "object",
                "properties": {
                    "area_id": {
                        "type": "string",
                        "description": "ID of the area to update"
                    },
                    "name": {
                        "type": "string",
                        "description": "New name for the area"
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Updated aliases for the area"
                    }
                },
                "required": ["area_id", "name"]
            }
        ),
        Tool(
            name="delete_area",
            description="Delete an area/zone",
            inputSchema={
                "type": "object",
                "properties": {
                    "area_id": {
                        "type": "string",
                        "description": "ID of the area to delete"
                    }
                },
                "required": ["area_id"]
            }
        ),
        # Device Management Tools
        Tool(
            name="get_devices",
            description="Get list of all devices. Supports search (name, manufacturer, model) and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Filter by name, manufacturer, or model"},
                    "offset": {"type": "integer", "description": "Skip first N results", "default": 0},
                    "limit": {"type": "integer", "description": "Max results (0=all)", "default": 0}
                },
                "required": []
            }
        ),
        Tool(
            name="get_device",
            description="Get information about a specific device",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID of the device to get information for"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="update_device",
            description="Update device configuration (name, area assignment, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID of the device to update"
                    },
                    "name": {
                        "type": "string",
                        "description": "New name for the device"
                    },
                    "area_id": {
                        "type": "string",
                        "description": "Area ID to assign device to"
                    },
                    "disabled_by": {
                        "type": "string",
                        "description": "Disable device (set to 'user' to disable, null to enable)"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="get_entities_by_area",
            description="Get all entities in a specific area. Supports search, domain filter, and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "area_id": {"type": "string", "description": "ID of the area to get entities for"},
                    "search": {"type": "string", "description": "Filter by entity_id or name"},
                    "domain": {"type": "string", "description": "Filter by domain (e.g., 'light', 'switch', 'sensor')"},
                    "offset": {"type": "integer", "description": "Skip first N results", "default": 0},
                    "limit": {"type": "integer", "description": "Max results (0=all)", "default": 0}
                },
                "required": ["area_id"]
            }
        ),
        # System Management Tools
        Tool(
            name="restart_homeassistant",
            description="Restart Home Assistant system",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="stop_homeassistant",
            description="Stop Home Assistant system",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="check_config_valid",
            description="Check if Home Assistant configuration is valid",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_system_health",
            description="Get system health information",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_supervisor_info",
            description="Get Home Assistant supervisor information (if available)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_system_info",
            description="Get system/host information",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        # Integration Management Tools
        Tool(
            name="get_integrations",
            description="Get list of all configured integrations. Supports search (title, domain), domain filter, and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Filter by title or domain"},
                    "domain": {"type": "string", "description": "Filter by integration domain (e.g., 'hue', 'zwave')"},
                    "offset": {"type": "integer", "description": "Skip first N results", "default": 0},
                    "limit": {"type": "integer", "description": "Max results (0=all)", "default": 0}
                },
                "required": []
            }
        ),
        Tool(
            name="reload_integration",
            description="Reload a specific integration",
            inputSchema={
                "type": "object",
                "properties": {
                    "integration_domain": {
                        "type": "string",
                        "description": "Domain of the integration to reload (e.g., 'zwave_js', 'mqtt')"
                    }
                },
                "required": ["integration_domain"]
            }
        ),
        Tool(
            name="disable_integration",
            description="Disable an integration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_entry_id": {
                        "type": "string",
                        "description": "Config entry ID of the integration to disable"
                    }
                },
                "required": ["config_entry_id"]
            }
        ),
        Tool(
            name="enable_integration",
            description="Enable a disabled integration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_entry_id": {
                        "type": "string",
                        "description": "Config entry ID of the integration to enable"
                    }
                },
                "required": ["config_entry_id"]
            }
        ),
        Tool(
            name="delete_integration",
            description="Delete/remove an integration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_entry_id": {
                        "type": "string",
                        "description": "Config entry ID of the integration to delete"
                    }
                },
                "required": ["config_entry_id"]
            }
        ),
        Tool(
            name="get_integration_info",
            description="Get detailed information about a specific integration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_entry_id": {
                        "type": "string",
                        "description": "Config entry ID of the integration"
                    }
                },
                "required": ["config_entry_id"]
            }
        ),
        # Notification Services Tools
        Tool(
            name="send_notification",
            description="Send a notification via Home Assistant notification services",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Notification message content"
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional notification title"
                    },
                    "target": {
                        "type": "string",
                        "description": "Target notification service (e.g., 'mobile_app_phone', 'persistent_notification')"
                    },
                    "data": {
                        "type": "object",
                        "description": "Additional notification data (optional)"
                    }
                },
                "required": ["message"]
            }
        ),
        Tool(
            name="get_notification_services",
            description="Get list of available notification services",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="dismiss_notification",
            description="Dismiss a persistent notification",
            inputSchema={
                "type": "object",
                "properties": {
                    "notification_id": {
                        "type": "string",
                        "description": "ID of the notification to dismiss"
                    }
                },
                "required": ["notification_id"]
            }
        ),
        # Entity Registry Management Tools
        Tool(
            name="get_entity_registry",
            description="Get entity registry. Supports search (entity_id, name), domain filter, and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Filter by entity_id or name"},
                    "domain": {"type": "string", "description": "Filter by domain (e.g., 'light', 'sensor', 'automation')"},
                    "offset": {"type": "integer", "description": "Skip first N results", "default": 0},
                    "limit": {"type": "integer", "description": "Max results (0=all)", "default": 0}
                },
                "required": []
            }
        ),
        Tool(
            name="update_entity_registry",
            description="Update entity registry entry (name, area, enabled/disabled status)",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to update"
                    },
                    "name": {
                        "type": "string",
                        "description": "New name for the entity"
                    },
                    "area_id": {
                        "type": "string",
                        "description": "Area ID to assign entity to"
                    },
                    "disabled_by": {
                        "type": "string",
                        "description": "Disable entity (set to 'user' to disable, null to enable)"
                    }
                },
                "required": ["entity_id"]
            }
        ),
        Tool(
            name="enable_entity",
            description="Enable a disabled entity",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to enable"
                    }
                },
                "required": ["entity_id"]
            }
        ),
        Tool(
            name="disable_entity",
            description="Disable an entity",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to disable"
                    }
                },
                "required": ["entity_id"]
            }
        ),
        Tool(
            name="websocket_call",
            description="Send a generic WebSocket command to Home Assistant. Use for operations only available via WS API (entity icons, subscribe_trigger, lovelace, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "WS command type, e.g. 'lovelace/config'"},
                    "data": {"type": "object", "description": "Additional command parameters (merged into the command)"}
                },
                "required": ["type"]
            }
        ),
        Tool(
            name="list_dashboards",
            description="List all Lovelace dashboards configured in Home Assistant",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="get_dashboard_config",
            description="Get the full configuration of a Lovelace dashboard",
            inputSchema={
                "type": "object",
                "properties": {
                    "url_path": {"type": "string", "description": "Dashboard URL path (omit for default dashboard)"}
                }
            }
        ),
        Tool(
            name="update_dashboard_config",
            description="Save/update a Lovelace dashboard configuration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {"type": "object", "description": "Full dashboard config object"},
                    "url_path": {"type": "string", "description": "Dashboard URL path (omit for default)"},
                    "force": {"type": "boolean", "description": "Force save even if config was edited in UI"}
                },
                "required": ["config"]
            }
        ),
        Tool(
            name="create_dashboard",
            description="Create a new Lovelace dashboard",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url_path": {"type": "string", "description": "URL slug, e.g. 'tablets'"},
                    "icon": {"type": "string", "description": "MDI icon, e.g. 'mdi:tablet'"},
                    "show_in_sidebar": {"type": "boolean", "default": True},
                    "require_admin": {"type": "boolean", "default": False}
                },
                "required": ["title", "url_path"]
            }
        ),
        Tool(
            name="delete_dashboard",
            description="Delete a Lovelace dashboard by its dashboard_id",
            inputSchema={
                "type": "object",
                "properties": {
                    "dashboard_id": {"type": "string", "description": "Dashboard ID (from list_dashboards)"}
                },
                "required": ["dashboard_id"]
            }
        ),
        Tool(
            name="list_lovelace_resources",
            description="List all registered Lovelace JS resources (custom card bundles loaded in the frontend)",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="list_lovelace_cards",
            description="List all available Lovelace card types: built-in HA cards, registered custom resources, and installed HACS plugins (frontend cards)",
            inputSchema={"type": "object", "properties": {}}
        ),
        # HACS tools
        Tool(
            name="hacs_status",
            description="Get HACS (Home Assistant Community Store) status and configuration",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="hacs_list_repositories",
            description="List HACS repositories. Filter by one or more categories: integration, plugin (custom cards), theme, python_script, appdaemon, netdaemon. Omit categories for all.",
            inputSchema={
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["integration", "plugin", "theme", "python_script", "appdaemon", "netdaemon"]
                        },
                        "description": "Categories to filter by (omit for all)"
                    }
                }
            }
        ),
        Tool(
            name="hacs_repository_info",
            description="Get detailed information about a specific HACS repository by its numeric ID (from hacs_list_repositories)",
            inputSchema={
                "type": "object",
                "properties": {
                    "repository_id": {
                        "type": "string",
                        "description": "HACS repository ID (numeric string)"
                    }
                },
                "required": ["repository_id"]
            }
        ),
        Tool(
            name="hacs_download",
            description="Download (install or update) a HACS repository to its latest version or a specific version tag",
            inputSchema={
                "type": "object",
                "properties": {
                    "repository_id": {
                        "type": "string",
                        "description": "HACS repository ID (numeric string)"
                    },
                    "version": {
                        "type": "string",
                        "description": "Version tag to install (omit for latest)"
                    }
                },
                "required": ["repository_id"]
            }
        ),
        Tool(
            name="hacs_remove",
            description="Uninstall a HACS repository (removes the downloaded files)",
            inputSchema={
                "type": "object",
                "properties": {
                    "repository_id": {
                        "type": "string",
                        "description": "HACS repository ID (numeric string)"
                    }
                },
                "required": ["repository_id"]
            }
        ),
        Tool(
            name="hacs_refresh",
            description="Refresh a HACS repository metadata (check for new versions without downloading)",
            inputSchema={
                "type": "object",
                "properties": {
                    "repository_id": {
                        "type": "string",
                        "description": "HACS repository ID (numeric string)"
                    }
                },
                "required": ["repository_id"]
            }
        ),
        Tool(
            name="hacs_releases",
            description="Get available release versions for a HACS repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repository_id": {
                        "type": "string",
                        "description": "HACS repository ID (numeric string)"
                    }
                },
                "required": ["repository_id"]
            }
        ),
        # HA restart & integration management
        Tool(
            name="restart_homeassistant",
            description="Restart Home Assistant core (graceful restart, keeps add-ons running)",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="list_config_entries",
            description="List all configured integrations (config entries) in Home Assistant",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Filter by integration domain (e.g. 'hue', 'mqtt')"
                    }
                }
            }
        ),
        Tool(
            name="start_integration_flow",
            description="Start a configuration flow to add a new integration. Returns flow_id for subsequent steps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "handler": {
                        "type": "string",
                        "description": "Integration domain to configure (e.g. 'hue', 'mqtt', 'zha')"
                    }
                },
                "required": ["handler"]
            }
        ),
        Tool(
            name="get_integration_flow",
            description="Get current state of an in-progress integration configuration flow",
            inputSchema={
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "description": "Flow ID returned by start_integration_flow"
                    }
                },
                "required": ["flow_id"]
            }
        ),
        Tool(
            name="submit_integration_flow",
            description="Submit a step in an integration configuration flow (provide form data)",
            inputSchema={
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "description": "Flow ID returned by start_integration_flow"
                    },
                    "data": {
                        "type": "object",
                        "description": "Form data to submit for this step"
                    }
                },
                "required": ["flow_id", "data"]
            }
        ),
        Tool(
            name="fully_kiosk_command",
            description="Control a Fully Kiosk Browser tablet via HA integration services (load URL, screenshot, screen on/off, restart, bring to foreground)",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Fully Kiosk device ID (found in HA device registry)"},
                    "command": {
                        "type": "string",
                        "enum": ["load_url", "screenshot", "screen_on", "screen_off",
                                 "restart", "bring_to_foreground", "start_screensaver",
                                 "stop_screensaver", "set_config"],
                        "description": "Command to execute"
                    },
                    "url": {"type": "string", "description": "URL to load (for load_url command)"},
                    "key": {"type": "string", "description": "Config key (for set_config)"},
                    "value": {"type": "string", "description": "Config value (for set_config)"}
                },
                "required": ["device_id", "command"]
            }
        )
    ]

def _detect_mime_type(data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if data[:2] == b'\xff\xd8':
        return "image/jpeg"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "image/webp"
    if data[:4] == b'GIF8':
        return "image/gif"
    return "image/png"  # safe default — Claude accepts PNG


def _process_image(image_data: bytes, arguments: Dict[str, Any]) -> tuple:
    """Resize/compress image and return (b64, mime_type, orig_size)."""
    import base64
    from io import BytesIO
    try:
        from PIL import Image
        max_size = int(arguments.get("max_size", 800))
        quality = int(arguments.get("quality", 60))
        quality = max(1, min(95, quality))
        img = Image.open(BytesIO(image_data))
        orig_size = f"{img.width}x{img.height}"
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        # Convert to RGB for JPEG (handles RGBA PNGs)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/jpeg", orig_size
    except ImportError:
        # No Pillow — send raw bytes with detected mime type
        mime_type = _detect_mime_type(image_data)
        b64 = base64.b64encode(image_data).decode('utf-8')
        return b64, mime_type, "unknown"


@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]):
    """Handle tool calls"""
    if not HA_TOKEN:
        return [TextContent(
            type="text",
            text=json.dumps({"error": "HA_TOKEN not configured"})
        )]
    
    try:
        async with HomeAssistantClient(HA_URL, HA_TOKEN) as client:
            if name == "get_entity_state":
                entity_id = arguments["entity_id"]
                result = await client.get_state(entity_id)
                
            elif name == "call_service":
                domain = arguments["domain"]
                service = arguments["service"]
                service_data = arguments.get("service_data", {})
                
                # Add entity_id to service_data if provided
                if "entity_id" in arguments:
                    service_data["entity_id"] = arguments["entity_id"]
                
                result = await client.call_service(domain, service, service_data)
                
            elif name == "search_entities":
                all_states = await client.get_states()
                query = arguments["query"].lower()
                domain_filter = arguments.get("domain")
                state_filter = arguments.get("state")
                
                filtered_entities = []
                for entity in all_states:
                    # Check if query matches entity_id or friendly_name
                    matches_query = (
                        query in entity["entity_id"].lower() or
                        query in entity.get("attributes", {}).get("friendly_name", "").lower()
                    )
                    
                    # Apply domain filter
                    if domain_filter and not entity["entity_id"].startswith(f"{domain_filter}."):
                        continue
                    
                    # Apply state filter
                    if state_filter and entity["state"].lower() != state_filter.lower():
                        continue
                    
                    if matches_query:
                        filtered_entities.append(entity)
                
                result = filtered_entities
                
            elif name == "get_area_entities":
                # This would require additional API calls to get area information
                # For now, return a placeholder
                result = {"message": "Area entity lookup requires additional implementation"}
                
            elif name == "fire_event":
                event_type = arguments["event_type"]
                event_data = arguments.get("event_data")
                result = await client.fire_event(event_type, event_data)
                
            elif name == "get_history":
                entity_ids = arguments["entity_ids"]
                start_time = arguments.get("start_time")
                end_time = arguments.get("end_time")
                minimal_response = arguments.get("minimal_response", False)
                no_attributes = arguments.get("no_attributes", False)
                significant_changes_only = arguments.get("significant_changes_only", False)
                
                result = await client.get_history(
                    start_time=start_time,
                    end_time=end_time,
                    filter_entity_id=",".join(entity_ids),
                    minimal_response=minimal_response,
                    no_attributes=no_attributes,
                    significant_changes_only=significant_changes_only
                )
                
            elif name == "get_logbook":
                start_time = arguments.get("start_time")
                end_time = arguments.get("end_time")
                entity = arguments.get("entity")
                result = await client.get_logbook(start_time, end_time, entity)
                
            elif name == "set_state":
                entity_id = arguments["entity_id"]
                state = arguments["state"]
                attributes = arguments.get("attributes")
                result = await client.set_state(entity_id, state, attributes)
                
            elif name == "delete_state":
                entity_id = arguments["entity_id"]
                result = await client.delete_state(entity_id)
                
            elif name == "render_template":
                template = arguments["template"]
                result = await client.render_template(template)
                
            elif name == "check_config":
                result = await client.check_config()
                
            elif name == "get_error_log":
                result = await client.get_error_log()
                
            elif name == "get_calendars":
                result = await client.get_calendars()
                
            elif name == "get_calendar_events":
                calendar_id = arguments["calendar_id"]
                start = arguments["start"]
                end = arguments["end"]
                result = await client.get_calendar_events(calendar_id, start, end)
                
            elif name == "get_camera_image":
                import base64
                from io import BytesIO
                camera_entity_id = arguments["camera_entity_id"]
                image_data = await client.get_camera_proxy(camera_entity_id)
                b64, mime_type, orig_size = _process_image(image_data, arguments)
                return [
                    ImageContent(type="image", data=b64, mimeType=mime_type),
                    TextContent(type="text", text=f"Camera image from {camera_entity_id} (original: {orig_size})"),
                ]

            elif name == "get_image_entity":
                import base64
                from io import BytesIO
                image_entity_id = arguments["image_entity_id"]
                image_data = await client.get_image_proxy(image_entity_id)
                b64, mime_type, orig_size = _process_image(image_data, arguments)
                return [
                    ImageContent(type="image", data=b64, mimeType=mime_type),
                    TextContent(type="text", text=f"Image from {image_entity_id} (original: {orig_size})"),
                ]

            elif name == "call_service_with_response":
                domain = arguments["domain"]
                service = arguments["service"]
                service_data = arguments.get("service_data", {})
                result = await client.call_service_with_response(domain, service, service_data)
                
            elif name == "handle_intent":
                intent_name = arguments["intent_name"]
                intent_data = arguments.get("intent_data")
                result = await client.handle_intent(intent_name, intent_data)
                
            elif name == "subscribe_events":
                # Handle SSE subscription
                events = set(arguments.get("events", []))
                entity_id = arguments.get("entity_id")
                domain = arguments.get("domain")
                
                try:
                    client_id = sse_manager.add_subscription(events, entity_id, domain)
                    # Start the event stream
                    await sse_manager.start_event_stream(client_id, HA_URL, HA_TOKEN)
                    
                    result = {
                        "status": "subscribed",
                        "client_id": client_id,
                        "events": list(events),
                        "entity_id": entity_id,
                        "domain": domain,
                        "message": "Successfully subscribed to Home Assistant events"
                    }
                except ValueError as e:
                    result = {"error": str(e)}
                
            elif name == "get_sse_stats":
                result = sse_manager.get_stats()
                
            elif name == "get_automations":
                result = await client.get_automations(
                    compact=arguments.get("compact", False),
                    search=arguments.get("search"),
                    state_filter=arguments.get("state"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )

            elif name == "get_automation_configs":
                result = await client.get_automation_configs(
                    search=arguments.get("search"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )

            elif name == "get_automation":
                automation_id = arguments["automation_id"]
                result = await client.get_automation(automation_id)
                
            elif name == "toggle_automation":
                automation_id = arguments["automation_id"]
                result = await client.toggle_automation(automation_id)
                
            elif name == "turn_on_automation":
                automation_id = arguments["automation_id"]
                result = await client.turn_on_automation(automation_id)
                
            elif name == "turn_off_automation":
                automation_id = arguments["automation_id"]
                result = await client.turn_off_automation(automation_id)
                
            elif name == "trigger_automation":
                automation_id = arguments["automation_id"]
                result = await client.trigger_automation(automation_id)
                
            elif name == "create_automation":
                config = arguments["config"]
                if isinstance(config, str):
                    config = json.loads(config)
                result = await client.create_automation(config)

            elif name == "update_automation":
                automation_id = arguments["automation_id"]
                config = arguments["config"]
                if isinstance(config, str):
                    config = json.loads(config)
                result = await client.update_automation(automation_id, config)
                
            elif name == "delete_automation":
                automation_id = arguments["automation_id"]
                result = await client.delete_automation(automation_id)
                
            elif name == "reload_automations":
                result = await client.reload_automations()
                
            elif name == "get_automation_trace":
                automation_id = arguments["automation_id"]
                run_id = arguments.get("run_id")
                result = await client.get_automation_trace(automation_id, run_id)
                
            # Script Management Tools
            elif name == "list_scripts":
                result = await client.get_scripts()

            elif name == "create_script":
                script_id = arguments["script_id"]
                config = arguments["config"]
                config["id"] = script_id
                result = await client.create_script(config)

            elif name == "update_script":
                script_id = arguments["script_id"]
                config = arguments["config"]
                result = await client.update_script(script_id, config)

            elif name == "delete_script":
                script_id = arguments["script_id"]
                result = await client.delete_script(script_id)

            elif name == "reload_scripts":
                result = await client.reload_scripts()

            elif name == "get_scenes":
                result = await client.get_scenes(
                    search=arguments.get("search"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )
                
            elif name == "activate_scene":
                scene_id = arguments["scene_id"]
                result = await client.activate_scene(scene_id)
                
            elif name == "create_scene":
                scene_data = arguments["scene_data"]
                result = await client.create_scene(scene_data)
                
            # Area Management Tools
            elif name == "get_areas":
                result = await client.get_areas()
                
            elif name == "create_area":
                name_arg = arguments["name"]
                aliases = arguments.get("aliases")
                result = await client.create_area(name_arg, aliases)
                
            elif name == "update_area":
                area_id = arguments["area_id"]
                name_arg = arguments["name"]
                aliases = arguments.get("aliases")
                result = await client.update_area(area_id, name_arg, aliases)
                
            elif name == "delete_area":
                area_id = arguments["area_id"]
                result = await client.delete_area(area_id)
                
            elif name == "get_entities_by_area":
                result = await client.get_entities_by_area(
                    area_id=arguments["area_id"],
                    search=arguments.get("search"),
                    domain=arguments.get("domain"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )
                
            # Device Management Tools
            elif name == "get_devices":
                result = await client.get_devices(
                    search=arguments.get("search"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )
                
            elif name == "get_device":
                device_id = arguments["device_id"]
                result = await client.get_device(device_id)
                
            elif name == "update_device":
                device_id = arguments["device_id"]
                name_arg = arguments.get("name")
                area_id = arguments.get("area_id")
                disabled_by = arguments.get("disabled_by")
                result = await client.update_device(device_id, name_arg, area_id, disabled_by)
                
            # System Management Tools
            elif name == "restart_homeassistant":
                result = await client.restart_homeassistant()
                
            elif name == "stop_homeassistant":
                result = await client.stop_homeassistant()
                
            elif name == "check_config_valid":
                result = await client.check_config_valid()
                
            elif name == "get_system_health":
                result = await client.get_system_health()
                
            elif name == "get_supervisor_info":
                result = await client.get_supervisor_info()
                
            elif name == "get_system_info":
                result = await client.get_system_info()
                
            # Integration Management Tools
            elif name == "get_integrations":
                result = await client.get_integrations(
                    search=arguments.get("search"),
                    domain=arguments.get("domain"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )
                
            elif name == "reload_integration":
                integration_domain = arguments["integration_domain"]
                result = await client.reload_integration(integration_domain)
                
            elif name == "disable_integration":
                config_entry_id = arguments["config_entry_id"]
                result = await client.disable_integration(config_entry_id)
                
            elif name == "enable_integration":
                config_entry_id = arguments["config_entry_id"]
                result = await client.enable_integration(config_entry_id)
                
            elif name == "delete_integration":
                config_entry_id = arguments["config_entry_id"]
                result = await client.delete_integration(config_entry_id)
                
            elif name == "get_integration_info":
                config_entry_id = arguments["config_entry_id"]
                result = await client.get_integration_info(config_entry_id)
                
            # Notification Services Tools
            elif name == "send_notification":
                message = arguments["message"]
                title = arguments.get("title")
                target = arguments.get("target")
                data = arguments.get("data")
                result = await client.send_notification(message, title, target, data)
                
            elif name == "get_notification_services":
                result = await client.get_notification_services()
                
            elif name == "dismiss_notification":
                notification_id = arguments["notification_id"]
                result = await client.dismiss_notification(notification_id)
                
            # Entity Registry Management Tools
            elif name == "get_entity_registry":
                result = await client.get_entity_registry(
                    search=arguments.get("search"),
                    domain=arguments.get("domain"),
                    offset=int(arguments.get("offset", 0)),
                    limit=int(arguments.get("limit", 0)),
                )
                
            elif name == "update_entity_registry":
                entity_id = arguments["entity_id"]
                name_arg = arguments.get("name")
                disabled_by = arguments.get("disabled_by")
                area_id = arguments.get("area_id")
                result = await client.update_entity_registry(entity_id, name_arg, disabled_by, area_id)
                
            elif name == "enable_entity":
                entity_id = arguments["entity_id"]
                result = await client.enable_entity(entity_id)
                
            elif name == "disable_entity":
                entity_id = arguments["entity_id"]
                result = await client.disable_entity(entity_id)

            elif name == "websocket_call":
                data = arguments.get("data", {})
                result = await client.ws_command(arguments["type"], data)

            elif name == "list_dashboards":
                try:
                    result = await client.get_dashboards()
                except RuntimeError as e:
                    result = {"error": str(e), "hint": "Try websocket_call with type='lovelace/dashboards'"}

            elif name == "get_dashboard_config":
                result = await client.get_dashboard_config(arguments.get("url_path"))

            elif name == "update_dashboard_config":
                result = await client.save_dashboard_config(
                    arguments["config"],
                    arguments.get("url_path"),
                    arguments.get("force", False)
                )

            elif name == "create_dashboard":
                result = await client.create_dashboard(
                    arguments["title"],
                    arguments["url_path"],
                    arguments.get("icon"),
                    arguments.get("show_in_sidebar", True),
                    arguments.get("require_admin", False)
                )

            elif name == "delete_dashboard":
                result = await client.delete_dashboard(arguments["dashboard_id"])

            elif name == "list_lovelace_resources":
                result = await client.get_lovelace_resources()

            elif name == "list_lovelace_cards":
                result = await client.list_lovelace_cards()

            elif name == "hacs_status":
                result = await client.hacs_status()

            elif name == "hacs_list_repositories":
                result = await client.hacs_list_repositories(arguments.get("categories"))

            elif name == "hacs_repository_info":
                result = await client.hacs_repository_info(arguments["repository_id"])

            elif name == "hacs_download":
                result = await client.hacs_download(
                    arguments["repository_id"],
                    arguments.get("version")
                )

            elif name == "hacs_remove":
                result = await client.hacs_remove(arguments["repository_id"])

            elif name == "hacs_refresh":
                result = await client.hacs_refresh(arguments["repository_id"])

            elif name == "hacs_releases":
                result = await client.hacs_releases(arguments["repository_id"])

            elif name == "restart_homeassistant":
                result = await client.restart_homeassistant()

            elif name == "list_config_entries":
                result = await client.list_config_entries(arguments.get("domain"))

            elif name == "start_integration_flow":
                result = await client.start_integration_flow(arguments["handler"])

            elif name == "get_integration_flow":
                result = await client.get_integration_flow(arguments["flow_id"])

            elif name == "submit_integration_flow":
                result = await client.submit_integration_flow(
                    arguments["flow_id"],
                    arguments["data"]
                )

            elif name == "fully_kiosk_command":
                device_id = arguments["device_id"]
                command = arguments["command"]
                service_map = {
                    "load_url": ("load_url", {"url": arguments.get("url", "")}),
                    "screenshot": ("screenshot", {}),
                    "screen_on": ("turn_screen_on", {}),
                    "screen_off": ("turn_screen_off", {}),
                    "restart": ("restart_app", {}),
                    "bring_to_foreground": ("bring_to_foreground", {}),
                    "start_screensaver": ("start_screensaver", {}),
                    "stop_screensaver": ("stop_screensaver", {}),
                    "set_config": ("set_config", {"key": arguments.get("key", ""), "value": arguments.get("value", "")}),
                }
                if command not in service_map:
                    result = {"error": f"Unknown command: {command}"}
                else:
                    svc, extra_data = service_map[command]
                    service_data = {"device_id": device_id, **extra_data}
                    result = await client.call_service("fully_kiosk", svc, service_data)

            else:
                result = {"error": f"Unknown tool: {name}"}
        
        return [TextContent(
            type="text", 
            text=json.dumps(result, indent=2)
        )]
    
    except Exception as e:
        logger.error(f"Error in tool {name}: {e}")
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]

async def main():
    """Run the MCP server"""
    # Import here to avoid issues if mcp package isn't available
    from mcp.server.stdio import stdio_server
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="homeassistant-mcp",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
