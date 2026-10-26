# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Units, their state, and what Home Assistant shows for them.

A UNIT is what the Control's webapp shows as a tile: every zone, plus every
room that has no zones. A room that has zones is not a unit; its zones are
(API app/includes/units.php). One unit = one Home Assistant device and one
media_player.

Pure rules with no Home Assistant import, so tests/test_units.py,
tests/test_media_state.py and tests/test_offered.py load this file by path.
Several functions here are PORTS of the webapp's own PHP and say so: when the
webapp changes, the twin test in tests/test_units.py fails until this follows.
"""
from __future__ import annotations

# Now Playing states the daemon writes (artiled_nowplaying.py 'state': literals).
# Only these two are shown as such; stopped, idle, on, unknown and anything new
# read as plain ON - a state we cannot vouch for is never shown as playing.
PLAYING = "playing"
PAUSED = "paused"

# The transport keys a media_player can offer, as /button names them.
TRANSPORT_KEYS = ("play", "pause", "play_pause", "stop", "next", "previous")

# Music sources by name - the twin of app_remote_np_is_film (remote.php).
MUSIC_SOURCES = ("roon", "sonos")

# How far a reported position may drift from where it should be before it is
# published again. Below this, every 5 s poll would be a recorder write.
POSITION_JUMP_SECONDS = 2


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def unit_key(kind: str, item_id) -> str:
    """("zone", 3) -> "zone_3". Also the tail of every unit unique_id."""
    return f"{kind}_{_int(item_id)}"


def parse_unit_key(key: str) -> tuple[str, int] | None:
    """"zone_3" -> ("zone", 3); anything else None."""
    kind, _, item_id = str(key or "").partition("_")
    if kind not in ("zone", "room") or not item_id.isdigit() or int(item_id) <= 0:
        return None
    return kind, int(item_id)


def legacy_id(kind: str, item_id) -> int:
    """The ONE number the Activity Service knows a unit by: the zone id, or
    minus the room id (app_unit_legacy_id). /button's routing takes it."""
    return _int(item_id) if kind == "zone" else -_int(item_id)


def units_from_lists(rooms, zones) -> list[dict]:
    """Port of app_units_from_lists (app/includes/units.php).

    Rooms in their order; a room with zones is represented by its zones, a room
    without zones is a unit itself. A zone whose room is gone is still a unit.

    ⚠ ONE DIFFERENCE, on purpose: the webapp leaves hidden units out, this keeps
    them with hidden=True. A hidden unit is one the customer means to bring
    back, like a disabled Hub light - its entity must not be deleted, only stop
    being offered. Everything else, order included, is the webapp's.
    """
    by_room: dict[int, list[dict]] = {}
    for zone in zones or []:
        by_room.setdefault(_int(zone.get("room_id")), []).append(zone)
    units: list[dict] = []
    placed: set[int] = set()
    for room in rooms or []:
        rid = _int(room.get("id"))
        if by_room.get(rid):
            for zone in by_room[rid]:
                placed.add(_int(zone.get("id")))
                units.append(_unit("zone", zone, str(room.get("name") or ""), rid))
        else:
            units.append(_unit("room", room, "", rid))
    for zone in zones or []:
        if _int(zone.get("id")) not in placed:
            units.append(_unit("zone", zone, "", _int(zone.get("room_id"))))
    return units


def _unit(kind: str, row: dict, caption: str, room_id: int) -> dict:
    return {
        "kind": kind,
        "id": _int(row.get("id")),
        "key": unit_key(kind, row.get("id")),
        "name": str(row.get("name") or ""),
        # The room's name, for a zone ("" for a room unit or an orphan zone).
        "caption": caption,
        "room_id": room_id,
        "hidden": _int(row.get("disabled")) == 1,
        "raw": row,
    }


