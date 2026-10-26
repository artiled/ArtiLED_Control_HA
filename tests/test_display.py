#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for the room display rules (plan section 1.7), row by row, through the
REAL media_player and voice scene entities and a recording fake Control.

Run:  python3 tests/test_display.py     (exit 0 = all pass)

⚠ WHY THIS FILE EXISTS. Activities are START-ONLY (Michel 2026-10-25). The
only Off is the room's media_player, which always runs the room's Off
activity; a voice scene starts exactly one activity and never an Off; a voice
choice whose activity moved, was disabled, was deleted or became the Off
activity refuses and sends nothing - it never re-binds itself. And two of Home
Assistant's own commands for one room go out in order, without the ordering
lock being held through the confirmation that follows.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
A, B, OFF = 110, 112, 111


async def main():
    control = S.house(S.FakeControl())
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    hass.storage["artiled_control_e1_voice"] = {"activities": [
        {"kind": "zone", "id": 11, "activity": A, "name": "Netflix in the Living Room"},
        {"kind": "zone", "id": 11, "activity": B, "name": "Kaleidescape in the Living Room"},
    ]}
    hass, entry, control = await S.setup_integration(control, hass=hass)
    zone = control.zones[0]

    async def poll(inventory=False):
        """One poll of the CURRENT coordinator. An inventory change reloads the
        entry, as in Home Assistant, so entities are looked up again after."""
        coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
        if inventory:
            coordinator.inventory_due = True
        await coordinator.async_refresh()
        await hass.settle()

    def current():
        data = hass.data["artiled_control"]["e1"]
        return (data, data["coordinator"], hass.entity("media_player.living_room_main"),
                hass.entity("scene.netflix_in_the_living_room"), hass.entity("scene.kaleidescape_in_the_living_room"))

    data, coordinator, player, netflix, kscape = current()

    async def show(**fields):
        zone.update(fields)
        await poll()
        attrs = player.extra_state_attributes
        return str(player.state.value), player.source, attrs["starting"], attrs["starting_name"]

    print("the section 1.7 table, row by row")
    np = {"state": "playing", "title": "Dune", "artist": "", "image_url": "http://192.0.2.10/media/x.jpg", "updated": 1.0, "position": 5, "duration": 100}
    row = await show(active_activity=A, starting_activity=0, now_playing=np)
    check("stable A playing -> PLAYING, source A", row == ("playing", "Watch Netflix", False, ""), str(row))
    row = await show(active_activity=A, starting_activity=0, now_playing={**np, "state": "paused"})
    check("stable A paused -> PAUSED", row[0] == "paused")
    row = await show(active_activity=A, starting_activity=0, now_playing={**np, "state": "stopped"})
    check("stable A stopped -> ON", row[0] == "on")
    row = await show(active_activity=A, starting_activity=B, starting_name="Kaleidescape", now_playing=np)
    check("P=A, starting B -> ON, source B, starting", row == ("on", "Kaleidescape", True, "Kaleidescape"), str(row))
    check("...the old Now Playing is suppressed: no title, no picture",
          player.media_title is None and player.media_image_url is None)
    row = await show(active_activity=A, starting_activity=OFF, starting_name="Off", now_playing=np)
    check("P=A, starting Off -> OFF, no source, starting Off", row == ("off", None, True, "Off"), str(row))
    row = await show(active_activity=OFF, starting_activity=A, starting_name="Watch Netflix", now_playing=None)
    check("P=O, starting A -> ON, source A, starting", row == ("on", "Watch Netflix", True, "Watch Netflix"), str(row))
    row = await show(active_activity=0, starting_activity=A, starting_name="Watch Netflix")
    check("P=0, starting A -> ON", row[0] == "on" and row[2])
    row = await show(active_activity=OFF, starting_activity=0, starting_name="")
    check("stable Off -> OFF, no source", row == ("off", None, False, ""))
    row = await show(active_activity=0, starting_activity=0)
    check("P=0 -> OFF", row[0] == "off")

    print("\nthree failed polls -> UNAVAILABLE, retained through the first two")
    await show(active_activity=A, now_playing=np)
    control.fail.add("rooms")
    await coordinator.async_refresh()
    check("1st failure: still available, last state kept", player.available and str(player.state.value) == "playing")
    await coordinator.async_refresh()
    check("2nd failure: still available", player.available)
    await coordinator.async_refresh()
    check("3rd failure: unavailable", not player.available)
    control.fail.clear()
    await coordinator.async_refresh()
    check("back: available again", player.available)

    print("\nturn_off ALWAYS sends /activity start O, whatever runs")
    for active, starting in ((A, 0), (OFF, 0), (A, B), (0, 0)):
        zone.update(active_activity=active, starting_activity=starting)
        await coordinator.async_refresh()
        control.requests.clear()
        await player.async_turn_off()
        check(f"P={active}, S={starting}: exactly one start of the Off activity",
              control.requests == [("activity", {"action": "start", "activity": OFF})], str(control.requests))

    print("\nvoice scenes: exactly one start, never an Off")
    netflix = hass.entity("scene.netflix_in_the_living_room")
    kscape = hass.entity("scene.kaleidescape_in_the_living_room")
    check("both stored choices are entities", netflix is not None and kscape is not None)
    check("named as the user said it, not prefixed by the device", netflix.name == "Netflix in the Living Room")
    zone.update(active_activity=A, starting_activity=0)
    await coordinator.async_refresh()
    control.requests.clear()
    await netflix.async_activate()
    check("activating sends exactly one /activity start A - even with A already running",
          control.requests == [("activity", {"action": "start", "activity": A})], str(control.requests))
    check("no Off and no stop anywhere", all(d.get("activity") != OFF and d.get("action") != "stop" for _, d in control.requests))

    print("\na choice whose activity is gone or changed refuses, nothing sent")

    async def refused(name):
        entity = hass.entity(name)
        before = len(control.requests)
        try:
            await entity.async_activate()
        except S.HomeAssistantError:
            return not entity.available and len(control.requests) == before
        return False

    K = "scene.kaleidescape_in_the_living_room"
    saved = [dict(a) for a in control.activities]
    next(a for a in control.activities if a["id"] == B)["disabled"] = 1
    await poll(inventory=True)
    check("activity disabled -> unavailable and refused", await refused(K))
    check("the other choice is unaffected", hass.entity("scene.netflix_in_the_living_room").available)
    control.activities = [dict(a) for a in saved]
    next(a for a in control.activities if a["id"] == B).update(zone_id=12)
    await poll(inventory=True)
    check("activity MOVED to another zone -> refused, never re-bound", await refused(K))
    control.activities = [dict(a) for a in saved if a["id"] != B]
    await poll(inventory=True)
    check("activity deleted -> refused", await refused(K))
    control.activities = [dict(a) for a in saved]
    zone.update(off_activity=B)
    await poll(inventory=True)
    check("activity became the unit's Off -> refused", await refused(K))
    zone.update(off_activity=OFF)
    await poll(inventory=True)
    check("restored on the Control -> available again", hass.entity(K).available)
    check("the voice choice survived all of it (nothing pruned within the hour)",
          len(hass.storage["artiled_control_e1_voice"]["activities"]) == 2)
    data, coordinator, player, netflix, kscape = current()

    print("\nordering: one unit's commands go out in order, the lock is not held through confirmation")
    gate = asyncio.Event()
    control.gates["activity"] = gate
    control.requests.clear()
    first = asyncio.ensure_future(player.async_turn_on())
    second = asyncio.ensure_future(player.async_select_source("Kaleidescape"))
    await hass.settle()
    starts = [d for e, d in control.requests if e == "activity"]
    check("while the first is in flight, the second waits", len(starts) == 1 and starts[0]["activity"] == A, str(starts))
    del control.gates["activity"]
    gate.set()
    await first
    await second
    starts = [d["activity"] for e, d in control.requests if e == "activity"]
    check("both sent, in order", starts == [A, B], str(starts))
    lock = data["locks"]["zone_11"]
    check("the lock is free while the confirmations are still pending", not lock.locked() and coordinator._pending)
    check("nothing was replayed", len(starts) == 2)
    other = hass.entity("media_player.secondary")
    gate2 = asyncio.Event()
    control.gates["activity"] = gate2
    control.requests.clear()
    blocked = asyncio.ensure_future(player.async_turn_on())
    await hass.settle()
    free = asyncio.ensure_future(other.async_turn_on())
    await hass.settle()
    check("another unit is NOT held up by this unit's command",
          [d["activity"] for e, d in control.requests if e == "activity"] == [A, 120])
    gate2.set()
    del control.gates["activity"]
    await blocked
    await free

    print("\npolls are never queued behind a slow command")
    gate3 = asyncio.Event()
    control.gates["button"] = gate3
    slow = asyncio.ensure_future(player.async_media_stop())
    await hass.settle()
    control.requests.clear()
    await coordinator.async_refresh()
    check("a poll completes while a /button is still waiting", any(e == "rooms" for e, _ in control.requests))
    gate3.set()
    del control.gates["button"]
    await slow

    coordinator.cancel_pending()
    await hass.settle()


asyncio.run(main())
check.done("display")
