# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Voice choices: which unit + activity a spoken name belongs to.

Pure rules with no Home Assistant import, so tests/test_voice.py loads this file
by path, the same way the Hub integration's test_voice.py loads its voice.py.

⚠ ACTIVITIES ARE START-ONLY (Michel 2026-10-25). A voice choice starts one
activity in one unit; there is no "turn off Netflix". Off is always the unit's
media_player, which runs the unit's Off activity.

⚠ A choice is keyed on the activity ID, not its name. Control starts an activity
by id, so renaming "Netflix" to "Watch Netflix" on the Control must not break
what someone says. (The Hub keys on the scene NAME because Hub scenes are
addressed by name.)

Spoken names ignore case when they are matched, as on the Hub: "netflix in the
living room" and "Netflix in the Living Room" are one name. A stored entry keeps
the spelling it was added with.
"""


def same_name(a, b) -> bool:
    """Two names are the same name, ignoring capitals."""
    return str("" if a is None else a).strip().lower() == str("" if b is None else b).strip().lower()


def without_entry(chosen, kind, item_id, activity_id):
    """Everything except this unit + activity - so re-adding is a rename, not a
    duplicate."""
    return [
        t for t in (chosen or [])
        if not (t["kind"] == kind and int(t["id"]) == int(item_id)
                and int(t["activity"]) == int(activity_id))
    ]


# The verb an activity's own name starts with. Said after "turn on" it doubles
# up ("turn on Listen to Sonos in Kitchen"), so the suggestion drops it
# (Michel 2026-10-26: "Turn on Sonos in the Kitchen").
_LEADING_VERBS = ("listen to ", "watch ", "play ", "start ")


def default_spoken_name(activity_name, unit_name) -> str:
    """"Sonos in the Kitchen" - what a person would say after "turn on"."""
    activity = str(activity_name).strip()
    for verb in _LEADING_VERBS:
        if activity.lower().startswith(verb) and len(activity) > len(verb):
            activity = activity[len(verb):].strip()
            break
    return f"{activity} in {_with_the(unit_name)}"


def _with_the(unit_name) -> str:
    unit = str(unit_name).strip()
    return unit if unit.lower().startswith("the ") else f"the {unit}"


def long_spoken_name(activity_name, unit_name) -> str:
    """"Listen to Sonos in the Kitchen" - the activity's full name, verb kept.

    Offered as an ALIAS beside the short spoken name (Michel 2026-10-26), and
    as the suggestion when the short one is taken.
    """
    return f"{str(activity_name).strip()} in {_with_the(unit_name)}"


def suggest_spoken_name(activity_name, unit_name, in_use) -> str:
    """The short form, unless something already answers to it - then the long
    one: "Listen to Zidoo" and "Watch Zidoo" in one Kitchen would both be
    "Zidoo in the Kitchen", so the second keeps its verb. `in_use(name)` says
    whether a name is taken (ignoring case)."""
    short = default_spoken_name(activity_name, unit_name)
    return long_spoken_name(activity_name, unit_name) if in_use(short) else short


def long_alias(spoken, long_name, aliases, in_use):
    """The long form to add as an alias, or None: not when it IS the spoken
    name, not when the entity already has it, and not when something else
    answers to it (an assistant must find exactly one thing). Ignores case."""
    if not str(long_name or "").strip() or same_name(long_name, spoken):
        return None
    if any(isinstance(a, str) and same_name(a, long_name) for a in aliases or []):
        return None
    if in_use(long_name):
        return None
    return str(long_name).strip()


def name_taken(name, everything, control=None, kind=None, item_id=None, activity_id=None):
    """The entry that already answers to this spoken name, or None.

    `everything` is every stored choice of EVERY ArtiCONTROL entry, each with
    its "control" (the entry's ident): one house can have two Controls, and an
    assistant that hears "Netflix in the Living Room" must find exactly one
    thing. The entry being replaced (same Control, unit and activity) does not
    count against itself.
    """
    for t in everything or []:
        if not same_name(t.get("name"), name):
            continue
        if (kind is not None and t.get("control") == control and t["kind"] == kind
                and int(t["id"]) == int(item_id) and int(t["activity"]) == int(activity_id)):
            continue
        return t
    return None


def binding_problem(entry, offered_units, activities_by_unit, off_by_unit):
    """Why a voice choice cannot start right now, or None when it can.

    ⚠ NEVER RE-BOUND SILENTLY. A choice belongs to its unit AND its activity id.
    If that activity moved to another unit, was disabled or deleted, became the
    unit's Off activity, or the unit is no longer offered (Michel's empty-room
    rule), the choice refuses - it does not go looking for something similar.
    """
    key = f"{entry['kind']}_{int(entry['id'])}"
    if key not in offered_units:
        return "unit"
    activity_id = int(entry["activity"])
    if activity_id == int(off_by_unit.get(key) or 0):
        return "off"
    if not any(int(row.get("id", 0)) == activity_id for row in activities_by_unit.get(key) or []):
        return "activity"
    return None


# ---- voice favourites (Michel 2026-10-26) -----------------------------------
#
# "Favourites are favourites in ANY room": a heart of the Control (an album, a
# playlist, a film) under ONE spoken name for the whole house - "Thriller" -
# played in the one room playing at that moment (media.voice_room). The play
# object is the Control's own, passed through.


def favorite_key(play) -> str:
    """One identity per playable thing (media.play_key, as text)."""
    play = play or {}
    if play.get("kind") == "movie":
        return f"movie|{int(play.get('title_id') or 0)}|{int(play.get('copy') or 0)}"
    return f"music|{play.get('source')}|{play.get('key')}"


# How a favourite's kind and service are said ("say movies, not films").
_KIND_WORD = {"Movie": "movie", "Radio": "radio", "Track": "song"}
_SERVICE_WORD = {"roon": "Roon", "sonos": "Sonos", "spotify": "Spotify", "heos": "HEOS", "zidoo": "Zidoo",
                 "movies": "the movie player"}


def favorite_names(title, kind, service) -> list:
    """The spoken names to try, in order (Michel 2026-10-26): "Thriller", then
    with its kind ("Thriller album"), then with its service ("Thriller on
    Roon"). No room: a favourite plays in the right room by itself."""
    title = str(title or "").strip()
    names = [title] if title else []
    k = str(kind or "").strip()
    if title and k:
        names.append(f"{title} {_KIND_WORD.get(k, k.lower())}")
    svc = _SERVICE_WORD.get(str(service or ""), str(service or "").strip())
    if title and svc:
        names.append(f"{title} on {svc}")
    return names


def suggest_favorite_name(title, kind, service, in_use):
    """The first of favorite_names nothing else answers to, or None."""
    for name in favorite_names(title, kind, service):
        if not in_use(name):
            return name
    return None


def plan_auto(hearts, existing, excluded, cap, complete: bool = True) -> dict:
    """"Make all favourites available to voice": what to add and remove.

    hearts: the Control's hearts, one per favourite ({"play", "title", "type",
    "service"}), from every room. complete: every list answered this round -
    otherwise nothing is removed (a failed read is not a heart removed).
    existing: the stored favourites ({"play", "name", "auto"}). excluded: the
    favourite keys the user removed - never added back. cap: the most
    favourite scenes there may be, counting every one.
    ⚠ Only AUTOMATIC ones are ever removed. -> {"add", "remove", "left_out"}
    """
    keys = {favorite_key(h["play"]) for h in hearts or []}
    remove = [f for f in existing or [] if f.get("auto") and complete and favorite_key(f["play"]) not in keys]
    kept = [f for f in existing or [] if f not in remove]
    have = {favorite_key(f["play"]) for f in kept}
    add, left_out = [], 0
    for h in hearts or []:
        k = favorite_key(h["play"])
        if k in have or k in (excluded or set()):
            continue
        have.add(k)
        if len(kept) + len(add) >= cap:
            left_out += 1
            continue
        add.append({"play": h["play"], "title": h.get("title") or "", "type": h.get("type") or "",
                    "service": h.get("service") or "", "auto": True})
    return {"add": add, "remove": remove, "left_out": left_out}


def migrate_favorites(stored, generated) -> dict:
    """0.3.9-0.3.11 kept a favourite PER ROOM ({"kind", "id", "activity",
    "play", "name", "auto"}). Now one per favourite: the first of each play
    stays (its registry entry moves to the new unique_id - its entity id,
    aliases and exposure with it), any other room's copy goes. A name the user
    typed is kept; a generated room name ("Thriller in the Kitchen",
    generated(fav) says) is replaced by the house-wide one.
    -> {"favorites": the new list, "moved": [(old, new)], "retired": [old]}
    """
    out, moved, retired, seen = [], [], [], {}
    for f in stored or []:
        if "kind" not in f:
            k = favorite_key(f["play"])
            if k not in seen:
                seen[k] = f
                out.append(f)
            continue
        k = favorite_key(f["play"])
        if k in seen:
            retired.append(f)
            continue
        new = {"play": f["play"], "name": None if generated(f) else f["name"], "auto": bool(f.get("auto")),
               "title": str((f.get("play") or {}).get("title") or "")}
        seen[k] = new
        out.append(new)
        moved.append((f, new))
    return {"favorites": out, "moved": moved, "retired": retired}
