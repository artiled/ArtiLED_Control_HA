#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for the six boundaries of Mycroft's S1 review 8662 (HA-S1..HA-S6).

Run:  python3 tests/test_boundaries.py     (exit 0 = all pass)

The real integration against the stand-ins in tests/ha_stubs.py. Every case
HOLDS the in-flight step (a gated /alive or /activity), injects the change
while it waits, then releases it - the in-flight case the review describes,
not a sequential stand-in for it.

⚠ WHY THIS FILE EXISTS. Each section is a way a correct-looking step could act
on a world that changed under it, or take an unreadable reply for a real one:
  HA-S1  a follow proven against an address published after a second claim,
         a reconfigure, a recovery or a removal
  HA-S2  a command queued behind another sent after the entry or the binding
         that authorised it went away
  HA-S3  a "successful" reply without its list read as an empty Control - and
         after the grace hour, customer entities deleted
  HA-S4  a hidden unit pruned because its last activity went while it was hidden
  HA-S5  remove_voice_activity leaving the scene's registry entry, and an
         assistant's exposure of it, behind
  HA-S6  a "successful" command reply without its documented shape reported as done
Each section was run against 3df1708 (before the fixes) and fails there.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
OLD, NEW, OTHER = "192.0.2.10", "192.0.2.20", "192.0.2.30"
PACKET = {"ident": S.IDENT, "type": "CTRL", "ip": "x", "name": "x"}


def section(func):
    """A crash inside a section is a failure of that section, not of the file."""
    async def run():
        try:
            await func()
        except Exception:  # noqa: BLE001
            check(f"{func.__name__} ran to the end", False, traceback.format_exc(limit=3))
    return run


async def raises(coro, words=""):
    try:
        await coro
    except S.HomeAssistantError as err:
        return words.lower() in str(err).lower()
    return False


# --------------------------------------------------------------------------
async def lost_entry(token=S.TOKEN):
    """A fresh Home Assistant (fresh tracker) whose entry cannot reach its
    Control at OLD, and our Control at NEW whose /alive is held."""
    S.load_package()
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    gone = S.house(S.FakeControl())
    gone.fail.update({"alive", "rooms", "zones"})
    hass.session.boxes[OLD] = gone
    entry = S.ConfigEntry("e1", S.IDENT, {"host": OLD, "token": S.TOKEN, "assist_all": False})
    hass.config_entries.entries["e1"] = entry
    try:
        await hass.config_entries.setup(entry)
    except S.ConfigEntryNotReady:
        pass
    moved = S.house(S.FakeControl(token=token))
    gate = asyncio.Event()
    moved.gates["alive"] = gate
    hass.session.boxes[NEW] = moved
    listener = S._LISTENERS[-1]
    listener(PACKET, NEW)
    await hass.settle()
    return hass, entry, gone, moved, gate, listener


@section
async def ha_s1():
    print("HA-S1  a follow is checked again when it is PUBLISHED")
    hass, entry, gone, moved, gate, listener = await lost_entry()
    check("control: the entry is retrying and qualification is held", entry.state == S.ConfigEntryState.SETUP_RETRY)
    gate.set()
    await hass.settle()
    check("control: nothing changed meanwhile -> followed", entry.data["host"] == NEW)
    await S.stop(hass)

    hass, entry, gone, moved, gate, listener = await lost_entry()
    hass.session.boxes[OTHER] = S.house(S.FakeControl())
    listener(PACKET, OTHER)
    await hass.settle()
    check("a second claim while qualifying raises the ambiguity issue", "ambiguous_e1" in hass.issues)
    gate.set()
    await hass.settle()
    check("...and the already-running follow does NOT publish", entry.data["host"] == OLD, entry.data["host"])
    check("...nor clear the ambiguity issue", "ambiguous_e1" in hass.issues)
    await S.stop(hass)

    hass, entry, gone, moved, gate, listener = await lost_entry()
    hass.config_entries.async_update_entry(entry, data={**entry.data, "host": "192.0.2.60"})
    gate.set()
    await hass.settle()
    check("a manual reconfigure while qualifying is not overwritten", entry.data["host"] == "192.0.2.60", entry.data["host"])
    await S.stop(hass)

    hass, entry, gone, moved, gate, listener = await lost_entry()
    gone.fail.clear()
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("the old address recovered while qualifying (entry loaded there)", entry.state == S.ConfigEntryState.LOADED)
    gate.set()
    await hass.settle()
    check("...so the follow does not move a working entry", entry.data["host"] == OLD, entry.data["host"])
    await S.stop(hass)

    hass, entry, gone, moved, gate, listener = await lost_entry()
    hass.config_entries.entries.pop("e1")
    updates = len(hass.config_entries.updates)
    gate.set()
    await hass.settle()
    check("an entry removed while qualifying is not written to", len(hass.config_entries.updates) == updates)
    await S.stop(hass)

    hass, entry, gone, moved, gate, listener = await lost_entry(token="AnotherTokenAfterAFactoryReset-0001")
    hass.session.boxes[OTHER] = S.house(S.FakeControl())
    listener(PACKET, OTHER)
    await hass.settle()
    gate.set()
    await hass.settle()
    check("the re-auth publication is fenced too: a second claim meanwhile -> no re-auth opened", entry.reauth_started == [])
    await S.stop(hass)

    hass, entry, gone, moved, gate, listener = await lost_entry(token="AnotherTokenAfterAFactoryReset-0001")
    gate.set()
    await hass.settle()
    check("control: token refused, nothing changed meanwhile -> re-auth for the new address",
          entry.reauth_started and entry.reauth_started[-1].get("follow_host") == NEW)
    await S.stop(hass)


