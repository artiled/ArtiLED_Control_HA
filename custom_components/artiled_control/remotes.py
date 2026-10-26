# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""One `remote` per Control device (S3c, #426): the pure rules.

Michel 2026-10-26: one Home Assistant remote per Control device, the device's
/commands ids through remote.send_command, only devices of OFFERED units,
each on its own HA device named after the Control device, not exposed to
voice.

⚠ The Control API has no "devices an activity uses". Used instead: the
unit's devices as the Control's own unit page lists them - /devices
{zone|room} (#557: a zone's own devices, a room unit's room-level devices
(zone 0), plus the shared ones) - for the offered units. Disabled devices
are left out, as the webapp leaves them out (app_unit_device_rows).
"""
from __future__ import annotations


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def remote_unique_id(ident: str, device_id) -> str:
    return f"{ident}_device_{_int(device_id)}"


def unit_devices(devices, units) -> list[dict]:
    """The devices any of these units' pages shows, in /devices order."""
    zones = {_int(u["id"]) for u in units or [] if u.get("kind") == "zone"}
    rooms = {_int(u["id"]) for u in units or [] if u.get("kind") == "room"}
    out = []
    for row in devices or []:
        if not isinstance(row, dict) or _int(row.get("disabled")) == 1 or _int(row.get("id")) <= 0:
            continue
        zone, room = _int(row.get("zone")), _int(row.get("room"))
        if (zone > 0 and zone in zones) or (zone == 0 and room in rooms) or (_int(row.get("shared")) == 1 and units):
            out.append(row)
    return out


def command_id(commands, wanted) -> str | None:
    """A command by its /commands id, or by its label (case ignored)."""
    text = str(wanted or "").strip()
    for cmd in commands or []:
        if str(cmd.get("id")) == text:
            return str(cmd["id"])
    for cmd in commands or []:
        if str(cmd.get("name") or "").strip().lower() == text.lower() and text:
            return str(cmd["id"])
    return None


def power_on(row: dict | None) -> bool | None:
    """The device's power as the Control knows it (/devices "power"):
    on / off, None when it is not known."""
    power = str((row or {}).get("power") or "").lower()
    return True if power == "on" else False if power == "off" else None
