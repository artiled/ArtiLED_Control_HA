# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Diagnostics download: what the integration sees, never what signs it in.

⚠ The token is the Control's box-wide key. It is redacted from the entry, and
any token= inside a URL anywhere in the snapshot is redacted too - a Now
Playing picture of the Control's own art route is only loadable with one.
Now Playing text beyond title and artist is dropped: synopsis, lyrics and the
like are not needed to diagnose anything.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import redact
from .const import DOMAIN

TO_REDACT = {"token", "password", "username"}
NOW_PLAYING_KEPT = ("state", "title", "artist", "source", "image_url", "duration", "position", "updated")


def redact_snapshot(value: Any) -> Any:
    """Every string redacted, every Now Playing trimmed - recursively."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in TO_REDACT:
                out[key] = "**REDACTED**"
            elif key == "now_playing" and isinstance(item, dict):
                out[key] = {k: redact_snapshot(item.get(k)) for k in NOW_PLAYING_KEPT if k in item}
            else:
                out[key] = redact_snapshot(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_snapshot(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def build(entry_data: dict, data: dict | None) -> dict:
    """The diagnostics body. Pure, so tests/test_redaction.py runs it."""
    coordinator_data = data or {}
    return redact_snapshot({
        "entry": entry_data,
        "units": {
            key: {k: v for k, v in unit.items()}
            for key, unit in (coordinator_data.get("units") or {}).items()
        },
        "names": coordinator_data.get("names") or {},
        "activities": coordinator_data.get("activities") or {},
    })


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    coordinator = data.get("coordinator")
    body = build(async_redact_data(dict(entry.data), TO_REDACT), coordinator.data if coordinator else None)
    body["version"] = coordinator.version if coordinator else None
    body["api"] = str(coordinator.client.api) if coordinator else None
    return body