def unit_names(units) -> dict[str, str]:
    """What Home Assistant calls each unit (Michel 2026-10-25, Q3).

    A zone keeps its own name when that name is unique among the units of this
    Control; otherwise it becomes "<Room> <Zone>". The doc's own example of
    zones is "Main, Secondary", so two rooms each with a "Main" is normal, and
    two devices both called "Main" are useless to a voice assistant.
    Room units always keep their own name. Compared ignoring case, as a person
    saying it would.
    """
    counts: dict[str, int] = {}
    for unit in units or []:
        folded = unit["name"].strip().lower()
        counts[folded] = counts.get(folded, 0) + 1
    names: dict[str, str] = {}
    for unit in units or []:
        name = unit["name"].strip() or f"{unit['kind'].title()} {unit['id']}"
        if unit["kind"] == "zone" and unit["caption"] and counts.get(unit["name"].strip().lower(), 0) > 1:
            name = f"{unit['caption'].strip()} {name}"
        names[unit["key"]] = name
    return names


# ---- on, off, starting (plan section 1.7) ----------------------------------
#
# ⚠ These work on the RAW /rooms and /zones fields, never on the webapp's
# display record: app_unit_record swaps the activity for the starting one and
# keeps the published one apart. P = active_activity, S = starting_activity
# (0 = nothing starting), O = off_activity.


def starting(raw: dict) -> int:
    return max(0, _int((raw or {}).get("starting_activity")))


def unit_on(raw: dict) -> bool:
    """The webapp rule (app_unit_record): S != O while something starts, else
    P > 0 and P != O. ⚠ The Off activity IS an activity: running it makes it
    the active one, and that means OFF (the 2026-09-07 CinemaDream bug)."""
    raw = raw or {}
    off_id = _int(raw.get("off_activity"))
    s = starting(raw)
    if s > 0:
        return s != off_id
    p = _int(raw.get("active_activity"))
    return p > 0 and p != off_id


def display_activity(raw: dict) -> int:
    """The activity shown: the starting one while it starts, else the running
    one. Presentation only - source, "Starting...", the activity's keys."""
    s = starting(raw)
    return s if s > 0 else _int((raw or {}).get("active_activity"))


def media_state(raw: dict) -> str:
    """"off" | "on" | "playing" | "paused" - plan section 1.2, in precedence.

    Unavailable (three failed polls) is decided by the coordinator, before this.
    ⚠ Starting must LOOK active (feedback_starting_looks_active): a starting
    non-Off activity is ON, never buffering, and the OLD activity's playing or
    paused is not carried over it.
    """
    if not unit_on(raw):
        return "off"
    if starting(raw) > 0:
        return "on"
    np = (raw or {}).get("now_playing")
    if isinstance(np, dict):
        if np.get("state") == PLAYING:
            return "playing"
        if np.get("state") == PAUSED:
            return "paused"
    return "on"


def now_playing(raw: dict) -> dict | None:
    """The Now Playing record to show, or None.

    Shown only while the unit is on and nothing is starting - the webapp shows
    no title or picture of the old activity over a start (units.php).
    """
    if not unit_on(raw) or starting(raw) > 0:
        return None
    np = (raw or {}).get("now_playing")
    return np if isinstance(np, dict) else None


def media_title(np: dict | None) -> str | None:
    """The title, or the film selected in a Kaleidescape's menu while nothing
    plays (remote.php app_remote_np_title)."""
    if not np:
        return None
    title = str(np.get("title") or "") or str(np.get("selected_title") or "")
    return title or None


def is_music(np: dict | None) -> bool:
    """Music when an artist is named or the source is Roon or Sonos; anything
    else is a film. The twin of app_remote_np_is_film (remote.php)."""
    if not np:
        return False
    return str(np.get("source") or "") in MUSIC_SOURCES or str(np.get("artist") or "").strip() != ""


# ---- activities -------------------------------------------------------------


def unit_activity_rows(rows, kind: str) -> list[dict]:
    """Port of app_unit_activity_rows' filter: enabled only, and for a ROOM
    unit an activity that also names a zone belongs to that zone (D1)."""
    out = []
    for row in rows or []:
        if kind == "room" and _int(row.get("zone_id")) > 0:
            continue
        if _int(row.get("disabled")) == 1:
            continue
        out.append(row)
    return out


def group_all_activities(rows) -> dict[str, list[dict]]:
    """/activities {} (API 2.00, G3): every unit's activities in one answer.
    zone_id above 0 = that zone's, else the room room_id's. Order kept."""
    grouped: dict[str, list[dict]] = {}
    for row in rows or []:
        zone_id = _int(row.get("zone_id"))
        key = unit_key("zone", zone_id) if zone_id > 0 else unit_key("room", row.get("room_id"))
        grouped.setdefault(key, []).append(row)
    return grouped


