# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Pure helpers for unique_ids and entity ids. No Home Assistant imports, so
the rules can be tested without the framework.

Every id is built from the Control's STORED ident (the config entry's
unique_id), never from a live answer or the host: a Control that moves to a new
address keeps every entity id, alias, area and voice choice.

    <ident>_zone_3                               the unit media_player (and device)
    <ident>_room_7                               a room without zones
    <ident>_voice_activity_zone_3_12             a voice scene: unit zone 3, activity 12
    <ident>_version                              the diagnostic sensor
"""
from __future__ import annotations

UNIT_KINDS = ("zone", "room")


def unit_unique_id(ident: str, kind: str, item_id: int) -> str:
    return f"{ident}_{kind}_{int(item_id)}"


def voice_unique_id(ident: str, kind: str, item_id: int, activity_id: int) -> str:
    return f"{ident}_voice_activity_{kind}_{int(item_id)}_{int(activity_id)}"


def parse_unit_uid(unique_id: str, ident: str) -> tuple[str, int] | None:
    """"<ident>_zone_3" -> ("zone", 3); anything else None.

    ⚠ This decides which entities get RENAMED, so it has to be exact. The same
    entry also owns "<ident>_voice_activity_zone_3_12" and "<ident>_version";
    renaming either would break ids a user may already reference. Matched
    positively; nothing is assumed from a prefix alone.
    """
    prefix = f"{ident}_"
    if not ident or not str(unique_id).startswith(prefix):
        return None
    kind, sep, item_id = str(unique_id)[len(prefix):].partition("_")
    if not sep or kind not in UNIT_KINDS or not item_id.isdigit() or int(item_id) <= 0:
        return None
    return kind, int(item_id)


def parse_voice_uid(unique_id: str, ident: str) -> tuple[str, int, int] | None:
    """"<ident>_voice_activity_zone_3_12" -> ("zone", 3, 12); anything else None."""
    prefix = f"{ident}_voice_activity_"
    if not ident or not str(unique_id).startswith(prefix):
        return None
    parts = str(unique_id)[len(prefix):].split("_")
    if len(parts) != 3 or parts[0] not in UNIT_KINDS or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return parts[0], int(parts[1]), int(parts[2])


def pin_plan(entries, wanted_for, already, taken) -> tuple[dict, set]:
    """Which entities to rename, once, onto their clean id.

    entries: (unique_id, entity_id) pairs of this config entry.
    wanted_for(unique_id): the clean entity id, or None for anything we do not pin.
    already: unique_ids pinned before - never touched again, so a user's own
             rename afterwards stands.
    taken(entity_id): True when anything else holds that id.

    Returns ({entity_id: new_entity_id}, unique_ids to record as pinned).

    ⚠ Each entity is pinned EXACTLY ONCE, on its first appearance, and never
    onto an id something else holds. An entity_id that anything references
    cannot be changed without silently breaking dashboards, scripts and
    automations, so this is only defensible while the entity is brand new.
    """
    renames: dict[str, str] = {}
    pinned: set[str] = set()
    claimed: set[str] = set()
    for unique_id, entity_id in entries:
        if unique_id in already:
            continue
        wanted = wanted_for(unique_id)
        if wanted is None:
            continue
        pinned.add(unique_id)
        if wanted == entity_id:
            continue
        if wanted in claimed or taken(wanted):
            continue
        claimed.add(wanted)
        renames[entity_id] = wanted
    return renames, pinned


def _digest(key: str) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha1(str(key).encode()).hexdigest()[:12]


def favorite_unique_id(ident: str, favorite_key: str) -> str:
    """A voice favourite of the whole house: its play's identity (hashed - a
    key can be a long Sonos name or a Zidoo address)."""
    return f"{ident}_voice_favorite_{_digest(favorite_key)}"


def room_favorite_unique_id(ident: str, kind: str, item_id: int, favorite_key: str) -> str:
    """The per-room form of 0.3.9-0.3.11, for the migration."""
    return f"{ident}_voice_favorite_{kind}_{int(item_id)}_{_digest(favorite_key)}"