# --------------------------------------------------------------------------
async def held(voice=None):
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    if voice:
        hass.storage["artiled_control_e1_voice"] = {"activities": voice}
    hass, entry, control = await S.setup_integration(hass=hass)
    gate = asyncio.Event()
    control.gates["activity"] = gate
    return hass, entry, control, gate


def starts(control):
    return [d["activity"] for e, d in control.requests if e == "activity"]


@section
async def ha_s2():
    print("\nHA-S2  a queued command is checked again when its turn comes")
    voice = [{"kind": "zone", "id": 11, "activity": 112, "name": "Kaleidescape in the Living Room"}]

    hass, entry, control, gate = await held(voice)
    player = hass.entity("media_player.living_room_main")
    scene = hass.entity("scene.kaleidescape_in_the_living_room")
    first = asyncio.ensure_future(player.async_turn_on())
    await hass.settle()
    queued = asyncio.ensure_future(scene.async_activate())
    await hass.settle()
    next(a for a in control.activities if a["id"] == 112)["disabled"] = 1
    await S.poll(hass, inventory=True)
    del control.gates["activity"]
    gate.set()
    await first
    refused = await raises(queued, "nothing was sent")
    check("voice start queued, then its activity disabled by an inventory round -> NOT sent", starts(control) == [110], str(starts(control)))
    check("...and the queued activation says so", refused)
    await S.stop(hass)

    hass, entry, control, gate = await held()
    player = hass.entity("media_player.living_room_main")
    first = asyncio.ensure_future(player.async_turn_on())
    await hass.settle()
    queued = asyncio.ensure_future(player.async_turn_off())
    await hass.settle()
    await hass.config_entries.async_reload("e1")
    del control.gates["activity"]
    gate.set()
    await first
    refused = await raises(queued, "nothing was sent")
    check("room Off queued, then the entry reloaded -> not sent through the old client", starts(control) == [110], str(starts(control)))
    check("...and it says so", refused)
    await S.stop(hass)

    hass, entry, control, gate = await held()
    player = hass.entity("media_player.living_room_main")
    first = asyncio.ensure_future(player.async_turn_on())
    await hass.settle()
    queued = asyncio.ensure_future(hass.services.call("artiled_control", "start_activity",
                                                      {"unit": "Living Room Main", "activity": 112}))
    await hass.settle()
    await hass.config_entries.unload(entry)
    del control.gates["activity"]
    gate.set()
    await first
    await raises(queued)
    check("start_activity queued, then the entry unloaded -> not sent", starts(control) == [110], str(starts(control)))
    await S.stop(hass)

    hass, entry, control, gate = await held()
    player = hass.entity("media_player.living_room_main")
    first = asyncio.ensure_future(player.async_turn_on())
    await hass.settle()
    queued = asyncio.ensure_future(player.async_select_source("Kaleidescape"))
    await hass.settle()
    next(a for a in control.activities if a["id"] == 112)["disabled"] = 1
    await S.poll(hass, inventory=True)
    del control.gates["activity"]
    gate.set()
    await first
    await raises(queued)
    check("select_source queued, then that activity disabled -> not sent", starts(control) == [110], str(starts(control)))
    await S.stop(hass)

    hass, entry, control, gate = await held(voice)
    player = hass.entity("media_player.living_room_main")
    scene = hass.entity("scene.kaleidescape_in_the_living_room")
    first = asyncio.ensure_future(player.async_turn_on())
    await hass.settle()
    second = asyncio.ensure_future(scene.async_activate())
    third = asyncio.ensure_future(player.async_turn_off())
    other = asyncio.ensure_future(hass.entity("media_player.secondary").async_turn_on())
    await hass.settle()
    check("control: another unit is not held up", 120 in starts(control))
    del control.gates["activity"]
    gate.set()
    await asyncio.gather(first, second, third, other)
    check("control: nothing changed -> all sent, same-unit order kept",
          [a for a in starts(control) if a != 120] == [110, 112, 111], str(starts(control)))
    await S.stop(hass)