def offered_activities(rows, off_id) -> list[dict]:
    """What a unit offers to start: its enabled activities minus its Off."""
    off_id = _int(off_id)
    return [row for row in rows or [] if _int(row.get("id")) != off_id or off_id <= 0]


def is_offered(rows, off_id) -> bool:
    """Michel's rule (2026-10-25, N2): a unit is created and offered only when
    it has at least one enabled activity OTHER THAN its Off activity. Devices
    do not count. An Off-only unit is not a room anyone can start anything in."""
    return bool(offered_activities(rows, off_id))


def unique_labels(names) -> list[str]:
    """One label per name, in order, with duplicates told apart by " (2)".

    ⚠ A suffix never takes a label a REAL name already has. With X, X, X (2)
    the second X becomes "X (3)", because "X (2)" belongs to the third. Compared
    ignoring case, as a spoken or typed name would be. A label maps back to an
    activity ID, never to a name, so two labels can never start the same thing
    by accident.
    """
    names = [str(n or "") for n in names or []]
    reserved = {n.lower() for n in names}
    used: set[str] = set()
    labels: list[str] = []
    for name in names:
        label = name
        if label.lower() in used:
            n = 2
            while True:
                label = f"{name} ({n})"
                if label.lower() not in used and label.lower() not in reserved:
                    break
                n += 1
        used.add(label.lower())
        labels.append(label)
    return labels


def source_list(rows, off_id) -> list[tuple[str, int]]:
    """(label, activity id) for the media_player's source list: the offered
    activities in Control sort order, duplicates labelled apart."""
    offered = offered_activities(rows, off_id)
    labels = unique_labels(row.get("name") for row in offered)
    return [(label, _int(row.get("id"))) for label, row in zip(labels, offered)]


def activity_row(rows, activity_id) -> dict | None:
    activity_id = _int(activity_id)
    for row in rows or []:
        if _int(row.get("id")) == activity_id:
            return row
    return None


def transport_keys(row: dict | None) -> set[str]:
    """The transport keys the running activity uses (#570, the webapp remote's
    app_remote_key_shown). buttons null = not known: every key is offered."""
    buttons = (row or {}).get("buttons")
    if not isinstance(buttons, list):
        return {"play", "pause", "stop", "next", "previous"}
    keys = {k for k in TRANSPORT_KEYS if k in buttons}
    if (row or {}).get("play_pause") == "toggle" and "play_pause" in buttons:
        # One combined key on the device: it serves both play and pause.
        keys |= {"play", "pause", "play_pause"}
    return keys


def is_toggle(row: dict | None) -> bool:
    return (row or {}).get("play_pause") == "toggle"


# ---- volume -----------------------------------------------------------------


def volume_view(raw: dict) -> dict:
    """What can be done with the unit's volume, and what is KNOWN about it.

    ⚠ Being able to set the volume is not knowing the level. Before API 2.00 the
    Control answered volume 50 when it had never read a level, with
    volume_display null; since 2.00 it answers volume null with volume_max set.
    Either way the level is unknown and is shown as unknown, never as 50 -
    while set and step stay offered, because the device takes them anyway.
    """
    raw = raw or {}
    if not unit_on(raw):
        return {"can_set": False, "level": None, "muted": None, "display": None}
    volume = raw.get("volume")
    display = raw.get("volume_display")
    can_set = volume is not None or raw.get("volume_max") is not None
    level = None
    if volume is not None and display is not None:
        level = max(0, min(100, _int(volume))) / 100
    muted = raw.get("muted") if isinstance(raw.get("muted"), bool) else None
    return {"can_set": can_set, "level": level, "muted": muted if can_set else None,
            "display": display if can_set else None}


def volume_button(level: float) -> str:
    """HA's 0..1 -> the volume_<n> button the webapp slider sends."""
    return f"volume_{max(0, min(100, round(float(level) * 100)))}"


# ---- position and artwork ---------------------------------------------------


