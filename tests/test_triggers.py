#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for S3b: triggers for automations (#426, Michel 2026-10-26).

Run:  python3 tests/test_triggers.py     (exit 0 = all pass)

Per unit: activity started, activity stopped (Off), playback paused, playback
resumed - each with the activity's id and name - as an `artiled_control_event`
on Home Assistant's bus and as device triggers ("When the Cinema: activity
started"), so "film starts in the Cinema -> curtains closed, lights dim;
pause -> lights 30 %" works in an automation.

⚠ WHY THIS FILE EXISTS.
- Started is the Control PUBLISHING an activity (active_activity), not the
  start request: a start in progress is not "started", and the Off activity
  becoming active is "stopped", never "started" (the webapp's own on/off rule,
  the 2026-09-07 CinemaDream bug).
- Switching from one activity to another starts the new one; it is not a stop
  (stopped = the room went Off).
- Paused and resumed only within one running activity: a new activity that
  happens to be paused is "started", not "paused".
- Nothing fires from what HA did not see change: not at the first poll, not
  from a failed poll.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
triggers = S.load_pure("units")

ROWS = [S.activity(110, "Watch Netflix", zone_id=11), S.activity(112, "Kaleidescape", zone_id=11),
        S.activity(111, "Off", zone_id=11)]


def raw(active, starting=0, state=None, off=111, name=""):
    return {"active_activity": active, "starting_activity": starting, "off_activity": off, "on_activity": 110,
            "activity_name": name, "now_playing": {"state": state, "title": "T"} if state else None}


def events(prev, new):
    return [(e["type"], e["activity_id"], e["activity_name"]) for e in triggers.unit_events(prev, new, ROWS)]


def pure() -> None:
    print("pure rules (units.unit_events)")
    check("the first look fires nothing", events(None, raw(110)) == [])
    check("Off -> Netflix, the start not seen between two polls: starting, then started",
          events(raw(111), raw(110)) == [("activity_starting", 110, "Watch Netflix"), ("activity_started", 110, "Watch Netflix")])
    check("nothing -> Netflix: starting, started",
          events(raw(0), raw(110)) == [("activity_starting", 110, "Watch Netflix"), ("activity_started", 110, "Watch Netflix")])
    check("Netflix -> Off: activity stopped, naming the activity that stopped",
          events(raw(110), raw(111)) == [("activity_stopped", 110, "Watch Netflix")])
    check("Netflix -> nothing: stopped", events(raw(110), raw(0)) == [("activity_stopped", 110, "Watch Netflix")])
    check("Netflix -> Kaleidescape: starting + started, not stopped",
          events(raw(110), raw(112)) == [("activity_starting", 112, "Kaleidescape"), ("activity_started", 112, "Kaleidescape")])
    check("Off -> nothing, nothing -> Off: no event", events(raw(111), raw(0)) == [] and events(raw(0), raw(111)) == [])
    check("a start in progress: activity STARTING (Michel 2026-10-26), not started",
          events(raw(111), raw(111, starting=110)) == [("activity_starting", 110, "Watch Netflix")])
    check("...still starting at the next poll: nothing more", events(raw(111, starting=110), raw(111, starting=110)) == [])
    check("...started when the Control publishes it (starting not repeated)",
          events(raw(111, starting=110), raw(110)) == [("activity_started", 110, "Watch Netflix")])
    check("the Off activity starting is not 'activity starting'", events(raw(110), raw(110, starting=111)) == [])
    check("a start of another activity while one runs: starting",
          events(raw(110), raw(110, starting=112)) == [("activity_starting", 112, "Kaleidescape")])
    check("playing -> paused: paused, with the running activity",
          events(raw(112, state="playing"), raw(112, state="paused")) == [("paused", 112, "Kaleidescape")])
    check("paused -> playing: resumed", events(raw(112, state="paused"), raw(112, state="playing")) == [("resumed", 112, "Kaleidescape")])
    check("on -> playing (playback begins) is not resumed", events(raw(112), raw(112, state="playing")) == [])
    check("a new activity that is paused is started, not paused",
          events(raw(110, state="playing"), raw(112, state="paused")) ==
          [("activity_starting", 112, "Kaleidescape"), ("activity_started", 112, "Kaleidescape")])
    check("paused while another activity starts: only its starting",
          events(raw(112, state="playing"), raw(112, starting=110, state="paused")) == [("activity_starting", 110, "Watch Netflix")])
    check("an activity not in the rows is named from the Control's own name",
          events(raw(111), raw(999, name="Party"))[-1] == ("activity_started", 999, "Party"))
    check("the five types", set(triggers.TRIGGER_TYPES) == {"activity_starting", "activity_started", "activity_stopped", "paused", "resumed"})


