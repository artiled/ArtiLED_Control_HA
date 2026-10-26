# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Voice favourites - "favourites are favourites in ANY room" (Michel
2026-10-26).

A heart of the Control (an album, a playlist, a film) under ONE spoken name
for the whole house - "Thriller". 0.3.25 (Michel 2026-10-26: "Favorites are
the same in all rooms ... but NOT in all services"; Mycroft #426 8712):
- the list is the same everywhere, GROUPED PER SERVICE: a record hearted on
  Roon and on Sonos is two favourites, each with its own service's play -
  never one service's id translated into another's (media.dedupe_per_service);
- from a room's page it plays in THAT room (play_in_room): on its running
  activity when that is the favourite's service, else the room switches to its
  activity of that service; no such activity: refused, nothing sent;
- said without a room (the scene - Home Assistant passes it no context): the
  room where something is playing now, only when exactly ONE plays
  (media.voice_room); none or several: refused with the reason. No "last
  started", no default room, no "first that can".

- add_voice_favorite / remove_voice_favorite: one by hand, from the Voice
  page's Favourites tab (one list for the whole Control) or an automation.
- set_voice_favorites_all: "Make all favourites available to voice" (entry
  data "all_favorites", off by default): every heart becomes one by itself on
  the inventory cycle; off: the automatic ones go, the hand-made stay.
- websocket artiled_control/favorites: the Control's hearts, one per
  favourite, with what is already chosen and the rooms that can play each.
- migrate: the per-room favourites of 0.3.9-0.3.11 become house-wide ones.

⚠ What the user did stands: the sync never removes one added by hand, never
adds back one the user removed (the excluded list), and - once-only voice
defaults - never re-exposes one the user un-exposed. A failed read removes
nothing. A hard cap (_FAVORITES_CAP in __init__) with a Repairs note naming
how many were left out.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.storage import Store

from .api import ArtiControlAuthError, ArtiControlError
from .cache import KeptCache, store
from .const import DOMAIN, STORAGE_VERSION
from .entity_ids import favorite_unique_id, room_favorite_unique_id
from .media import (SERVICE_NAMES, activity_in_room, artist_line, current_activity, dedupe, dedupe_per_service, owns,
                    play_shape, player_activities, player_of, record_key, record_title, service_of_play, voice_room)
from .units import media_state
from .voice import favorite_key, favorite_names, migrate_favorites, plan_auto, same_name, suggest_favorite_name

_LOGGER = logging.getLogger(__name__)

CONF_ALL_FAVORITES = "all_favorites"
# Which kinds "Make all favourites available to voice" is on for (Michel
# 2026-10-26: one switch per sub-tab). Absent with all_favorites on: albums
# and movies - what the one switch of 0.3.9-0.3.14 meant.
CONF_ALL_KINDS = "all_favorite_kinds"
TABS = ("albums", "playlists", "artists", "movies")
# Playlists and artists as a start's play: the Control's API 2.02.
API_PLAYLISTS = (2, 2)


def tab_of(kind, play: dict | None = None) -> str:
    """The Favourites sub-tab a favourite sits under: Album -> Albums,
    Playlist, Radio and Track (Sonos favourites) -> Playlists, Artist ->
    Artists, a film -> Movies; anything else music -> Albums."""
    if (play or {}).get("kind") == "movie" or kind == "Movie":
        return "movies"
    return {"Playlist": "playlists", "Radio": "playlists", "Track": "playlists", "Artist": "artists"}.get(str(kind or ""), "albums")


def kinds_on(entry) -> set:
    """The kinds the setting is on for."""
    data = (entry.data if entry is not None else {}) or {}
    kinds = data.get(CONF_ALL_KINDS)
    if isinstance(kinds, list):
        return {k for k in kinds if k in TABS}
    return {"albums", "movies"} if data.get(CONF_ALL_FAVORITES) else set()
CAPPED_ISSUE = "favorites_capped"
WS_FAVORITES = "artiled_control/favorites"
WS_PLAY_FAVORITE = "artiled_control/play_favorite"

_CONTROL = vol.Optional("control")
ADD_SCHEMA = vol.Schema({
    _CONTROL: str,
    vol.Required("play"): dict,
    vol.Optional("title"): str,
    vol.Optional("type"): str,
    vol.Optional("name"): str,
})
REMOVE_SCHEMA = vol.Schema({_CONTROL: str, vol.Optional("play"): dict, vol.Optional("name"): str})
ALL_SCHEMA = vol.Schema({_CONTROL: str, vol.Required("on"): bool, vol.Optional("kind"): vol.In(TABS)})


def _pkg():
    """The integration package (its helpers), without an import cycle."""
    import sys  # noqa: PLC0415

    return sys.modules[__package__]


def fav_uid(data: dict, fav: dict) -> str:
    return favorite_unique_id(data["ident"], favorite_key(fav["play"]))


def all_spoken(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Every stored spoken name of every Control: (name, owner id)."""
    out = []
    for d in hass.data.get(DOMAIN, {}).values():
        if not isinstance(d, dict) or "ident" not in d:
            continue
        for c in d.get("voice_activities") or []:
            out.append((c["name"], f"a|{d['ident']}|{c['kind']}_{c['id']}|{c['activity']}"))
        for f in d.get("voice_favorites") or []:
            if f.get("name"):
                out.append((f["name"], f"f|{d['ident']}|{favorite_key(f['play'])}"))
    return out


def _in_use(hass: HomeAssistant, name: str, own: str | None, own_entity: str | None, extra=()) -> bool:
    if any(same_name(n, name) and o != own for n, o in all_spoken(hass)):
        return True
    if any(same_name(n, name) for n in extra):
        return True
    return _pkg()._name_in_use(hass, name, own_entity)


async def save(hass: HomeAssistant, data: dict, reload: bool = True) -> None:
    """Persist the WHOLE voice store (activities, favourites, excluded), then
    reload so the scenes follow."""
    await data["voice_store"].async_save({
        "activities": data.get("voice_activities") or [],
        "favorites": data.get("voice_favorites") or [],
        "excluded": sorted(data.get("voice_excluded") or []),
    })
    if reload:
        await hass.config_entries.async_reload(data["entry_id"])


async def retire_uids(hass: HomeAssistant, data: dict, wanted: set[str]) -> None:
    """Un-expose FIRST, remove, and drop the deleted-entry record."""
    if not wanted:
        return
    pkg = _pkg()
    registry = er.async_get(hass)
    doomed = [e.entity_id for e in er.async_entries_for_config_entry(registry, data["entry_id"]) if e.unique_id in wanted]
    if pkg._unexpose(hass, doomed):
        await asyncio.sleep(0)
    for entity_id in doomed:
        registry.async_remove(entity_id)
    await pkg._forget_voice_entities(hass, data["entry_id"], wanted)


async def retire(hass: HomeAssistant, data: dict, gone: list[dict]) -> None:
    await retire_uids(hass, data, {fav_uid(data, f) for f in gone})


def _rooms(data: dict) -> list[dict]:
    """The offered rooms as media.voice_room / activity_in_room read them."""
    cdata = data["coordinator"].data or {}
    out = []
    for key in data["shown_units"]:
        unit = (cdata.get("units") or {}).get(key)
        if unit is None or unit["hidden"]:
            continue
        raw = unit["raw"]
        changed, started = data["coordinator"].room_times(key)
        out.append({"key": key, "name": (cdata.get("names") or {}).get(key) or key,
                    "rows": (cdata.get("offered") or {}).get(key) or [],
                    "current": current_activity(raw), "playing": media_state(raw) == "playing",
                    "changed": changed, "last_start": started})
    return out


def rooms_for(data: dict, play: dict) -> list[str]:
    """The rooms with an activity whose player plays it, by name."""
    return [u["name"] for u in _rooms(data) if any(owns(r, play) for r in u["rows"])]


def _service_of(data: dict, play: dict) -> str:
    """The service a favourite plays on, for its name ("Thriller on Roon")."""
    for u in _rooms(data):
        for r in u["rows"]:
            if owns(r, play):
                return player_of(r)["service"] or "movies"
    return ""


async def play_favorite(hass: HomeAssistant, data: dict, play: dict, title: str) -> tuple[str, int, str]:
    """A favourite said without a room (its scene): the ONE room playing now
    (media.voice_room). -> (room name, activity, the API's play state).
    Refused, nothing sent, when none or several play, or that room has no
    activity for the favourite's service."""
    rooms = _rooms(data)
    key, activity_id, why = voice_room(rooms, play)
    if not key:
        raise HomeAssistantError(f"{title or 'That favourite'}: {why} - nothing was sent")
    name = next(u["name"] for u in rooms if u["key"] == key)
    from .media_player import play_on_unit  # noqa: PLC0415 - media_player is loaded with the platforms

    used, state = await play_on_unit(data, key, name, activity_id, play)
    return name, used, state


async def play_in_room(hass: HomeAssistant, data: dict, key: str, play: dict, title: str) -> tuple[str, int, str]:
    """A favourite tapped on a room's page: in THAT room (media.activity_in_room)
    - on its running activity when that plays the favourite's service, else the
    room is switched to its activity that does (the tap is the choice). None:
    refused, nothing sent."""
    room = next((u for u in _rooms(data) if u["key"] == key), None)
    if room is None:
        raise HomeAssistantError("That room is not offered on the Control")
    activity_id = activity_in_room(room, play)
    if not activity_id:
        service = SERVICE_NAMES.get(service_of_play(play), "its service")
        raise HomeAssistantError(f"{room['name']} has no activity for {service} - nothing was sent")
    from .media_player import play_on_unit  # noqa: PLC0415

    used, state = await play_on_unit(data, key, room["name"], activity_id, play)
    return room["name"], used, state


async def hearts_of(data: dict, row: dict) -> list[dict]:
    """One activity's hearts (/activity_favorites). Error 7 = it plays nothing
    with favourites: none. Anything else is raised - a failed read."""
    try:
        return await data["client"].activity_favorites(int(row["id"]))
    except ArtiControlError as err:
        if err.code == 7 and not isinstance(err, ArtiControlAuthError):
            return []
        raise


# 0.3.24 kept, then fresh for the hearts too (cache.py): MEASURED 300-670 ms a
# player's /activity_favorites, read one after the other for every player of
# the house - seconds before the Favourites list could draw.
HEARTS_FRESH = 60
HEARTS_KEEP = 6 * 3600


def hearts_cache(data: dict) -> KeptCache:
    return store(data.get("ident") or id(data), "hearts", lambda: KeptCache(HEARTS_FRESH, HEARTS_KEEP))


def _now() -> float:
    return time.monotonic()


async def house_hearts(data: dict, fresh: bool = False) -> tuple[list[dict], bool, list[str]]:
    hearts, complete, errors, _ = await house_hearts_kept(data, fresh)
    return hearts, complete, errors


async def house_hearts_kept(data: dict, fresh: bool = False) -> tuple[list[dict], bool, list[str], int]:
    """Every heart of the Control's offered rooms, ONE per favourite (its play,
    and its album / film: media.dedupe - the running activity's copy, else the
    first in Control order). Each player's list kept-then-fresh, the players
    read side by side. -> (hearts, every list answered, errors, age in seconds
    of the oldest kept list served - 0 when all were fresh)"""
    rooms = _rooms(data)
    cache = hearts_cache(data)
    by_player: dict[tuple, Any] = {}
    found: list[dict] = []
    errors: list[str] = []
    order: list[int] = []
    running = 0
    kept = 0
    first_row: dict[tuple, dict] = {}
    for u in rooms:
        for row in player_activities(u["rows"]):
            player = player_of(row)
            first_row.setdefault((player["service"], player["where"], player["movie_player"]), row)

    async def read(ident, row):
        nonlocal kept
        hit = None if fresh else cache.get(ident, _now())
        if hit is not None:
            if not cache.is_fresh(hit[1]):
                kept = max(kept, int(hit[1]))
            return hit[0]
        try:
            value = await hearts_of(data, row)
        except ArtiControlError as err:
            errors.append(f"{row.get('name')}: {err}")
            return None
        cache.put(ident, value, _now())
        return value

    idents = list(first_row)
    answers = await asyncio.gather(*(read(i, first_row[i]) for i in idents))
    by_player = dict(zip(idents, answers))
    for u in rooms:
        for row in player_activities(u["rows"]):
            aid = int(row["id"])
            order.append(aid)
            if u["playing"] and u["current"] == aid:
                running = running or aid
            player = player_of(row)
            ident = (player["service"], player["where"], player["movie_player"])
            for it in by_player.get(ident) or []:
                play = play_shape((it or {}).get("play"))
                if play is None or not owns(row, play) or not str(it.get("title") or "").strip():
                    continue
                found.append({"activity": aid, "play": play, "title": str(it["title"]),
                              "subtitle": artist_line(it), "type": str(it.get("type") or ""),
                              "image": str(it.get("image") or ""), "service": player["service"] or "movies",
                              "content_type": "movie" if play["kind"] == "movie" else "music"})
    return dedupe_per_service(found, running, order), not errors, errors, kept


# ---- services ----------------------------------------------------------------


def register(hass: HomeAssistant) -> None:
    pkg = _pkg()

    async def add_voice_favorite(call: ServiceCall) -> None:
        data = pkg.entry_for_call(hass, call)
        play = play_shape(call.data["play"])
        if play is None:
            raise ServiceValidationError("That is not a play the Control accepts")
        if not rooms_for(data, play):
            raise ServiceValidationError("No room of this Control plays that")
        key = favorite_key(play)
        own = f"f|{data['ident']}|{key}"
        own_entity = er.async_get(hass).async_get_entity_id("scene", DOMAIN, favorite_unique_id(data["ident"], key))
        # named by the RECORD's plain title, not an edition's (one heart = one record)
        title = record_title((call.data.get("title") or play.get("title") or "").strip())
        service = _service_of(data, play)
        spoken = (call.data.get("name") or "").strip() or suggest_favorite_name(
            title, call.data.get("type"), service, lambda n: _in_use(hass, n, own, own_entity))
        if not spoken:
            raise ServiceValidationError(f"Every name for {title} is taken - give one")
        if any(same_name(n, spoken) and o != own for n, o in all_spoken(hass)):
            raise ServiceValidationError(f"'{spoken}' is already a voice command - choose another name")
        prev = next((f for f in data.get("voice_favorites") or [] if favorite_key(f["play"]) == key), {})
        fav = {"play": play, "name": spoken, "auto": False, "title": title,   # by hand: the sync never removes it
               "type": str(call.data.get("type") or prev.get("type") or "")}
        data["voice_favorites"] = [f for f in data.get("voice_favorites") or [] if favorite_key(f["play"]) != key] + [fav]
        data["voice_excluded"] = set(data.get("voice_excluded") or set()) - {key}
        # ⚠ In place: the store, then this one scene - never a reload of the entry
        await save(hass, data, reload=False)
        from .scene import ArtiControlVoiceFavorite, place  # noqa: PLC0415

        await place(hass, data, ArtiControlVoiceFavorite(data, fav))
        names = favorite_names(title, call.data.get("type"), service)
        pkg._add_long_alias(hass, fav_uid(data, fav), spoken, names[1] if len(names) > 1 else "",
                            lambda n: _in_use(hass, n, own, own_entity))

    async def remove_voice_favorite(call: ServiceCall) -> None:
        data = pkg.entry_for_call(hass, call)
        favs = data.get("voice_favorites") or []
        if call.data.get("name") and "play" not in call.data:
            gone = [f for f in favs if same_name(f.get("name"), call.data["name"])]
        elif "play" in call.data:
            wanted = favorite_key(call.data["play"])
            gone = [f for f in favs if favorite_key(f["play"]) == wanted]
        else:
            raise ServiceValidationError("Give the spoken name, or its play")
        if not gone:
            raise ServiceValidationError("No voice favourite matches that")
        await retire(hass, data, gone)
        data["voice_favorites"] = [f for f in favs if f not in gone]
        # An automatic one the user removed is not added back by the sync.
        data["voice_excluded"] = set(data.get("voice_excluded") or set()) | {
            favorite_key(f["play"]) for f in gone if f.get("auto")}
        for f in gone:
            (data.get("scenes") or {}).pop(fav_uid(data, f), None)
        await save(hass, data, reload=False)   # its registry entry (and so its entity) is gone already

    async def set_voice_favorites_all(call: ServiceCall) -> None:
        await set_all(hass, pkg.entry_for_call(hass, call), bool(call.data["on"]), call.data.get("kind"))

    hass.services.async_register(DOMAIN, "add_voice_favorite", add_voice_favorite, schema=ADD_SCHEMA)
    hass.services.async_register(DOMAIN, "remove_voice_favorite", remove_voice_favorite, schema=REMOVE_SCHEMA)
    hass.services.async_register(DOMAIN, "set_voice_favorites_all", set_voice_favorites_all, schema=ALL_SCHEMA)
    _register_ws(hass)


# ---- the setting and the automatic sync ------------------------------------------


async def set_all(hass: HomeAssistant, data: dict, on: bool, kind: str | None = None) -> None:
    """The setting, per kind (Albums, Playlists, Artists, Movies) or for all
    of them (kind None: Configure's one tick). Stored on the entry FIRST and
    checked (live 0.3.13: switched off, the entry still said on). On: a sync
    now (then every inventory round). Off: at once, the automatic ones of that
    kind go - un-exposed first, removed, deleted records dropped - and those
    added by hand stay. In place: no reload."""
    entry = hass.config_entries.async_get_entry(data["entry_id"])
    kinds = kinds_on(entry)
    changed = set(TABS) if kind is None else {kind}
    kinds = (kinds | changed) if on else (kinds - changed)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_ALL_KINDS: sorted(kinds),
                                                        CONF_ALL_FAVORITES: bool(kinds)})
    if kinds_on(entry) != kinds:
        raise HomeAssistantError("The setting could not be stored")
    _LOGGER.info("Make all favourites available to voice: %s", ", ".join(sorted(kinds)) or "off")
    if on:
        await sync_auto(hass, data)
        return
    if not kinds:
        ir.async_delete_issue(hass, DOMAIN, f"{CAPPED_ISSUE}_{data['entry_id']}")
    auto = [f for f in data.get("voice_favorites") or [] if f.get("auto") and tab_of(f.get("type"), f["play"]) not in kinds]
    if auto:
        await retire(hass, data, auto)
        for f in auto:
            (data.get("scenes") or {}).pop(fav_uid(data, f), None)
        data["voice_favorites"] = [f for f in data.get("voice_favorites") or [] if not any(f is a for a in auto)]
        await save(hass, data, reload=False)
        _LOGGER.info("Removed %s automatic voice favourites", len(auto))


async def sync_auto(hass: HomeAssistant, data: dict) -> None:
    """One round of "Make all favourites available to voice": one scene per
    favourite of the whole Control."""
    pkg = _pkg()
    entry = hass.config_entries.async_get_entry(data["entry_id"])
    if entry is None or not entry.data.get(CONF_ALL_FAVORITES) or data.get("retired"):
        return
    hearts, complete, _ = await house_hearts(data, fresh=True)
    # ⚠ Switched off, reloaded or removed while the hearts were read: this
    # round's answer is not written over the new state.
    if not entry.data.get(CONF_ALL_FAVORITES) or data.get("retired") \
            or hass.data.get(DOMAIN, {}).get(data["entry_id"]) is not data:
        return
    # only the kinds the setting is on for (an automatic one of a kind switched
    # off is not "gone": set_all removes those)
    kinds = kinds_on(entry)
    hearts = [h for h in hearts if tab_of(h.get("type"), h["play"]) in kinds]
    # A heart of a record that already has its favourite (another edition of
    # it) IS that favourite: not a second scene, and not "gone".
    def mine(h):
        return next((f for f in data.get("voice_favorites") or [] if _same_record(f, h)), None)
    hearts = [{**h, "play": mine(h)["play"]} if mine(h) is not None else h for h in hearts]
    existing = data.get("voice_favorites") or []
    plan = plan_auto(hearts, existing, data.get("voice_excluded") or set(), pkg._FAVORITES_CAP, complete)
    plan["remove"] = [f for f in plan["remove"] if tab_of(f.get("type"), f["play"]) in kinds]
    named: list[dict] = []
    taken: list[str] = []
    left_out = plan["left_out"]
    for fav in plan["add"]:
        own = f"f|{data['ident']}|{favorite_key(fav['play'])}"
        name = suggest_favorite_name(record_title(fav["title"]), fav["type"], fav["service"],
                                     lambda n, own=own: _in_use(hass, n, own, None, taken))
        if name is None:
            left_out += 1
            continue
        taken.append(name)
        named.append({"play": fav["play"], "name": name, "auto": True, "title": fav["title"], "type": fav["type"]})
    issue = f"{CAPPED_ISSUE}_{data['entry_id']}"
    if left_out:
        ir.async_create_issue(hass, DOMAIN, issue, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                              translation_key=CAPPED_ISSUE,
                              translation_placeholders={"name": data.get("control_name") or "",
                                                        "left_out": str(left_out), "cap": str(pkg._FAVORITES_CAP)})
    else:
        ir.async_delete_issue(hass, DOMAIN, issue)
    if not named and not plan["remove"]:
        return
    if plan["remove"]:
        await retire(hass, data, plan["remove"])
        for f in plan["remove"]:
            (data.get("scenes") or {}).pop(fav_uid(data, f), None)
    data["voice_favorites"] = [f for f in data.get("voice_favorites") or [] if f not in plan["remove"]] + named
    # In place - one add for all the new ones, no reload (Michel 2026-10-26)
    await save(hass, data, reload=False)
    from .scene import ArtiControlVoiceFavorite, place_many  # noqa: PLC0415

    await place_many(hass, data, [ArtiControlVoiceFavorite(data, f) for f in named])


# ---- one heart = one record --------------------------------------------------------


def record_of(fav: dict) -> tuple:
    """Which record a favourite is (feedback_one_heart_is_one_record): its
    plain title as compared (media.record_key) and music or movie."""
    play = fav.get("play") or {}
    return (record_key(fav.get("title") or play.get("title") or ""), play.get("kind"))


def _same_record(fav: dict, heart: dict) -> bool:
    """A heart is this favourite's record: the same plain title, music or
    movie, and - when the favourite knows it - the same kind (an album and a
    playlist of one name are two)."""
    return record_of(fav) == record_of(heart) and (not fav.get("type") or fav.get("type") == heart.get("type"))


async def merge_records(hass: HomeAssistant, entry, data: dict) -> None:
    """0.3.13 made a scene per EDITION ("Antarctica", "Antarctica
    (Remastered)", "Antarctica [Original Motion Picture Soundtrack]"; 132 on
    the master, all with Alexa). One per record now, in place, at setup - no
    reload: the plain-title one stays (its entity id, assistants and
    aliases), the others' names become its aliases and they are removed
    cleanly (un-exposed first: no ghost device in Alexa; deleted record
    dropped). Two the user added BY HAND are never merged into each other."""
    favs = list(data.get("voice_favorites") or [])
    groups: dict[tuple, list[dict]] = {}
    for f in favs:
        groups.setdefault(record_of(f) + (str(f.get("type") or ""),), []).append(f)
    registry = er.async_get(hass)
    gone: list[dict] = []
    for members in groups.values():
        if len(members) < 2 or not members[0]["play"]:
            continue
        hand = [f for f in members if not f.get("auto")]
        if len(hand) > 1:
            continue
        plain = [f for f in members if record_title(f.get("title") or f["play"].get("title")) == (f.get("title") or f["play"].get("title"))]
        keeper = hand[0] if hand else (plain[0] if plain else members[0])
        losers = [f for f in members if f is not keeper]
        keep_id = registry.async_get_entity_id("scene", DOMAIN, fav_uid(data, keeper))
        if keep_id:
            current = registry.async_get(keep_id).aliases
            names = [str(f.get("name") or "") for f in losers if str(f.get("name") or "").strip()]
            if isinstance(current, (set, frozenset)):
                registry.async_update_entity(keep_id, aliases=set(current) | set(names))
            else:
                computed = getattr(er, "COMPUTED_NAME", None)
                base = list(current) if current else ([computed] if computed is not None else [])
                registry.async_update_entity(keep_id, aliases=base + [n for n in names if n not in base])
        gone += losers
    if not gone:
        return
    await retire(hass, data, gone)
    data["voice_favorites"] = [f for f in favs if not any(f is g for g in gone)]
    await save(hass, data, reload=False)
    _LOGGER.info("One heart = one record: %s edition scenes merged into their record's", len(gone))


# ---- the migration from the per-room favourites (0.3.9-0.3.11) ---------------------


async def migrate(hass: HomeAssistant, entry, data: dict) -> None:
    """Once, for a store of 0.3.9-0.3.11: each favourite's FIRST room copy
    becomes the house-wide one - its registry entry moved to the new
    unique_id, so its entity id, aliases and exposure stay - and the other
    rooms' copies are removed cleanly. A name the user typed is kept; a
    generated "<title> in the <room>" becomes the house-wide suggestion."""
    stored = data.get("voice_favorites") or []
    if not any("kind" in f for f in stored):
        return
    names = (data["coordinator"].data or {}).get("names") or {}

    def generated(f) -> bool:
        room = str(names.get(f"{f['kind']}_{int(f['id'])}") or "").strip().lower()
        n = str(f.get("name") or "").strip().lower()
        return bool(room) and (n.endswith(f" in the {room}") or n.endswith(f" in {room}"))

    plan = migrate_favorites(stored, generated)
    registry = er.async_get(hass)
    ident = data["ident"]
    flags_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_flags")
    flags: dict = await flags_store.async_load() or {}
    defaults = list(flags.get("voice_defaults") or [])
    for old, _new in plan["moved"]:
        key = favorite_key(old["play"])
        old_uid = room_favorite_unique_id(ident, old["kind"], int(old["id"]), key)
        new_uid = favorite_unique_id(ident, key)
        entity_id = registry.async_get_entity_id("scene", DOMAIN, old_uid)
        if entity_id:
            registry.async_update_entity(entity_id, new_unique_id=new_uid)
        # its exposure was set once already: the new id counts as done
        defaults = [new_uid if uid == old_uid else uid for uid in defaults]
    await retire_uids(hass, data, {room_favorite_unique_id(ident, f["kind"], int(f["id"]), favorite_key(f["play"]))
                                   for f in plan["retired"]})
    flags["voice_defaults"] = defaults
    await flags_store.async_save(flags)
    favs = plan["favorites"]
    for f in favs:
        if not f.get("name"):
            f["name"] = suggest_favorite_name(
                f.get("title") or f["play"].get("title"), "", _service_of(data, f["play"]),
                lambda n, me=f: any(same_name(n, x.get("name")) for x in favs if x is not me)) \
                or f["play"].get("title") or "Favourite"
    data["voice_favorites"] = favs
    # an exclusion was "<unit>|<key>": now the key alone
    data["voice_excluded"] = {k.split("|", 1)[1] if k.split("|", 1)[0].startswith(("room_", "zone_")) else k
                              for k in data.get("voice_excluded") or set()}
    await save(hass, data, reload=False)
    _LOGGER.info("Voice favourites are house-wide now: %s kept, %s room copies removed",
                 len(plan["moved"]), len(plan["retired"]))


# ---- the Voice page's list -----------------------------------------------------------


def _register_ws(hass: HomeAssistant) -> None:
    from homeassistant.components import websocket_api  # noqa: PLC0415

    @websocket_api.websocket_command({vol.Required("type"): WS_FAVORITES, vol.Required("entity_id"): str,
                                      vol.Optional("fresh"): bool})
    @websocket_api.async_response
    async def ws_favorites(hass: HomeAssistant, connection, msg: dict) -> None:
        pkg = _pkg()
        registry = er.async_get(hass)
        entity = registry.async_get(msg["entity_id"])
        data = None
        for d in hass.data.get(DOMAIN, {}).values():
            if isinstance(d, dict) and "ident" in d and entity is not None \
                    and pkg.parse_unit_uid(entity.unique_id, d["ident"]) is not None:
                data = d
        if data is None:
            connection.send_error(msg["id"], "not_found", "Not an ArtiCONTROL room")
            return
        entry = hass.config_entries.async_get_entry(data["entry_id"])
        hearts, _, errors, kept = await house_hearts_kept(data, bool(msg.get("fresh")))
        chosen = {favorite_key(f["play"]): f for f in data.get("voice_favorites") or []}
        excluded = data.get("voice_excluded") or set()
        reg_by_uid = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(registry, data["entry_id"])}
        player = next(iter((data.get("players") or {}).values()), None)
        # 0.3.25 the room asked from: which favourites IT can play (its own
        # activities of the favourite's service) - a room page dims the rest
        parsed = pkg.parse_unit_uid(entity.unique_id, data["ident"])
        here_key = f"{parsed[0]}_{parsed[1]}" if parsed else ""
        here_rows = next((u["rows"] for u in _rooms(data) if u["key"] == here_key), [])
        items = []
        for h in hearts:
            k = favorite_key(h["play"])
            fav = chosen.get(k)
            own = f"f|{data['ident']}|{k}"
            items.append({
                "tab": tab_of(h["type"], h["play"]),
                "play": h["play"], "title": h["title"], "subtitle": h["subtitle"], "type": h["type"],
                "service": h["service"], "rooms": rooms_for(data, h["play"]),
                "image": player._thumbnail(h["image"]) if (player is not None and h["image"]) else "",
                "suggest": suggest_favorite_name(record_title(h["title"]), h["type"], h["service"],
                                                 lambda n, own=own: _in_use(hass, n, own, None)) or "",
                "chosen": None if fav is None else {
                    "name": fav["name"], "auto": bool(fav.get("auto")), "entity_id": reg_by_uid.get(fav_uid(data, fav))},
                "excluded": k in excluded,
                "here": any(owns(r, h["play"]) for r in here_rows),
            })
        api = getattr(data["client"], "api", None)
        has = {it["tab"] for it in items}
        tabs = [t for t in TABS if t in ("albums", "movies") or t in has or (api is not None and api >= API_PLAYLISTS)]
        # 0.3.25 the services, in the order their favourites come: the list's
        # first level (Roon, Sonos, Spotify, ...)
        services = []
        for it in items:
            if it["service"] not in services:
                services.append(it["service"])
        connection.send_result(msg["id"], {"items": items, "errors": errors, "tabs": tabs, "kept": kept,
                                           "services": [{"id": sv, "name": SERVICE_NAMES.get(sv, sv.title())} for sv in services],
                                           "kinds_on": sorted(kinds_on(entry)),
                                           "all_favorites": bool(kinds_on(entry))})

    websocket_api.async_register_command(hass, ws_favorites)

    @websocket_api.websocket_command({vol.Required("type"): WS_PLAY_FAVORITE, vol.Required("entity_id"): str,
                                      vol.Required("play"): dict})
    @websocket_api.async_response
    async def ws_play_favorite(hass: HomeAssistant, connection, msg: dict) -> None:
        """A favourite tapped on a room's page: played in THAT room
        (play_in_room) - never another room, never another service's copy."""
        pkg = _pkg()
        entity = er.async_get(hass).async_get(msg["entity_id"])
        data = key = None
        for d in hass.data.get(DOMAIN, {}).values():
            if isinstance(d, dict) and "ident" in d and entity is not None:
                parsed = pkg.parse_unit_uid(entity.unique_id, d["ident"])
                if parsed is not None:
                    data, key = d, f"{parsed[0]}_{parsed[1]}"
        if data is None or key not in data["shown_units"]:
            connection.send_error(msg["id"], "not_found", "Not an ArtiCONTROL room")
            return
        play = play_shape(msg["play"])
        if play is None:
            connection.send_error(msg["id"], "invalid", "That is not a play the Control accepts")
            return
        try:
            room, activity, state = await play_in_room(hass, data, key, play, str(play.get("title") or ""))
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "not_played", str(err))
            return
        connection.send_result(msg["id"], {"room": room, "activity": activity, "state": state})

    websocket_api.async_register_command(hass, ws_play_favorite)
