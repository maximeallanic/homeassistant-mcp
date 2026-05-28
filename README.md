# Home Assistant MCP Server

A comprehensive [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for Home Assistant. Gives Claude agents (and any MCP-compatible AI client) full programmatic access to your Home Assistant instance via REST API and WebSocket — 70+ tools across entity management, automations, scripts, scenes, Lovelace dashboards, HACS, Fully Kiosk tablets, and more.

## Features

- **REST + WebSocket** — entity states, services, history, logbook, configs, entity registry, device registry
- **Automation & Script management** — full CRUD (create, read, update, delete, trigger, trace)
- **Lovelace dashboards** — list, get, create, update, delete dashboards; list cards and resources
- **HACS** — status, list/install/remove/refresh repositories, get releases
- **Fully Kiosk Browser tablets** — load URL, screenshot, screen on/off, restart, set config
- **Camera** — get images (auto-resized, base64 JPEG, Pillow-optimized)
- **Real-time events** — SSE-based event subscriptions with entity/domain filtering
- **System management** — restart/stop HA, validate config, system health, supervisor info
- **Integration management** — list, enable, disable, delete, reload integrations; run config flows

## Requirements

- Python 3.11+
- Home Assistant instance (local or remote, HTTP or HTTPS)
- A Home Assistant [long-lived access token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token)

## Installation

```bash
git clone https://github.com/maximeallanic/homeassistant-mcp.git
cd homeassistant-mcp
./setup.sh
```

`setup.sh` creates a `venv/` virtual environment and installs dependencies from `requirements.txt`.

## Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```env
HA_URL=http://homeassistant.local:8123   # or https://your-ha-domain.com
HA_TOKEN=your_long_lived_access_token_here
```

**How to get a token:** Home Assistant → Profile → Long-Lived Access Tokens → Create Token.

## Usage

### Start the server

```bash
./start_server.sh
```

Or directly:

```bash
export HA_URL=http://homeassistant.local:8123
export HA_TOKEN=your_token
python server.py
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "homeassistant": {
      "command": "/path/to/homeassistant-mcp/start_server.sh"
    }
  }
}
```

### Personas Studio (Claude Agent SDK)

Register in your agent's MCP list using `start_mcp.sh` (loads credentials from `.env` and activates the `.venv`):

```json
{
  "name": "ha-history",
  "command": "/path/to/homeassistant-mcp/start_mcp.sh",
  "args": []
}
```

The MCP is used internally by the `home-manager` agent with tools prefixed `mcp__ha-history__*`.

## Available Tools

### Entity State Management

| Tool | Description |
|------|-------------|
| `get_entity_state` | Get current state of an entity |
| `search_entities` | Search entities by name, domain, or state |
| `set_state` | Set/update entity state (virtual state, not device control) |
| `delete_state` | Delete an entity state |
| `get_entity_registry` | Get full entity registry |
| `update_entity_registry` | Update entity name, area, enabled/disabled |
| `enable_entity` / `disable_entity` | Enable or disable an entity |
| `get_area_entities` | Get entities in a given area |

### Service Calls

| Tool | Description |
|------|-------------|
| `call_service` | Call any HA service (`domain`, `service`, `entity_id`, `service_data`) |
| `call_service_with_response` | Call service and return response data (e.g. weather forecasts) |
| `fire_event` | Fire a custom HA event |
| `handle_intent` | Handle a HA intent (SetTimer, GetWeather, etc.) |
| `render_template` | Render a Jinja2 template |

### Automations

| Tool | Description |
|------|-------------|
| `get_automations` | List all automations |
| `get_automation` | Get automation details |
| `create_automation` | Create automation from config |
| `update_automation` | Update existing automation |
| `delete_automation` | Delete automation |
| `toggle_automation` / `turn_on_automation` / `turn_off_automation` | Control automation state |
| `trigger_automation` | Manually trigger an automation |
| `reload_automations` | Reload automations from config |
| `get_automation_trace` | Get execution trace for debugging |

### Scripts

| Tool | Description |
|------|-------------|
| `list_scripts` | List all scripts |
| `create_script` | Create a new script |
| `update_script` | Update an existing script |
| `delete_script` | Delete a script |
| `reload_scripts` | Reload all scripts |

### Scenes

| Tool | Description |
|------|-------------|
| `get_scenes` | List all scenes |
| `activate_scene` | Activate a scene |
| `create_scene` | Create scene from current entity states |

### Areas & Devices

| Tool | Description |
|------|-------------|
| `get_areas` | List all areas |
| `create_area` / `update_area` / `delete_area` | Manage areas |
| `get_entities_by_area` | Get entities in an area |
| `get_devices` | List all devices |
| `get_device` | Get device details |
| `update_device` | Rename device or assign to area |

### Lovelace Dashboards

| Tool | Description |
|------|-------------|
| `list_dashboards` | List all dashboards |
| `get_dashboard_config` | Get full dashboard YAML config |
| `update_dashboard_config` | Save dashboard config |
| `create_dashboard` | Create new dashboard |
| `delete_dashboard` | Delete a dashboard |
| `list_lovelace_resources` | List registered JS resources |
| `list_lovelace_cards` | List all available card types (built-in + HACS) |

### HACS

| Tool | Description |
|------|-------------|
| `hacs_status` | Get HACS status |
| `hacs_list_repositories` | List repos (filter by category) |
| `hacs_repository_info` | Get repo details |
| `hacs_download` | Install/update a repo |
| `hacs_remove` | Uninstall a repo |
| `hacs_refresh` | Refresh repo metadata |
| `hacs_releases` | List available versions |

### System & Integrations

| Tool | Description |
|------|-------------|
| `get_system_health` | System health status |
| `get_system_info` | Host/OS info |
| `get_supervisor_info` | Supervisor info (HA OS only) |
| `check_config` / `check_config_valid` | Validate HA configuration |
| `restart_homeassistant` | Graceful HA restart |
| `stop_homeassistant` | Stop HA |
| `get_integrations` / `list_config_entries` | List integrations |
| `reload_integration` | Reload an integration |
| `enable_integration` / `disable_integration` / `delete_integration` | Manage integrations |
| `start_integration_flow` / `get_integration_flow` / `submit_integration_flow` | Run config flows |
| `get_integration_info` | Get integration details |

### Notifications

| Tool | Description |
|------|-------------|
| `send_notification` | Send via any notify service or persistent notification |
| `get_notification_services` | List available notification targets |
| `dismiss_notification` | Dismiss a persistent notification |

### Data & History

| Tool | Description |
|------|-------------|
| `get_history` | Historical entity states with filtering |
| `get_logbook` | Logbook events |
| `get_error_log` | HA error log |
| `get_calendars` | List calendar entities |
| `get_calendar_events` | Get events from a calendar |
| `get_camera_image` | Get camera snapshot (resized JPEG, base64) |

### Real-time Events

| Tool | Description |
|------|-------------|
| `subscribe_events` | Subscribe to HA events via SSE (filter by type/entity/domain) |
| `get_sse_stats` | Get current SSE subscription stats |

### WebSocket & Kiosk

| Tool | Description |
|------|-------------|
| `websocket_call` | Generic WebSocket command (entity icons, subscribe_trigger, etc.) |
| `fully_kiosk_command` | Control Fully Kiosk Browser tablets (load_url, screenshot, screen_on/off, restart) |

## MCP Resources

The server also exposes MCP resources readable by the client:

- `homeassistant://states` — all entity states
- `homeassistant://config` — HA system configuration
- `homeassistant://services` — all available services
- `homeassistant://events` — available event types

