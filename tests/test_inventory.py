#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for the inventory rules - the logic that decides whether a customer's
entities appear, stay, or are destroyed.

Run:  python3 tests/test_inventory.py     (exit 0 = all pass)

No Home Assistant required. inventory.py is loaded straight from its path. A
copy of the Hub integration's suite, with this integration's kinds: units and
activities.

⚠ WHY THIS FILE EXISTS. The Hub integration's 0.4.0 shipped an hour-long
delete grace that did NOTHING: a config-entry reload built a new coordinator,
the seen-map was rebuilt with it, and anything just removed looked like it had
never existed and was deleted on the spot. test "the grace survives a reload"
below is that bug, pinned here too. And one this integration adds: after a Home
Assistant restart nothing is deletable until the memory has watched for a whole
grace, so an activity that happened to be disabled during the restart does not
lose its voice choice on the spot.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ha_stubs import Checker, load_pure  # noqa: E402

inv = load_pure("inventory")
check = Checker()
GRACE = inv.DELETE_GRACE_SECONDS


def snap(units=(), hidden=(), activities=None):
    """units: offered unit keys; hidden: keys of hidden units; activities: {key: [ids]}.
    A unit in neither list but in activities with [] is present and not offered."""
    activities = activities if activities is not None else {k: [1] for k in units}
    all_keys = set(units) | set(hidden) | set(activities)
    return {
        "units": {k: {"offered": bool(activities.get(k)), "hidden": k in hidden} for k in all_keys},
        "activities": {k: v for k, v in activities.items()},
    }


# --------------------------------------------------------------------------
print("signature - what exists and is shown. Drives the reload.")
sig = inv.signature(snap(units=["zone_1"], activities={"zone_1": [5, 6], "room_2": []}))
check("an offered unit is flagged shown", ("unit", "zone_1", True) in sig)
check("a unit with no activity besides Off is flagged NOT shown", ("unit", "room_2", False) in sig)
check("each offered activity is in it", {("activity", "zone_1:5", True), ("activity", "zone_1:6", True)} <= sig)
check("no snapshot is None, not an empty Control", inv.signature(None) is None and inv.signature({}) is None)
check("a unit's first activity changes the signature (N2: it appears)",
      inv.signature(snap(activities={"room_2": []})) != inv.signature(snap(activities={"room_2": [7]})))
check("hiding a unit changes the signature",
      inv.signature(snap(units=["zone_1"])) != inv.signature(snap(units=["zone_1"], hidden=["zone_1"])))
check("an activity moving to another unit changes the signature",
      inv.signature(snap(activities={"zone_1": [5], "zone_2": [6]}))
      != inv.signature(snap(activities={"zone_1": [5, 6], "zone_2": [8]})))

# --------------------------------------------------------------------------
print("\npresence - the ONLY deletable set. Hidden is not deleted.")
seen: dict = {}
inv.stamp_seen(snap(units=["zone_1"], hidden=["zone_1"]), seen, 1000.0)
check("a HIDDEN unit is stamped (never deletable while hidden)", ("unit", "zone_1") in seen)
seen = {}
inv.stamp_seen(snap(activities={"room_2": []}), seen, 1000.0)
check("a unit that is no longer offered is NOT stamped - its grace starts (N3)", ("unit", "room_2") not in seen)

# --------------------------------------------------------------------------
print("\npresence - the grace, and the clock it runs on")
seen = {}
inv.stamp_seen(snap(units=["zone_9"]), seen, 1000.0)
check("still protected just inside the grace", ("unit", "zone_9") in inv.presence(seen, 1000.0 + GRACE - 1))
check("deletable just outside the grace", ("unit", "zone_9") not in inv.presence(seen, 1000.0 + GRACE + 1))
check("custom grace is honoured", ("unit", "zone_9") not in inv.presence(seen, 1100.0, grace=60))
check("an unreachable Control ages NOTHING out", ("unit", "zone_9") in inv.presence(seen, 1000.0),
      "last_success frozen at the last good round, so nothing expires")