def position_reading(np: dict | None) -> dict | None:
    """The parts of Now Playing that decide whether a position is news.

    ⚠ The timestamp is the DAEMON's `updated` - when it read the position -
    never the poll time. Without it the position is not published at all.
    """
    if not np:
        return None
    updated = np.get("updated")
    try:
        updated = float(updated)
    except (TypeError, ValueError):
        updated = 0.0
    position = np.get("position")
    known = np.get("state") in (PLAYING, PAUSED) and updated > 0 and position is not None
    return {
        "state": np.get("state"),
        "content": (np.get("title"), np.get("artist"), np.get("album"), _int(np.get("duration"))),
        "position": _int(position) if known else None,
        "updated": updated if known else None,
    }


def position_is_news(prev: dict | None, new: dict | None) -> bool:
    """Publish this position? Only on a JUMP of more than 2 s from where the
    last one says it should be by now, so a steady play is not a recorder write
    every 5 s - but AT ONCE on pause or resume, a new title, unknown, or any
    jump when the content changed."""
    if prev is None or new is None:
        return prev is not new
    if new["position"] is None or prev["position"] is None:
        return new["position"] != prev["position"]
    if new["state"] != prev["state"] or new["content"] != prev["content"]:
        return True
    expected = prev["position"]
    if prev["state"] == PLAYING:
        expected += new["updated"] - prev["updated"]
    return abs(new["position"] - expected) > POSITION_JUMP_SECONDS


def artwork(kept, raw: dict) -> tuple[str | None, tuple | None]:
    """The picture to show, and what to keep for next time.

    Live cover first, else the idle source logo - both arrive as image_url
    (Michel's order, units.php). Only while on and not starting.
    ⚠ A stop or a gap keeps the last cover (feedback_keep_the_old_artwork): an
    empty image_url while the SAME activity still runs shows the kept one. It is
    keyed on the activity id - a new activity never inherits a cover because
    its URL or title happens to match.
    """
    np = now_playing(raw)
    if np is None:
        return None, None
    activity_id = display_activity(raw)
    url = str(np.get("image_url") or "")
    if url:
        return url, (activity_id, url)
    if kept and kept[0] == activity_id:
        return kept[1], kept
    return None, None


# ---- is this Control new enough? ---------------------------------------------

# Without these in /rooms and /zones the On/Off rule and "Starting..." cannot
# be read, so setup refuses rather than guess (plan G5: feature detection, not
# an invented version threshold).
REQUIRED_UNIT_FIELDS = ("on_activity", "off_activity", "starting_activity")


def too_old(rooms, zones) -> bool:
    """True when any room or zone lacks the fields the unit model rests on."""
    for row in list(rooms or []) + list(zones or []):
        if not isinstance(row, dict) or any(field not in row for field in REQUIRED_UNIT_FIELDS):
            return True
    return False


# ---- triggers for automations (S3b, #426) -----------------------------------
#
# Per unit, from one poll to the next: an activity STARTED, the room STOPPED
# (its Off), playback PAUSED, playback RESUMED - each with the activity's id
# and name, so "film starts in the Cinema -> curtains closed; pause -> lights
# 30 %" can be an automation.
#
# ⚠ Read from the RAW /rooms and /zones fields, as the predicates above are:
# - started = the Control PUBLISHING an activity other than Off as active
#   (active_activity), not the start request - its steps have run;
# - stopped = the published activity going from one that is not Off to the Off
#   activity (or to none) - the room went off. Switching to another activity
#   is that one starting, not a stop;
# - paused / resumed = Now Playing going playing -> paused / paused -> playing
#   while the SAME activity runs and nothing starts (media_state).
# Nothing fires without a previous look: not at the first poll, not after a
# reload.

EVENT = "artiled_control_event"

ACTIVITY_STARTING = "activity_starting"
ACTIVITY_STARTED = "activity_started"
ACTIVITY_STOPPED = "activity_stopped"
PAUSED = "paused"
RESUMED = "resumed"
TRIGGER_TYPES = (ACTIVITY_STARTING, ACTIVITY_STARTED, ACTIVITY_STOPPED, PAUSED, RESUMED)


