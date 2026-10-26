#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for the unit model - which units exist, what they are called, when
they are on, and what they offer.

Run:  python3 tests/test_units.py     (exit 0 = all pass)

No Home Assistant required; units.py is loaded straight from its path.

⚠ WHY THIS FILE EXISTS. The unit rules are a PORT of the Control webapp's
app/includes/units.php. Two of them have already cost a demo: the Off activity
is an activity, and running it is OFF, not on (Michel's CinemaDream demo,
2026-09-07); and a start in progress must show the starting activity, on,
unless it is the Off activity starting. The TWIN section at the end runs the
webapp's own PHP on the same fixtures when a Control API checkout and php are
present (CONTROL_API, default /opt/artiled/api/ArtiLED_Control_API), so the
port cannot drift from the webapp without this failing.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ha_stubs import Checker, activity, load_pure, room, zone  # noqa: E402

units = load_pure("units")
check = Checker()


def raw(active=0, off=9, starting=0, **extra):
    row = {"active_activity": active, "off_activity": off, "starting_activity": starting, "on_activity": 1}
    row.update(extra)
    return row


# --------------------------------------------------------------------------
print("on / off / starting (plan 1.7)")
check("nothing running = off", not units.unit_on(raw(active=0)))
check("the Off activity running = OFF, not on (the CinemaDream bug)", not units.unit_on(raw(active=9, off=9)))
check("another activity running = on", units.unit_on(raw(active=3, off=9)))
check("starting another activity = on", units.unit_on(raw(active=9, off=9, starting=3)))
check("starting Off while A runs = off", not units.unit_on(raw(active=3, off=9, starting=9)))
check("no Off activity at all (0) and something runs = on", units.unit_on(raw(active=3, off=0)))
check("display activity = the starting one while it starts", units.display_activity(raw(active=3, starting=4)) == 4)
check("display activity = the running one otherwise", units.display_activity(raw(active=3)) == 3)

# --------------------------------------------------------------------------
print("\nwhich units exist (port of app_units_from_lists)")
rooms = [room(1, "Living Room"), room(2, "Kitchen"), room(3, "Bedroom"), room(5, "Study", disabled=1)]
zones = [zone(11, "Main", 1), zone(12, "Secondary", 1), zone(31, "Main", 3), zone(99, "Patio", 42),
         zone(13, "Bar", 1, disabled=1)]
found = units.units_from_lists(rooms, zones)
keys = [u["key"] for u in found]
check("a room WITH zones is not a unit; its zones are", "room_1" not in keys and {"zone_11", "zone_12"} <= set(keys))
check("a room WITHOUT zones is a unit", "room_2" in keys)
check("an orphan zone (its room is gone) is still a unit", "zone_99" in keys)
check("order: rooms in order, each room's zones in place, orphans last",
      keys == ["zone_11", "zone_12", "zone_13", "room_2", "zone_31", "room_5", "zone_99"], str(keys))
check("a hidden zone is KEPT, flagged hidden (unavailable, never deleted)",
      next(u for u in found if u["key"] == "zone_13")["hidden"])
check("a hidden room unit is kept, flagged hidden", next(u for u in found if u["key"] == "room_5")["hidden"])
check("a zone's caption is its room's name", next(u for u in found if u["key"] == "zone_11")["caption"] == "Living Room")
check("a room unit has no caption", next(u for u in found if u["key"] == "room_2")["caption"] == "")
check("legacy id: zone = its id, room = minus its id",
      units.legacy_id("zone", 11) == 11 and units.legacy_id("room", 2) == -2)

# --------------------------------------------------------------------------
print("\nnames (Q3: the zone name when unique on this Control, else '<Room> <Zone>')")
names = units.unit_names(found)
check("a unique zone name stays", names["zone_12"] == "Secondary")
check("a repeated zone name gets its room", names["zone_11"] == "Living Room Main" and names["zone_31"] == "Bedroom Main", str(names))
check("the clash is found ignoring case",
      units.unit_names(units.units_from_lists([room(1, "A"), room(2, "B")],
                                              [zone(1, "Main", 1), zone(2, "MAIN", 2)]))["zone_2"] == "B MAIN")
check("a room unit keeps its own name", names["room_2"] == "Kitchen")
check("an orphan zone with a clash cannot be prefixed and keeps its name",
      units.unit_names(units.units_from_lists([room(1, "A")], [zone(1, "Main", 1), zone(2, "Main", 0)]))["zone_2"] == "Main")
check("a zone clashing with a ROOM unit's name gets its room",
      units.unit_names(units.units_from_lists([room(1, "Cinema"), room(2, "Kitchen")],
                                              [zone(1, "Kitchen", 1)]))["zone_1"] == "Cinema Kitchen")

# --------------------------------------------------------------------------
print("\nactivities and the source list")
rows = [activity(1, "Watch TV", room_id=2), activity(2, "Off", room_id=2),
        activity(3, "Zone thing", zone_id=21, room_id=2), activity(4, "Old", room_id=2, disabled=1)]
