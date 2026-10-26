#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for S3c-3: Repairs when a room's device stops answering (#426).

Run:  python3 tests/test_health.py     (exit 0 = all pass)

⚠ WHY THIS FILE EXISTS. The Control decides what is down, never Home
Assistant: check_devices.php marks a network device down after THREE missed
checks in a row (every 5 minutes), and the Control's own dashboard alerts on
exactly "enabled, not Deep Standby, down" (home/index.php). That is ported,
not reinvented. Each issue comes with its fix (Michel:
feedback_problem_comes_with_a_solution) in the Touch's own words ("Check
that the ... is switched on and on the network"), and goes away by itself
when the Control reaches the device again. A failed /devices read is not "all
answer again": the issues stand.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
health = S.load_pure("health")


def dev(did, name, room=0, zone=0, down=0, disabled=0, deep=0, **extra):
    row = {"id": did, "name": name, "type": 1, "type_name": "Projector", "brand": "Sony", "ip": "192.0.2.50",
           "mac": "", "room": room, "zone": zone, "disabled": disabled, "down": down, "deep_standby": deep,
           "fail_count": 3 if down else 0, "sort": did, "icon": "fa-tv"}
    row.update(extra)
    return row


def pure() -> None:
    print("pure rules (health.py)")
    rows = [dev(1, "Projector", room=1, zone=11, down=1), dev(2, "AVR", room=2, down=0),
            dev(3, "Magnetar", room=1, zone=11, down=1, deep=1), dev(4, "Old TV", room=3, down=1, disabled=1),
            dev(5, "Amp", room=2, down=1)]
    down = health.down_devices(rows)
    check("down = enabled, not Deep Standby, down (the Control's own alert rule)",
          [d["id"] for d in down] == [1, 5], str(down))
    check("an older Control without deep_standby: down is down", [d["id"] for d in health.down_devices([{"id": 9, "name": "X", "down": 1}])] == [9])
    names = {"zone_11": "Living Room Main", "room_2": "Kitchen"}
    rooms = {1: "Living Room", 2: "Kitchen", 3: "Bedroom"}
    check("where: the zone's unit name", health.where(rows[0], names, rooms) == "Living Room Main")
    check("where: a room's own device", health.where(rows[4], names, rooms) == "Kitchen")
    check("where: a room with zones, device on the room", health.where(dev(6, "X", room=1), names, rooms) == "Living Room")
    check("where: nowhere", health.where(dev(7, "X"), names, rooms) == "")


async def runtime() -> None:
    control = S.house(S.FakeControl())
    control.devices = [dev(1, "Projector", room=1, zone=11, down=1), dev(2, "AVR", room=2)]
    hass, entry, control = await S.setup_integration(control)
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]

    print("\nan issue per device the Control says is down, with the fix")
    issue_id = "device_down_e1_1"
    check("the inventory round read /devices", ("devices", {}) in control.requests)
    issue = hass.issues.get(issue_id)
    check("one issue for the Projector", issue is not None and list(k for k in hass.issues if k.startswith("device_down")) == [issue_id],
          str(list(hass.issues)))
    if issue:
        check("a warning, not fixable from Home Assistant",
              issue.get("is_fixable") is False and str(issue.get("severity")).lower().endswith("warning"))
        ph = issue.get("translation_placeholders") or {}
        check("translation device_down, naming the device, its place and the Control",
              issue.get("translation_key") == "device_down" and ph.get("device") == "Projector"
              and ph.get("where") == " in Living Room Main" and ph.get("name") == "Cinema Control", str(ph))
        check("the fix links the Control's own Devices page", ph.get("url") == "http://192.0.2.10/devices/"
              and issue.get("learn_more_url") == "http://192.0.2.10/devices/")
    import json  # noqa: PLC0415
    strings = json.loads((S.PKG_DIR / "strings.json").read_text())
    text = strings.get("issues", {}).get("device_down", {})
    check("strings: a title and a description with the fix",
          "{device}" in text.get("title", "") and "switched on and on the network" in text.get("description", "")
          and "{url}" in text.get("description", ""))
    check("strings = en.json", strings == json.loads((S.PKG_DIR / "translations" / "en.json").read_text()))

    print("\nit goes away by itself; a failed read changes nothing")
    control.fail.add("devices")
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    check("a failed /devices read: the issue stands", issue_id in hass.issues)
    control.fail.discard("devices")
    control.devices[0]["down"] = 0
    control.devices[1]["down"] = 1
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    check("answering again: its issue is gone", issue_id not in hass.issues)
    check("the AVR down now: its issue, in the Kitchen",
          (hass.issues.get("device_down_e1_2") or {}).get("translation_placeholders", {}).get("where") == " in Kitchen")
    control.devices[1]["disabled"] = 1
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    check("a device switched off in the Control (disabled): no issue", "device_down_e1_2" not in hass.issues)
    control.devices[1].update(disabled=0, deep_standby=1)
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    check("Deep Standby (off the network while off is normal): no issue", "device_down_e1_2" not in hass.issues)
    control.devices[1].update(deep_standby=0)
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    check("down again: back", "device_down_e1_2" in hass.issues)

    print("\nnot re-read on every 5-second poll")
    control.requests.clear()
    await coordinator.async_refresh()
    check("a state poll does not read /devices", ("devices", {}) not in control.requests)

    print("\nremoving the integration removes its issues")
    await hass.config_entries.unload(entry)
    await sys.modules["artiled_control"].async_remove_entry(hass, entry)
    await hass.settle()
    check("no device issue left", not [k for k in hass.issues if k.startswith("device_down_e1")], str(list(hass.issues)))
    await S.stop(hass)


async def main() -> None:
    pure()
    await runtime()


asyncio.run(main())
check.done("device health")
