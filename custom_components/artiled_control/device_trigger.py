# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Device triggers per unit (S3b, #426): "When the Cinema: activity started".

activity started, activity stopped (Off), playback paused, playback resumed,
each optionally narrowed to one of the unit's activities. They listen for the
artiled_control_event the unit's media_player fires (units.unit_events), so
the automation gets the event's data: activity_id, activity_name, unit,
entity_id.

The HA 2026.10.4 scaffold's shape (script/scaffold/templates/device_trigger),
attached through Home Assistant's own event trigger.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .entity_ids import unit_unique_id
from .units import EVENT, TRIGGER_TYPES, parse_unit_key, source_list

CONF_ACTIVITY_ID = "activity_id"

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_ACTIVITY_ID): vol.Coerce(int),
    }
)


def _unit_of(hass: HomeAssistant, device_id: str) -> tuple[dict, str] | None:
    """(entry data, unit key) of the unit this device is, or None."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for data in (hass.data.get(DOMAIN) or {}).values():
        if not isinstance(data, dict) or "shown_units" not in data:
            continue
        for key in data["shown_units"]:
            parsed = parse_unit_key(key)
            if parsed and (DOMAIN, unit_unique_id(data["ident"], *parsed)) in device.identifiers:
                return data, key
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """The four triggers, on a unit's device only."""
    if _unit_of(hass, device_id) is None:
        return []
    base = {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id}
    return [{**base, CONF_TYPE: kind} for kind in TRIGGER_TYPES]


async def async_get_trigger_capabilities(hass: HomeAssistant, config: dict) -> dict[str, vol.Schema]:
    """An optional activity to narrow the trigger to: the unit's offered
    activities (its Off is what "stopped" means, never one to pick), labelled
    as the source list labels them."""
    found = _unit_of(hass, config[CONF_DEVICE_ID])
    if found is None:
        return {}
    data, key = found
    coordinator_data = data["coordinator"].data or {}
    raw = (coordinator_data.get("units", {}).get(key) or {}).get("raw") or {}
    offered = coordinator_data.get("offered", {}).get(key) or []
    choices = {activity_id: label for label, activity_id in source_list(offered, raw.get("off_activity"))}
    if not choices:
        return {}
    return {"extra_fields": vol.Schema({vol.Optional(CONF_ACTIVITY_ID): vol.In(choices)})}


async def async_attach_trigger(hass: HomeAssistant, config: dict, action, trigger_info) -> Any:
    """Home Assistant's event trigger on our event, this device and type
    (and the activity, when one was picked)."""
    event_data = {CONF_DEVICE_ID: config[CONF_DEVICE_ID], CONF_TYPE: config[CONF_TYPE]}
    if config.get(CONF_ACTIVITY_ID):
        event_data[CONF_ACTIVITY_ID] = int(config[CONF_ACTIVITY_ID])
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(hass, event_config, action, trigger_info, platform_type="device")