## Usage Examples

### Turn on a light

```json
{
  "tool": "call_service",
  "arguments": {
    "domain": "light",
    "service": "turn_on",
    "entity_id": "light.living_room",
    "service_data": { "brightness": 200, "color_temp": 3000 }
  }
}
```

### Get temperature sensor history

```json
{
  "tool": "get_history",
  "arguments": {
    "entity_ids": ["sensor.living_room_temperature"],
    "start_time": "2024-01-01T00:00:00Z",
    "minimal_response": true
  }
}
```

### Send a mobile notification

```json
{
  "tool": "send_notification",
  "arguments": {
    "message": "Front door opened",
    "title": "Security Alert",
    "target": "mobile_app_myphone"
  }
}
```

### Control a Fully Kiosk tablet

```json
{
  "tool": "fully_kiosk_command",
  "arguments": {
    "device_id": "9bdddc78adfa5d4fd74b98b735dc112f",
    "command": "load_url",
    "url": "http://homeassistant.local:8123/dashboard-kiosk/default"
  }
}
```

### Generic WebSocket command (set entity icon)

```json
{
  "tool": "websocket_call",
  "arguments": {
    "type": "entity_registry/update",
    "data": {
      "entity_id": "light.living_room",
      "icon": "mdi:ceiling-light"
    }
  }
}
```

## Testing

```bash
# Basic connectivity test (requires HA_TOKEN)
python tests/test_connection.py

# Tool schema validation (no HA connection needed)
python tests/test_mcp_tools.py

# Tool handler tests with mocks (no HA connection needed)
python tests/test_tool_handlers.py

# Live integration tests (requires HA_TOKEN)
python tests/test_new_features.py
```

## Security

- Never commit your `.env` file (it is in `.gitignore`)
- Use a dedicated long-lived access token with minimal required permissions
- If exposing HA externally, use HTTPS and ensure your token is kept secret
- Prefer internal network access (`http://homeassistant.local:8123`) over external URLs when possible

## Architecture

```
server.py                    # Main MCP server (70+ tools, REST + WebSocket)
  HomeAssistantClient        # Async HTTP + WebSocket client
  SSEManager                 # Real-time event subscription manager
  MCP tool handlers          # One handler per tool, all async

start_mcp.sh                 # Entry point for Personas Studio (uses .venv)
start_server.sh              # Entry point for other clients (uses venv/)
setup.sh                     # One-time setup (creates venv, installs deps)
mcp_config.json              # MCP server config for Warp terminal
requirements.txt             # Python dependencies (aiohttp, mcp, Pillow)
tests/                       # Test suite (connection, schemas, handlers, integration)
```

## License

MIT — see [LICENSE](LICENSE)