mine = units.unit_activity_rows(rows, "room")
check("a room unit drops activities that name a zone (D1)", all(r["id"] != 3 for r in mine))
check("disabled activities are dropped", all(r["id"] != 4 for r in mine))
check("a zone unit keeps rows with its zone_id", [r["id"] for r in units.unit_activity_rows([rows[2]], "zone")] == [3])
src = units.source_list(mine, 2)
check("the source list excludes off_activity", [label for label, _ in src] == ["Watch TV"], str(src))
check("labels map back to ids", src == [("Watch TV", 1)])
grouped = units.group_all_activities([activity(1, "A", room_id=2), activity(2, "B", zone_id=11, room_id=1),
                                      activity(3, "C", zone_id=11, room_id=1)])
check("G3: one answer is grouped per unit by zone_id / room_id",
      [r["id"] for r in grouped["zone_11"]] == [2, 3] and [r["id"] for r in grouped["room_2"]] == [1])

print("\nduplicate names get ' (2)' without taking a real name")
check("X, X -> X, X (2)", units.unique_labels(["X", "X"]) == ["X", "X (2)"])
check("X, X, X (2) -> X, X (3), X (2)", units.unique_labels(["X", "X", "X (2)"]) == ["X", "X (3)", "X (2)"],
      str(units.unique_labels(["X", "X", "X (2)"])))
check("case-insensitive: x, X -> x, X (2)", units.unique_labels(["x", "X"]) == ["x", "X (2)"])
check("three of a kind", units.unique_labels(["A", "A", "A"]) == ["A", "A (2)", "A (3)"])
dup = units.source_list([activity(1, "Netflix"), activity(2, "Netflix"), activity(3, "Off")], 3)
check("duplicate names still start different ids", dup == [("Netflix", 1), ("Netflix (2)", 2)], str(dup))

print("\nthe offered rule (Michel 2026-10-25, N2)")
check("a unit with an activity besides Off is offered", units.is_offered(mine, 2))
check("an Off-only unit is NOT offered", not units.is_offered([activity(2, "Off")], 2))
check("no activities at all is not offered", not units.is_offered([], 2))

print("\nis this Control new enough (G5: feature detection)")
check("rooms/zones with on/off/starting are fine", not units.too_old(rooms, zones))
old = [dict((k, v) for k, v in room(1, "X").items() if k != "starting_activity")]
check("a room without starting_activity is too old", units.too_old(old, []))
check("no rooms and no zones is not 'too old'", not units.too_old([], []))


# --------------------------------------------------------------------------
# THE TWIN: the webapp's own PHP on the same fixtures.
print("\ntwin: the webapp's app_units_from_lists on the same fixtures")
api_root = pathlib.Path(os.environ.get("CONTROL_API", "/opt/artiled/api/ArtiLED_Control_API"))
php = shutil.which("php")
twin = pathlib.Path(__file__).resolve().parent / "twin_units.php"
CASES = {
    "the reference house": (rooms, zones),
    "Off running": ([room(1, "A", active=9, off=9, on=3)], []),
    "starting Off while A runs": ([room(1, "A", active=3, off=9, on=3, starting=9, starting_name="Off")], []),
    "starting B over A": ([room(1, "A", active=3, off=9, on=3, starting=4, starting_name="B")], []),
    "starting A from Off": ([room(1, "A", active=9, off=9, on=3, starting=3, starting_name="A")], []),
    "P=0": ([room(1, "A", active=0, off=9, on=3)], []),
    "orphan + hidden zone": ([room(1, "A")], [zone(2, "Z", 1, disabled=1), zone(3, "Y", 7, active=5, off=6)]),
}
if php is None or not (api_root / "app" / "includes" / "units.php").exists():
    print("  skip  no php or no Control API checkout (set CONTROL_API)")
else:
    for label, (r, z) in CASES.items():
        out = subprocess.run([php, str(twin)], input=json.dumps({"rooms": r, "zones": z}), text=True,
                             capture_output=True, env={**os.environ, "CONTROL_API": str(api_root)}, timeout=30)
        try:
            theirs = json.loads(out.stdout)
        except ValueError:
            check(f"{label}: the webapp's PHP ran", False, out.stderr[-300:])
            continue
        ours = [u for u in units.units_from_lists(r, z) if not u["hidden"]]
        same_list = [(t["kind"], t["id"], t["caption"]) for t in theirs] == [(u["kind"], u["id"], u["caption"]) for u in ours]
        check(f"{label}: same units, order and captions (hidden left out, as the webapp does)", same_list,
              f"theirs {[(t['kind'], t['id']) for t in theirs]} ours {[(u['kind'], u['id']) for u in ours]}")
        if not same_list:
            continue
        on_same = all((t["state"] == "1") == units.unit_on(u["raw"]) for t, u in zip(theirs, ours))
        check(f"{label}: same on/off", on_same,
              str([(t["state"], units.unit_on(u["raw"])) for t, u in zip(theirs, ours)]))
        shown_same = all(
            int(t["activity"]) == (units.display_activity(u["raw"]) if units.unit_on(u["raw"]) else 0)
            and int(t["starting"]) == units.starting(u["raw"])
            for t, u in zip(theirs, ours))
        check(f"{label}: same shown and starting activity", shown_same)

check.done("unit")
