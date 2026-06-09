#!/usr/bin/env python3
"""Tests for the file-based dashboard config flow (anti-truncation / anti-confusion).

get_dashboard_config now materializes the LIVE config to a deterministic file and
returns only a short summary; update_dashboard_config_from_file reads that file back
and saves it. This removes the truncation of big Lovelace configs and the live/backup
confusion that froze the tablet refonte mission.
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import _materialize_dashboard, handle_call_tool


def test_materialize_writes_file_and_short_summary():
    cfg = {"views": [{"cards": [{"type": "a"}, {"type": "b"}]}]}
    r = _materialize_dashboard(cfg, "wallpanel-bureau")
    assert r["n_cards"] == 2, r
    assert r["path"].endswith("dash-live-wallpanel-bureau.json"), r
    assert os.path.exists(r["path"]), r
    assert "config" not in r and "views" not in r, "must NOT inline the full config"
    with open(r["path"], encoding="utf-8") as f:
        assert json.load(f) == cfg
    # deterministic: same name, overwritten
    r2 = _materialize_dashboard({"views": [{"cards": [{"type": "c"}]}]}, "wallpanel-bureau")
    assert r2["path"] == r["path"] and r2["n_cards"] == 1


def _run_get_then_save():
    with patch("server.HA_TOKEN", "tok"), patch("server.HomeAssistantClient") as MC:
        inst = AsyncMock()
        MC.return_value.__aenter__.return_value = inst
        cfg = {"views": [{"cards": [{"type": "a"}]}]}
        inst.get_dashboard_config = AsyncMock(return_value=cfg)
        inst.get_states = AsyncMock(return_value=[])  # no entity → guard passes
        inst.save_dashboard_config = AsyncMock(return_value={"ok": True})

        # get → materialize to file, summary only
        res = asyncio.get_event_loop().run_until_complete(
            handle_call_tool("get_dashboard_config", {"url_path": "wallpanel-test"}))
        data = json.loads(res[0].text)
        assert data["n_cards"] == 1, data
        path = data["path"]

        # edit the file (add a card), then save from file
        edited = {"views": [{"cards": [{"type": "a"}, {"type": "b"}]}]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(edited, f)
        res2 = asyncio.get_event_loop().run_until_complete(
            handle_call_tool("update_dashboard_config_from_file",
                             {"file_path": path, "url_path": "wallpanel-test"}))
        data2 = json.loads(res2[0].text)
        assert "error" not in data2, data2
        inst.save_dashboard_config.assert_awaited()
        saved_cfg = inst.save_dashboard_config.call_args[0][0]
        assert saved_cfg == edited, saved_cfg  # the EDITED file content was saved


def test_get_materializes_and_save_from_file_roundtrip():
    _run_get_then_save()


def test_save_from_missing_file_errors_cleanly():
    with patch("server.HA_TOKEN", "tok"), patch("server.HomeAssistantClient") as MC:
        inst = AsyncMock()
        MC.return_value.__aenter__.return_value = inst
        inst.save_dashboard_config = AsyncMock(return_value={"ok": True})
        res = asyncio.get_event_loop().run_until_complete(
            handle_call_tool("update_dashboard_config_from_file",
                             {"file_path": "/nonexistent/nope.json"}))
        data = json.loads(res[0].text)
        assert "error" in data, data
        inst.save_dashboard_config.assert_not_awaited()  # never tried to save


if __name__ == "__main__":
    test_materialize_writes_file_and_short_summary()
    test_get_materializes_and_save_from_file_roundtrip()
    test_save_from_missing_file_errors_cleanly()
    print("OK — all dashboard file-io tests passed")