def _name(rows, activity_id: int, raw: dict | None) -> str:
    row = activity_row(rows, activity_id)
    if row is not None:
        return str(row.get("name") or "")
    # Not in the inventory (yet): the Control's own name for it.
    if raw and _int(raw.get("active_activity")) == activity_id:
        return str(raw.get("activity_name") or "")
    if raw and starting(raw) == activity_id:
        return str(raw.get("starting_name") or "")
    return ""


def _starting_on(raw: dict) -> int:
    """The activity starting, when it is not the Off activity, else 0."""
    s = starting(raw)
    return s if s > 0 and s != _int(raw.get("off_activity")) else 0


def _running(raw: dict) -> int:
    """The published activity when it is not Off, else 0."""
    p = _int(raw.get("active_activity"))
    return p if p > 0 and p != _int(raw.get("off_activity")) else 0


def unit_events(prev: dict | None, raw: dict | None, rows) -> list[dict]:
    """The events between two looks at one unit's raw record."""
    if not prev or not raw:
        return []
    out: list[dict] = []
    before, now = _running(prev), _running(raw)
    was_starting, is_starting = _starting_on(prev), _starting_on(raw)
    # Activity STARTING (Michel 2026-10-26): a start the Control has taken
    # and is running the steps of (starting_activity), as soon as a poll sees
    # it. A start that fell between two polls is reported starting, then
    # started, so an automation on either never misses it.
    if is_starting and is_starting != was_starting:
        out.append({"type": ACTIVITY_STARTING, "activity_id": is_starting, "activity_name": _name(rows, is_starting, raw)})
    if now and now != before:
        if was_starting != now and is_starting != now:
            out.append({"type": ACTIVITY_STARTING, "activity_id": now, "activity_name": _name(rows, now, raw)})
        out.append({"type": ACTIVITY_STARTED, "activity_id": now, "activity_name": _name(rows, now, raw)})
        return out
    if before and not now and _int(raw.get("active_activity")) != before:
        out.append({"type": ACTIVITY_STOPPED, "activity_id": before, "activity_name": _name(rows, before, prev)})
        return out
    if now and now == before and starting(prev) == 0 and starting(raw) == 0:
        was, is_ = media_state(prev), media_state(raw)
        if was == "playing" and is_ == "paused":
            out.append({"type": PAUSED, "activity_id": now, "activity_name": _name(rows, now, raw)})
        elif was == "paused" and is_ == "playing":
            out.append({"type": RESUMED, "activity_id": now, "activity_name": _name(rows, now, raw)})
    return out


# ---- seek, shuffle and repeat (S3c, #426) ------------------------------------
#
# A port of the webapp remote's progress bar, shuffle and repeat
# (app/js/playback.js; app/api/playback.php -> player_door_handle, the same door
# as /api/unit_player): only while the unit's RUNNING activity plays a music
# service (app/remote/index.php $app_playback), shown as the PLAYER says.

PLAYBACK_READ_SECONDS = 15  # app/js/remote.js PLAYBACK_STATE_MS
REPEAT_MODES = ("off", "all", "one")


def playback_activity(raw: dict, rows) -> int:
    """The running activity whose player's shuffle, repeat and seek apply,
    or 0: on, nothing starting, and the activity plays a music service."""
    raw = raw or {}
    if not unit_on(raw) or starting(raw) > 0:
        return 0
    p = _int(raw.get("active_activity"))
    row = activity_row(rows, p)
    if row is None or not str(row.get("music_service") or ""):
        return 0
    return p


def playback_view(data) -> dict:
    """playback.js take(): the player's own words; anything else - and no
    answer at all - is unknown, never the old state (Mycroft PD1)."""
    data = data if isinstance(data, dict) else {}
    shuffle = data.get("shuffle")
    if shuffle in ("on", "off"):
        shuffle = shuffle == "on"
    repeat = data.get("repeat")
    return {
        "shuffle": shuffle if isinstance(shuffle, bool) else None,
        "repeat": repeat if repeat in REPEAT_MODES else None,
        "can_seek": data.get("can_seek") is True,
    }


def seek_seconds(position, duration) -> int:
    """playback.js seekSeconds: whole seconds, within the track."""
    d = max(0, int(_int(duration)))
    try:
        s = int(round(float(position)))
    except (TypeError, ValueError):
        s = 0
    return max(0, min(d, s))
