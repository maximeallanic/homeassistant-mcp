#!/usr/bin/env python3
"""
Regression test for create_automation duplicate-check.

get_automations() returns a paginated dict {"items": [...], ...}. create_automation
must read records from "items"; iterating the dict directly yields its string keys
and used to crash with "'str' object has no attribute 'get'" on any payload that
carried an id or alias (the duplicate check runs before creation).
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import HomeAssistantClient


def _make_client() -> HomeAssistantClient:
    return HomeAssistantClient("http://ha.test", "token")


async def _run() -> None:
    paginated = {
        "items": [
            {"entity_id": "automation.other", "attributes": {"id": "other", "friendly_name": "Other"}},
        ],
        "total": 1,
        "offset": 0,
        "limit": 0,
    }

    # Case 1: brand-new automation with a fresh id → no duplicate → POST create.
    client = _make_client()
    client.get_automations = AsyncMock(return_value=paginated)
    client._normalize_automation_keys = HomeAssistantClient._normalize_automation_keys
    created = {}
    client._request = AsyncMock(side_effect=lambda *a, **k: created.update(k.get("json", {})) or {"ok": True})
    result = await client.create_automation({"id": "auto_new", "alias": "New", "triggers": [], "actions": []})
    assert result == {"ok": True}, result
    assert created.get("id") == "auto_new", created

    # Case 2: existing id → must route to update, not crash.
    client = _make_client()
    paginated["items"][0]["attributes"]["id"] = "auto_dup"
    client.get_automations = AsyncMock(return_value=paginated)
    client.update_automation = AsyncMock(return_value={"updated": True})
    result = await client.create_automation({"id": "auto_dup", "alias": "Dup"})
    assert result == {"updated": True}, result
    client.update_automation.assert_awaited_once()

    print("OK: create_automation handles paginated get_automations() result")


def test_create_automation_handles_paginated_result() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_create_automation_handles_paginated_result()
