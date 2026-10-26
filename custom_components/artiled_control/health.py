# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Repairs when a room's device stops answering (S3c, #426).

⚠ THE CONTROL DECIDES WHAT IS DOWN, never Home Assistant. Its check
(check_devices.php, every 5 minutes) marks a network device down after three
missed checks in a row; its own dashboard alerts on exactly "enabled, not
Deep Standby, down" (home/index.php) - a Deep Standby device (a Magnetar)
drops off the network while off, which is normal, not a fault. That rule is
ported here from /devices.

Each issue carries its fix in the Touch's own words (dashboard.php: "Check
that the ... is switched on and on the network, or check it with Edit on
...") and a link to the Control's Devices page, and it goes away by itself
when the Control reaches the device again. A failed /devices read is not
"everything answers again": the issues stand.

down_devices and where are pure (tests load this file by path);
sync_issues touches Home Assistant's issue registry, imported inside it.
"""
from __future__ import annotations

ISSUE_PREFIX = "device_down"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def down_devices(rows) -> list[dict]:
    """The devices the Control's own alert list shows: enabled, not Deep
    Standby (deep_standby, the device page's check skip), down."""
    return [
        row for row in rows or []
        if isinstance(row, dict) and _int(row.get("down")) == 1
        and _int(row.get("disabled")) == 0 and _int(row.get("deep_standby")) == 0
    ]


def where(row: dict, names: dict, rooms: dict) -> str:
    """The device's place as Home Assistant names it: its zone's unit, its
    room's unit, else its room's own name; "" when it is in none."""
    zone, room = _int(row.get("zone")), _int(row.get("room"))
    if zone > 0 and names.get(f"zone_{zone}"):
        return names[f"zone_{zone}"]
    if room > 0:
        return names.get(f"room_{room}") or str(rooms.get(room) or "")
    return ""


def issue_id(entry_id: str, device_id) -> str:
    return f"{ISSUE_PREFIX}_{entry_id}_{_int(device_id)}"


def sync_issues(hass, entry_id: str, title: str, host: str, rows, names: dict, rooms: dict, raised: set) -> set:
    """One issue per down device; the others' issues deleted. rows None = not
    read (yet): nothing changes. Returns the issue ids now raised."""
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    from .const import DOMAIN  # noqa: PLC0415

    if rows is None:
        return raised
    url = f"http://{host}/devices/"
    now: set[str] = set()
    for row in down_devices(rows):
        iid = issue_id(entry_id, row.get("id"))
        place = where(row, names, rooms)
        ir.async_create_issue(
            hass,
            DOMAIN,
            iid,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            learn_more_url=url,
            translation_key=ISSUE_PREFIX,
            translation_placeholders={
                "device": str(row.get("name") or f"Device {row.get('id')}"),
                "where": f" in {place}" if place else "",
                "name": title,
                "url": url,
            },
        )
        now.add(iid)
    for iid in raised - now:
        ir.async_delete_issue(hass, DOMAIN, iid)
    return now


def clear_issues(hass, raised: set) -> None:
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    from .const import DOMAIN  # noqa: PLC0415

    for iid in raised:
        ir.async_delete_issue(hass, DOMAIN, iid)
