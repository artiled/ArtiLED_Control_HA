# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""What the Control has, what changed, and what may be deleted.

A copy of the Hub integration's inventory rules, with two kinds: UNITS (every
zone, and every room without zones, that is offered) and ACTIVITIES (what each
unit offers to start). Plain functions over plain data - no Home Assistant
imports - so tests/test_inventory.py and tests/test_offered.py can pin them
without a running Home Assistant or a Control.

⚠ The input is an inventory SNAPSHOT, built by the coordinator only from a
round in which /rooms, /zones and every unit's activities all answered. A
failed or partial round produces no snapshot and changes nothing here: a read
that failed is not an empty Control.
"""
from __future__ import annotations

# How long an item must be ABSENT, counted in successful inventory rounds,
# before its entity, device or voice choice may be deleted. Deletion cannot be
# undone and takes the entity's history, area, aliases and voice exposure with
# it, whereas a stale entity is merely untidy.
DELETE_GRACE_SECONDS = 3600


def snapshot(units, activities, offered_rows) -> dict:
    """The inventory a qualified round found.

    units: units.units_from_lists() output. activities / offered_rows: per unit
    key, the unit's enabled activities and the ones it offers (minus Off).
    """
    return {
        "units": {
            u["key"]: {"offered": bool(offered_rows.get(u["key"])), "hidden": bool(u["hidden"])}
            for u in units
        },
        "activities": {
            key: sorted(int(row["id"]) for row in rows)
            for key, rows in offered_rows.items()
        },
    }


def signature(snap: dict | None) -> frozenset | None:
    """What the Control HAS that Home Assistant shows - not what it is doing.

    Starting an activity does not change this; a unit getting its first
    activity, losing its last one, being hidden or shown, or an activity being
    added, deleted, disabled or moved does. A change reloads the entry, so a
    room the customer gives its first activity appears in about a minute
    without anyone setting anything up again (Michel 2026-10-25, N2).

    ⚠ The shown flag (offered and not hidden) is here ONLY so that showing a
    unit again recreates its entity. It must NEVER decide what to delete - that
    is `presence`.

    None while there is no snapshot yet, so a failed first round is never read
    as an empty Control.
    """
    if not snap:
        return None
    items: set[tuple[str, str, bool]] = set()
    for key, unit in (snap.get("units") or {}).items():
        items.add(("unit", key, bool(unit["offered"]) and not unit["hidden"]))
    for key, ids in (snap.get("activities") or {}).items():
        for activity_id in ids:
            items.add(("activity", f"{key}:{activity_id}", True))
    return frozenset(items)


def stamp_seen(snap: dict | None, seen: dict, now: float) -> None:
    """Record that everything in the snapshot still exists, at time `now`.

    ⚠ A HIDDEN unit is stamped, WHATEVER its activities (Mycroft 8662, HA-S4):
    it is one the customer means to bring back, so its device, entity, name and
    aliases stay while it is hidden - even if its last activity goes meanwhile.
    Stamping creates nothing: entities are only made for shown units.
    ⚠ A shown unit that is no longer offered (its last activity gone) is NOT
    stamped, so it starts its grace hour - unavailable at once, removed after
    the hour, fully restored if an activity comes back in time (N3).

    `seen` is mutated in place and MUST outlive a config-entry reload; see the
    coordinator.
    """
    if not snap:
        return
    for key, unit in (snap.get("units") or {}).items():
        if unit["offered"] or unit["hidden"]:
            seen[("unit", key)] = now
    for key, ids in (snap.get("activities") or {}).items():
        for activity_id in ids:
            seen[("activity", f"{key}:{activity_id}")] = now


def presence(
    seen: dict,
    last_success: float | None,
    first_success: float | None = None,
    grace: float = DELETE_GRACE_SECONDS,
) -> frozenset | None:
    """Everything the Control still has, or had recently. The ONLY deletable
    set: anything not in it may go.

    ⚠ The clock is the LAST SUCCESSFUL ROUND, not wall time. If the Control
    goes unreachable nothing ages out while it is gone.

    ⚠ None - nothing deletable - until this memory has itself watched for a
    whole grace. The memory survives a reload but not a Home Assistant restart;
    the Hub integration takes whatever the first round after a restart reports
    as the new baseline. Here that would delete, on the spot, the voice choice
    of an activity that merely happened to be disabled during the restart. An
    hour of successful rounds first costs nothing.
    """
    if last_success is None:
        return None
    if first_success is not None and last_success - first_success < grace:
        return None
    cutoff = last_success - grace
    return frozenset(key for key, at in seen.items() if at >= cutoff)


def deletable(seen: dict, present: frozenset | None) -> frozenset:
    """What `presence` gives up on: seen once, absent for the whole grace."""
    if present is None:
        return frozenset()
    return frozenset(key for key in seen if key not in present)
