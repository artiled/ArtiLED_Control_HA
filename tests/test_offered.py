#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for Michel's empty-room rule (plan section 1.8) - which units exist in
Home Assistant, and what happens when that changes.

Run:  python3 tests/test_offered.py     (exit 0 = all pass)

The real integration, set up through the real async_setup_entry, against a fake
Control (tests/ha_stubs.py).

⚠ WHY THIS FILE EXISTS. Michel 2026-10-25: a room or zone with nothing but its
Off activity is not created and not offered anywhere (N2); one that gets its
first activity later APPEARS on its own (N2); one that loses its last is
unavailable at once, removed after the hour - un-exposed first - and fully
restored if it comes back within it (N3). And a failed read is not an empty
Control: only a round in which everything answered may decide emptiness.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
GARAGE = "media_player.garage"


def shown(hass):
    return hass.data["artiled_control"]["e1"]["shown_units"]


async def appear_and_vanish(api):
    print(f"\n-- API {api or 'before 2.00 (one /activities call per unit)'} --")
    control = S.house(S.FakeControl(api=api))
    hass, entry, control = await S.setup_integration(control)

    print("an Off-only unit is not created")
    check("the Garage (only an Off activity) has no entity", hass.entity(GARAGE) is None)
    check("...and is not among the offered units", "room_4" not in shown(hass))
    check("...and has no device", hass.device_registry.async_get_device({("artiled_control", f"{S.IDENT}_room_4")}) is None)
    check("a hidden unit (Study) is not created either", "room_5" not in shown(hass))
    check("units with activities ARE created", {"zone_11", "zone_12", "room_2", "zone_31"} <= set(shown(hass)))
    if api:
        check("API 2.00: one /activities call for every unit",
              [d for e, d in control.requests if e == "activities"] == [{}])
    else:
        check("older API: one /activities call per unit",
              all(d for e, d in control.requests if e == "activities"))

    print("\na first activity appears on the next inventory round, without re-setup (N2)")
    control.activities.append(S.activity(41, "Workshop music", room_id=4))
    reloads = hass.config_entries.reloads
    await S.poll(hass, inventory=True)
    check("the inventory change reloaded the entry", hass.config_entries.reloads == reloads + 1)
    check("the Garage now has its media_player", hass.entity(GARAGE) is not None)
    check("...and it is available", hass.entity(GARAGE).available)
    hass.entity_registry.entities[GARAGE].aliases = ["the shed"]   # the user's own setting

    print("\nlosing the last activity: unavailable at once, removed after the hour (N3)")
    control.activities = [a for a in control.activities if a["id"] != 41]
    await S.poll(hass, inventory=True)
    check("no live entity any more (Home Assistant shows it unavailable)", hass.entity(GARAGE) is None)
    check("...but its registry entry - name, aliases, exposure - is kept", GARAGE in hass.entity_registry.entities)
    hass.exposure[("cloud.alexa", GARAGE)] = True
    S.age(hass, "e1", 1800)
    await S.poll(hass, inventory=True)
    check("half an hour later: still kept", GARAGE in hass.entity_registry.entities)

    print("\ncoming back within the hour restores it complete")
    control.activities.append(S.activity(41, "Workshop music", room_id=4))
    await S.poll(hass, inventory=True)
    check("the same entity_id is back", hass.entity(GARAGE) is not None)
    check("the user's alias survived", getattr(hass.entity_registry.entities[GARAGE], "aliases", None) == ["the shed"])

    print("\ngone for the whole hour: un-exposed FIRST, then removed")
    control.activities = [a for a in control.activities if a["id"] != 41]
    await S.poll(hass, inventory=True)
    S.age(hass, "e1", 3601)
    hass.log.clear()
    await S.poll(hass, inventory=True)
    check("the entity is removed", GARAGE not in hass.entity_registry.entities)
    check("its device is removed", hass.device_registry.async_get_device({("artiled_control", f"{S.IDENT}_room_4")}) is None)
    unexposed = [i for i, e in enumerate(hass.log) if e[0] == "expose" and e[2] == GARAGE and e[3] is False]
    removed = [i for i, e in enumerate(hass.log) if e == ("remove", GARAGE)]
    check("un-exposed from Alexa BEFORE it was removed", unexposed and removed and unexposed[0] < removed[0], str(hass.log))
    check("the other units are untouched", {"zone_11", "zone_12", "room_2", "zone_31"} <= set(shown(hass)))
    await S.stop(hass)


async def failed_reads():
    print("\n-- a failed or partial read is not an empty Control --")
    control = S.house(S.FakeControl(api=""))
    hass, entry, control = await S.setup_integration(control)
    before = list(shown(hass))
    control.activity_errors.add("room_2")
    reloads = hass.config_entries.reloads
    await S.poll(hass, inventory=True)
    check("a partial round (one unit's read failed) changes nothing", shown(hass) == before)
    check("...and does not reload", hass.config_entries.reloads == reloads)
    check("the Kitchen keeps its activities", hass.entity("media_player.kitchen").source_list == ["Listen to Sonos"])
    control.activity_errors.clear()
    control.fail.add("activities")
    await S.poll(hass, inventory=True)
    check("activities unreachable: nothing changes", shown(hass) == before)
    check("...and the rooms stay available (the state poll still answered)", hass.entity("media_player.kitchen").available)
    control.fail.clear()
    await S.stop(hass)

    control = S.house(S.FakeControl(api="2.00"))
    hass, entry, control = await S.setup_integration(control)
    control.activities_override = {"activities": "garbage"}
    reloads = hass.config_entries.reloads
    await S.poll(hass, inventory=True)
    check("a malformed /activities answer keeps the last good inventory",
          shown(hass) == before and hass.config_entries.reloads == reloads)
    control.activities_override = None

    print("\npositive control: a fully successful EMPTY round does decide")
    control.activities = [a for a in control.activities if a["name"] == "Off"]
    await S.poll(hass, inventory=True)
    check("every unit Off-only, read successfully -> nothing offered", shown(hass) == [])
    await S.stop(hass)

    print("\nfirst setup without a readable inventory waits, it does not create an empty Control")
    control = S.house(S.FakeControl(api="2.00"))
    control.fail.add("activities")
    try:
        await S.setup_integration(control, entry_id="e9")
        waited = False
    except S.ConfigEntryNotReady:
        waited = True
    check("setup raises ConfigEntryNotReady (Home Assistant retries)", waited)


async def main():
    await appear_and_vanish("2.00")
    await appear_and_vanish("")
    await failed_reads()


asyncio.run(main())
check.done("offered-unit")