check("no successful round yet is None, so nothing is deletable", inv.presence(seen, None) is None)
check("deletable = seen and gone for the grace",
      inv.deletable(seen, inv.presence(seen, 1000.0 + GRACE + 1)) == {("unit", "zone_9"), ("activity", "zone_9:1")})
check("deletable is empty while presence is None", inv.deletable(seen, None) == frozenset())

print("\npresence - a young memory deletes nothing (restart protection)")
seen = {}
inv.stamp_seen(snap(units=["zone_1"]), seen, 5000.0)   # first round after a restart: activity 7 absent
check("within the first grace after the memory began: None (nothing deletable)",
      inv.presence(seen, 5000.0 + GRACE - 1, first_success=5000.0) is None)
check("after a whole grace of successful rounds: deletable as usual",
      inv.presence(seen, 5000.0 + GRACE, first_success=5000.0) is not None)
check("sensitivity: WITHOUT the restart protection it would be a set at once",
      inv.presence(seen, 5000.0) is not None)

# --------------------------------------------------------------------------
print("\nregression - the grace must survive a reload")
surviving: dict = {}
inv.stamp_seen(snap(units=["zone_9"]), surviving, 1000.0)            # unit exists
inv.stamp_seen(snap(activities={"zone_9": []}), surviving, 1010.0)   # lost its last activity -> reload
check("after a reload the unit is still protected", ("unit", "zone_9") in inv.presence(surviving, 1010.0),
      "this is the Hub's 0.4.0 bug: it was deleted on the spot")
check("and it becomes deletable once the grace really elapses",
      ("unit", "zone_9") not in inv.presence(surviving, 1010.0 + GRACE + 1))
rebuilt: dict = {}
inv.stamp_seen(snap(activities={"zone_9": []}), rebuilt, 1010.0)
check("a REBUILT map would forget it at once (test is sensitive)", ("unit", "zone_9") not in inv.presence(rebuilt, 1010.0))

print("\ncoming back within the hour restores it")
back: dict = {}
inv.stamp_seen(snap(units=["zone_9"]), back, 1000.0)
inv.stamp_seen(snap(activities={"zone_9": []}), back, 1500.0)
inv.stamp_seen(snap(units=["zone_9"]), back, 2000.0)
check("re-stamped: present long after the first stamp's grace", ("unit", "zone_9") in inv.presence(back, 1000.0 + GRACE + 100))

# --------------------------------------------------------------------------
print("\nun-expose before delete (source order - the ORDER is the whole invariant)")
SRC = (pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "artiled_control" / "__init__.py").read_text()
body = SRC[SRC.index("async def _prune_deleted("):SRC.index("async def async_unload_entry(")]
check("_unexpose() clears every assistant", "for assistant in ASSISTANTS" in SRC[SRC.index("def _unexpose("):])
check("devices: un-expose comes BEFORE async_remove_device",
      body.index("_unexpose(hass, doomed_entities)") < body.index("device_registry.async_remove_device(device.id)"))
check("voice entities: un-expose comes BEFORE async_remove",
      body.index("_unexpose(hass, doomed)") < body.index("entity_registry.async_remove(entity_id)"))
check("entity ids are collected BEFORE the device is removed",
      body.index("doomed_entities.extend") < body.index("device_registry.async_remove_device(device.id)"))
check("the loop yields between un-exposing and deleting", body.count("await asyncio.sleep(0)") >= 2)
check("pruning is driven by presence, never by the last poll", "coordinator.present" in body)
check("_prune_deleted is awaited at its call site", "await _prune_deleted(" in SRC)
REMOVE = SRC[SRC.index("async def async_remove_entry("):]
check("removal un-exposes before it deletes the stores",
      REMOVE.index("_unexpose(") < REMOVE.index("async_remove()"))

check.done("inventory")
