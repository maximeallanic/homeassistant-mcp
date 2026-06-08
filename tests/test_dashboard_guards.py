#!/usr/bin/env python3
"""Unit tests for the dashboard write guards (entity validation extraction).

The guard rejects a dashboard config that binds cards to entities which do not
exist in Home Assistant — preventing an agent from persisting hallucinated cards
(e.g. a dishwasher sensor for a home that has no dishwasher).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import _extract_entity_ids


def test_extracts_entity_bindings_from_nested_cards():
    config = {
        "views": [{
            "cards": [{
                "type": "vertical-stack",
                "cards": [
                    {"type": "custom:mushroom-template-card", "entity": "light.kitchen"},
                    {"type": "custom:mushroom-template-card", "entity": "sensor.dishwasher_status"},
                ],
            }],
        }],
    }
    ids = _extract_entity_ids(config)
    assert "light.kitchen" in ids
    assert "sensor.dishwasher_status" in ids


def test_handles_entities_list_form():
    config = {"type": "entities", "entities": ["cover.kitchen_blind", {"entity": "media_player.kitchen_speaker"}]}
    ids = _extract_entity_ids(config)
    assert ids == {"cover.kitchen_blind", "media_player.kitchen_speaker"}


def test_ignores_non_entity_strings_and_templates():
    # navigation paths, titles and jinja templates must NOT be treated as entities
    config = {
        "type": "custom:button-card",
        "name": "Recipes",
        "tap_action": {"action": "navigate", "navigation_path": "/recipes/0"},
        "icon": "mdi:book",
        "secondary": "{{ states('light.x') }}",  # template, under a non-entity key
    }
    assert _extract_entity_ids(config) == set()


def test_validation_separates_known_from_unknown():
    live = {"light.kitchen", "cover.kitchen_blind"}
    referenced = _extract_entity_ids({
        "cards": [
            {"entity": "light.kitchen"},
            {"entity": "sensor.dishwasher_status"},  # does not exist
        ],
    })
    unknown = sorted(e for e in referenced if e not in live)
    assert unknown == ["sensor.dishwasher_status"]


if __name__ == "__main__":
    test_extracts_entity_bindings_from_nested_cards()
    test_handles_entities_list_form()
    test_ignores_non_entity_strings_and_templates()
    test_validation_separates_known_from_unknown()
    print("OK — all dashboard guard tests passed")