async def runtime() -> None:
    hass, entry, control = await S.setup_integration()
    device_trigger = __import__("artiled_control.device_trigger", fromlist=["x"])
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    main = hass.entity("media_player.living_room_main")
    device = hass.device_registry.async_get_device(identifiers={("artiled_control", main.unique_id)})

    print("\nthe bus event")
    hass.bus.fired.clear()
    control.zones[0].update(active_activity=110)
    await coordinator.async_refresh()
    fired = [d for t, d in hass.bus.fired if t == "artiled_control_event"]
    check("starting and started (the start fell between two polls)",
          [d["type"] for d in fired] == ["activity_starting", "activity_started"], str(fired))
    ev = fired[-1] if fired else {}
    check("...with the unit's device, entity, type, activity id and name",
          ev.get("device_id") == device.id and ev.get("entity_id") == "media_player.living_room_main"
          and ev.get("type") == "activity_started" and ev.get("activity_id") == 110
          and ev.get("activity_name") == "Watch Netflix", str(ev))
    hass.bus.fired.clear()
    await coordinator.async_refresh()
    check("an unchanged poll fires nothing", hass.bus.fired == [])
    control.zones[0].update(active_activity=111)
    control.fail.add("zones")
    await coordinator.async_refresh()
    check("a failed poll fires nothing", hass.bus.fired == [])
    control.fail.discard("zones")
    await coordinator.async_refresh()
    check("the change, once it is read", [d["type"] for t, d in hass.bus.fired] == ["activity_stopped"])

    print("\ndevice triggers")
    listed = await device_trigger.async_get_triggers(hass, device.id)
    check("the unit's device offers the five triggers",
          sorted(t["type"] for t in listed) == ["activity_started", "activity_starting", "activity_stopped", "paused", "resumed"])
    check("...as device triggers of this domain",
          all(t["platform"] == "device" and t["domain"] == "artiled_control" and t["device_id"] == device.id for t in listed))
    check("a device that is not a unit offers none", await device_trigger.async_get_triggers(hass, "nope") == [])
    caps = await device_trigger.async_get_trigger_capabilities(
        hass, {"platform": "device", "domain": "artiled_control", "device_id": device.id, "type": "activity_started"})
    choices = caps["extra_fields"].schema
    options = next(iter(choices.values()))
    check("an optional activity to narrow it to: the unit's activities, Off left out",
          getattr(options, "container", None) == {110: "Watch Netflix", 112: "Kaleidescape", 113: "Watch Netflix (2)"},
          str(getattr(options, "container", None)))

    calls = []
    remove = await device_trigger.async_attach_trigger(
        hass, {"platform": "device", "domain": "artiled_control", "device_id": device.id, "type": "activity_started",
               "activity_id": 112},
        lambda variables, context=None: calls.append(variables), {"trigger_data": {}, "variables": {}})
    control.zones[0].update(active_activity=110)
    await coordinator.async_refresh()
    check("narrowed to Kaleidescape: Netflix starting does not run it", calls == [])
    control.zones[0].update(active_activity=112)
    await coordinator.async_refresh()
    check("...Kaleidescape starting does", len(calls) == 1)
    check("...and the automation gets the event's data",
          calls and calls[0]["trigger"]["event"]["data"]["activity_name"] == "Kaleidescape")
    remove()
    any_calls = []
    await device_trigger.async_attach_trigger(
        hass, {"platform": "device", "domain": "artiled_control", "device_id": device.id, "type": "paused"},
        lambda variables, context=None: any_calls.append(variables), {"trigger_data": {}, "variables": {}})
    control.zones[0].update(now_playing={"state": "playing", "title": "Alien"})
    await coordinator.async_refresh()
    control.zones[0].update(now_playing={"state": "paused", "title": "Alien"})
    await coordinator.async_refresh()
    check("paused, without an activity named: fires", len(any_calls) == 1)

    print("\nthe trigger names are in the strings, and the translation is the same")
    import json  # noqa: PLC0415
    strings = json.loads((S.PKG_DIR / "strings.json").read_text())
    en = json.loads((S.PKG_DIR / "translations" / "en.json").read_text())
    names = strings.get("device_automation", {}).get("trigger_type", {})
    check("every trigger type has a name", set(names) == set(triggers.TRIGGER_TYPES), str(names))
    check("the optional activity field has a name",
          "activity_id" in strings.get("device_automation", {}).get("extra_fields", {}))
    check("en.json = strings.json", strings == en)

    await S.stop(hass)


async def main() -> None:
    pure()
    await runtime()


asyncio.run(main())
check.done("trigger")
