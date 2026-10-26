#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for unique_ids and the one-time entity id pin.

Run:  python3 tests/test_entity_ids.py     (exit 0 = all pass)

No Home Assistant required; entity_ids.py is loaded straight from its path.

⚠ WHY THIS FILE EXISTS. These ids are what every automation, dashboard card,
alias and voice exposure hangs on. A parser that matches too loosely renames
something it should not; a pin that runs twice overrules a user; a pin onto a
taken id breaks someone else's entity.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ha_stubs import Checker, load_pure  # noqa: E402

ids = load_pure("entity_ids")
check = Checker()
I = "c0ffee000001"

print("unique_id shapes")
check("unit", ids.unit_unique_id(I, "zone", 3) == f"{I}_zone_3")
check("room unit", ids.unit_unique_id(I, "room", 7) == f"{I}_room_7")
check("voice", ids.voice_unique_id(I, "zone", 3, 12) == f"{I}_voice_activity_zone_3_12")

print("\nparse_unit_uid matches exactly")
check("zone", ids.parse_unit_uid(f"{I}_zone_3", I) == ("zone", 3))
check("room", ids.parse_unit_uid(f"{I}_room_7", I) == ("room", 7))
for bad in (f"{I}_voice_activity_zone_3_12", f"{I}_version", f"{I}_zone_", f"{I}_zone_x", f"{I}_zone_0",
            f"{I}_light_3", "other_zone_3", f"{I}zone_3", f"{I}_zone_3_extra"):
    check(f"not a unit: {bad}", ids.parse_unit_uid(bad, I) is None)
check("an empty ident matches nothing", ids.parse_unit_uid("_zone_3", "") is None)

print("\nparse_voice_uid matches exactly")
check("voice", ids.parse_voice_uid(f"{I}_voice_activity_zone_3_12", I) == ("zone", 3, 12))
for bad in (f"{I}_zone_3", f"{I}_voice_activity_zone_3", f"{I}_voice_activity_light_3_12", f"{I}_voice_activity_zone_3_x"):
    check(f"not a voice choice: {bad}", ids.parse_voice_uid(bad, I) is None)

print("\npin_plan: once, never onto a taken id")
entries = [(f"{I}_zone_3", "media_player.living_room_living_room"), (f"{I}_zone_4", "media_player.kitchen"),
           (f"{I}_room_7", "media_player.study_2"), (f"{I}_version", "sensor.control_version")]
wanted = {f"{I}_zone_3": "media_player.living_room", f"{I}_zone_4": "media_player.kitchen",
          f"{I}_room_7": "media_player.study"}
renames, pinned = ids.pin_plan(entries, wanted.get, set(), lambda eid: eid == "media_player.study")
check("an area-prefixed id is renamed to the clean one", renames.get("media_player.living_room_living_room") == "media_player.living_room")
check("an id that is already right is recorded, not renamed", f"{I}_zone_4" in pinned and "media_player.kitchen" not in renames)
check("never onto a TAKEN id", "media_player.study_2" not in renames)
check("...but it is still recorded, so it is not retried over a user's later choice", f"{I}_room_7" in pinned)
check("things we do not pin are left alone", f"{I}_version" not in pinned)
renames2, pinned2 = ids.pin_plan(entries, wanted.get, pinned, lambda eid: False)
check("a second run touches nothing (a user's rename stands)", renames2 == {} and pinned2 == set())
dupes = [(f"{I}_zone_3", "media_player.a"), (f"{I}_zone_9", "media_player.b")]
renames3, _ = ids.pin_plan(dupes, lambda uid: "media_player.main", set(), lambda eid: False)
check("two entities wanting one id: only the first gets it", list(renames3.values()) == ["media_player.main"])

check.done("entity id")