# --------------------------------------------------------------------------
@section
async def ha_s3():
    print("\nHA-S3  a 'successful' reply without its list is not an empty Control")
    for api in ("2.00", ""):
        control = S.house(S.FakeControl(api=api))
        hass, entry, control = await S.setup_integration(control)
        before = list(hass.data["artiled_control"]["e1"]["shown_units"])
        memory = hass.data["artiled_control_memory"]["e1"]
        for label, envelope in (("data {}", {"success": 1, "error": 0, "data": {}}),
                                ("no data", {"success": 1, "error": 0}),
                                ("data null", {"success": 1, "error": 0, "data": None}),
                                ("activities null", {"success": 1, "error": 0, "data": {"activities": None}})):
            control.raw_answers["activities"] = envelope
            last = memory["last_success"]
            reloads = hass.config_entries.reloads
            await S.poll(hass, inventory=True)
            check(f"API {api or '<2.00'}, activities with {label}: nothing changes, no reload",
                  hass.data["artiled_control"]["e1"]["shown_units"] == before and hass.config_entries.reloads == reloads)
            check(f"API {api or '<2.00'}, activities with {label}: it is not a successful round", memory["last_success"] == last)
        S.age(hass, "e1", 3601)
        await S.poll(hass, inventory=True)
        await S.poll(hass, inventory=True)
        check(f"API {api or '<2.00'}: after the grace hour of malformed replies nothing is pruned",
              hass.device_registry.async_get_device({("artiled_control", f"{S.IDENT}_zone_11")}) is not None
              and "media_player.living_room_main" in hass.entity_registry.entities)
        del control.raw_answers["activities"]
        await S.stop(hass)

    hass, entry, control = await S.setup_integration()
    control.raw_answers["zones"] = {"success": 1, "error": 0, "data": {}}
    for _ in range(4):
        await S.poll(hass)
    shown = hass.data["artiled_control"]["e1"]["shown_units"]
    check("zones with no list never turns a room with zones into a room unit", "room_1" not in shown and "zone_11" in shown)
    check("...it is a failed poll (unavailable after three)", not hass.entity("media_player.living_room_main").available)
    del control.raw_answers["zones"]
    control.raw_answers["activities"] = {"success": 1, "error": 0, "data": {"activities": []}}
    await S.poll(hass)
    await S.poll(hass, inventory=True)
    check("positive control: an explicit [] IS a valid empty answer and decides",
          hass.data["artiled_control"]["e1"]["shown_units"] == [])
    await S.stop(hass)


# --------------------------------------------------------------------------
@section
async def ha_s4():
    print("\nHA-S4  a hidden unit keeps its identity, whatever its activities")
    hass, entry, control = await S.setup_integration()
    hass.entity_registry.entities["media_player.kitchen"].aliases = ["the kitchen"]
    control.rooms[1]["disabled"] = 1
    control.activities = [a for a in control.activities if a["id"] != 20]
    await S.poll(hass, inventory=True)
    check("hidden and Off-only: no live entity", hass.entity("media_player.kitchen") is None)
    S.age(hass, "e1", 3601)
    await S.poll(hass, inventory=True)
    await S.poll(hass, inventory=True)
    check("after the grace hour its device is KEPT",
          hass.device_registry.async_get_device({("artiled_control", f"{S.IDENT}_room_2")}) is not None)
    check("...and its registry entry, with the user's alias",
          getattr(hass.entity_registry.entities.get("media_player.kitchen"), "aliases", None) == ["the kitchen"])
    check("no hidden entity was created", hass.entity("media_player.kitchen") is None)
    control.rooms[1]["disabled"] = 0
    control.activities.append(S.activity(20, "Listen to Sonos", room_id=2))
    await S.poll(hass, inventory=True)
    check("control: shown again with an activity -> the same entity is back", hass.entity("media_player.kitchen") is not None)
    control.activities = [a for a in control.activities if a["id"] != 310]
    await S.poll(hass, inventory=True)
    S.age(hass, "e1", 3601)
    await S.poll(hass, inventory=True)
    await S.poll(hass, inventory=True)
    check("control: an UNhidden unit losing its last activity is still removed after the hour",
          "media_player.bedroom_main" not in hass.entity_registry.entities)
    await S.stop(hass)


# --------------------------------------------------------------------------
@section
async def ha_s5():
    print("\nHA-S5  removing a voice activity un-exposes and removes its entity")
    hass, entry, control = await S.setup_integration()
    await hass.services.call("artiled_control", "add_voice_activity", {"unit": "Kitchen", "activity": 20, "name": "Sonos time"})
    await hass.settle()
    await hass.services.call("artiled_control", "add_voice_activity", {"unit": "Secondary", "activity": 120, "name": "Roon time"})
    await hass.settle()
    for scene in ("scene.sonos_time", "scene.roon_time"):
        hass.exposure[("conversation", scene)] = True
        hass.exposure[("cloud.alexa", scene)] = True
    hass.log.clear()
    await hass.services.call("artiled_control", "remove_voice_activity", {"name": "sonos TIME"})
    await hass.settle()
    log = hass.log
    off = [i for i, e in enumerate(log) if e[0] == "expose" and e[2] == "scene.sonos_time" and e[3] is False]
    gone = [i for i, e in enumerate(log) if e == ("remove", "scene.sonos_time")]
    check("by name: the registry entry is removed", "scene.sonos_time" not in hass.entity_registry.entities)
    check("...un-exposed from Assist AND Alexa first",
          {log[i][1] for i in off} >= {"conversation", "cloud.alexa"} and gone and max(off) < gone[0], str(log))
    check("...the other voice scene, and the room's player, are untouched",
          "scene.roon_time" in hass.entity_registry.entities and "media_player.kitchen" in hass.entity_registry.entities
          and hass.exposure[("cloud.alexa", "scene.roon_time")] is True)
    hass.log.clear()
    await hass.services.call("artiled_control", "remove_voice_activity", {"unit": "Secondary", "activity": "Listen to Roon"})
    await hass.settle()
    off = [i for i, e in enumerate(hass.log) if e[0] == "expose" and e[2] == "scene.roon_time" and e[3] is False]
    gone = [i for i, e in enumerate(hass.log) if e == ("remove", "scene.roon_time")]
    check("by unit + activity: removed, un-exposed first",
          "scene.roon_time" not in hass.entity_registry.entities and off and gone and max(off) < gone[0])
    check("...the Secondary player stays", "media_player.secondary" in hass.entity_registry.entities)
    await S.stop(hass)


# --------------------------------------------------------------------------
@section
async def ha_s6():
    print("\nHA-S6  a 'successful' command reply must have its documented shape")
    hass, entry, control = await S.setup_integration()
    control.zones[0]["active_activity"] = 110
    await S.poll(hass)
    player = hass.entity("media_player.living_room_main")
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    for label, data in (("no result", {}), ("result not a string", {"result": 5}), ("data not an object", "ok")):
        control.raw_answers["button"] = {"success": 1, "error": 0, "data": data}
        coordinator.cancel_pending()
        check(f"/button with {label} -> an unknown outcome, said so", await raises(player.async_media_stop(), "could not be read"))
        check(f"/button with {label} -> no confirmation queued as if done", not coordinator._pending)
    control.raw_answers["button"] = {"success": 1, "error": 0, "data": {"zone": 11, "button": "stop", "result": ""}}
    await player.async_media_stop()
    check("control: an explicit empty result \"\" is delivered", bool(coordinator._pending))
    del control.raw_answers["button"]
    for label, data in (("no data", {}), ("another activity", {"activity": 999, "action": "start", "start": "1"}),
                        ("another action", {"activity": 110, "action": "stop"}), ("data not an object", "1")):
        control.raw_answers["activity"] = {"success": 1, "error": 0, "data": data}
        coordinator.cancel_pending()
        check(f"/activity start with {label} -> unknown outcome, said so", await raises(player.async_turn_on(), "could not be read"))
    control.raw_answers["activity"] = {"success": 1, "error": 0, "data": {"activity": 112, "action": "start"}}
    check("start_activity service: wrong id in the answer -> refused as unknown",
          await raises(hass.services.call("artiled_control", "start_activity", {"unit": "Living Room Main", "activity": 110}),
                       "could not be read"))
    control.raw_answers["activity"] = {"success": 1, "error": 0, "data": {"activity": 110, "zone": 11, "room": 1, "action": "start", "start": "accepted"}}
    await player.async_turn_on()
    check("control: the documented acceptance is accepted", True)
    control.raw_answers["activity"] = {"success": 1, "error": 0, "data": {"activity": "110", "action": "start"}}
    await player.async_turn_on()
    check("control: a pre-2.00 answer without the service's word is accepted", True)
    sends = [d for e, d in control.requests if e == "activity"]
    check("never sent again after an unknown outcome (no replay)", len(sends) == 4 + 1 + 2, str(len(sends)))
    await S.stop(hass)


async def main():
    await ha_s1()
    await ha_s2()
    await ha_s3()
    await ha_s4()
    await ha_s5()
    await ha_s6()


asyncio.run(main())
check.done("boundary")
