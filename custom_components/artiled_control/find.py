# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Browse = the webapp's Find Music / Find Movies (Michel 2026-10-26, 0.3.16).

The dashboard's Browse page is a copy of the webapp's Find pages: their tabs,
their requests, their pages. The browser never talks to the Control: it sends
the webapp's own request through Home Assistant's websocket
(artiled_control/find), and this forwards it to /unit_find (or
/unit_find_movies) for that room - READ actions only, the webapp's own
whitelist decides the rest on the Control.

- Which activity: the running one when it plays music (films) - with
  "expect", so a changed activity is refused, never answered for another
  player; else the room's first activity that does, named (the door serves it
  while nothing or the Off activity runs - API 2.02).
- Every row the Control marks playable ("play", API 2.02) gets a
  content_id that media_player.play_media plays (running-activity aware);
  a row without one is shown, not played - as the webapp shows it.
- Every picture goes through the room's own image proxy (no token, no
  address in what the browser sees).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api import ArtiControlError
from .cache import AFTER_PLAY, KeptCache, request_key, store
from .const import DOMAIN
from .media import artist_line, content_id, current_activity, play_shape, player_activities, player_of

_LOGGER = logging.getLogger(__name__)

WS_FIND = "artiled_control/find"
WS_SEARCH = "artiled_control/search"
# The webapp's Find Music READS (app/includes/music_proxy.php app_music_actions) a Browse page asks.
MUSIC_READS = frozenset({"recent", "favorites", "albums", "lists", "library", "browse", "search", "search_more",
                         "album", "accounts", "heos_account", "people_az", "cats",
                         # 0.3.21 the album / artist page (find-music.js openItem): Control's row, Roon's
                         # actions, something to read, four covers from inside, a Sonos favourite's contents
                         "about", "actions", "text", "covers", "sonos_about"})
# The ONE write a Browse page makes: the album page's play options (find-music.js api.play / api.track - Roon's
# own Play Now / Add Next / Queue / Start Radio, a service's address). Only while the room's music activity RUNS,
# and with "expect", so it can never reach another activity or player; with nothing running the page plays
# through media_player.play_media, which starts the activity first.
MUSIC_PLAYS = frozenset({"play_item", "play"})
# Find Movies' reads (app/includes/movie_proxy.php).
MOVIE_READS = frozenset({"titles", "people", "cats", "person"})
# The door's own fields: never the browser's to set.
_RESERVED = frozenset({"action", "zone", "room", "activity", "expect", "service", "target", "token", "csrf", "device", "player"})
_PICTURES = ("image", "image_url", "portrait", "cover", "large")
# 0.3.23 kept, then fresh (cache.py): a read answer is served as it is for a
# minute, then at once with its age while the page asks again (up to 6 h);
# a search, slow on the Control (3-7 s MEASURED), for 2 minutes / 30 minutes.
FIND_FRESH = 60
FIND_KEEP = 6 * 3600
SEARCH_FRESH = 120
SEARCH_KEEP = 1800


def _owner(data: dict):
    return data.get("ident") or id(data)


def find_cache(data: dict) -> KeptCache:
    """The Control's read cache (cache.store: kept across an entry reload)."""
    return store(_owner(data), "find", lambda: KeptCache(FIND_FRESH, FIND_KEEP))


def search_cache(data: dict) -> KeptCache:
    return store(_owner(data), "search",
                 lambda: KeptCache(SEARCH_FRESH, SEARCH_KEEP, max_entries=100, max_bytes=4 * 1024 * 1024))


def _now() -> float:
    import time  # noqa: PLC0415

    return time.monotonic()


def _clean(request: dict) -> dict:
    """The browser's request: plain values only, the door's own fields out."""
    out = {}
    for k, v in (request or {}).items():
        if not isinstance(k, str) or k in _RESERVED or not k.replace("_", "").isalpha() or len(k) > 40:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 8192):
            out[k] = v
    return out


def transform(value: Any, activity_id: int, player, movie_player: int = 0) -> Any:
    """Pictures through the room's proxy; a playable row gets its content_id."""
    if isinstance(value, list):
        return [transform(v, activity_id, player, movie_player) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k in _PICTURES and isinstance(v, str):
            out[k] = _picture(v, player)
        elif k == "covers" and isinstance(v, list):
            out[k] = [_picture(c, player) for c in v if isinstance(c, str)]
        elif k == "subtitle" and isinstance(v, str) and ("provider" in value or "play" in value or "key" in value):
            # a favourite's service / kind is never shown as its artist
            out[k] = artist_line(value)
        else:
            out[k] = transform(v, activity_id, player, movie_player)
    shape = play_shape(value.get("play")) if isinstance(value.get("play"), dict) else None
    if shape is not None:
        out["content_id"] = content_id(activity_id, shape)
        out["content_type"] = "movie" if shape["kind"] == "movie" else "music"
    elif movie_player and isinstance(value.get("id"), int) and isinstance(value.get("name"), str) and "on" in value:
        # a film of Find Movies' list: playable on THIS activity's player only
        if movie_player in [int(x) for x in value.get("on") or [] if isinstance(x, (int, str)) and str(x).isdigit()]:
            out["content_id"] = content_id(activity_id, {"kind": "movie", "title_id": value["id"], "title": value["name"]})
            out["content_type"] = "movie"
    return out


def _picture(url: str, player) -> str:
    if player is not None and (url.startswith("/api/unit_player_art?") or url.startswith("https://")):
        return player._thumbnail(url)
    return ""   # anything else the browser could not load


def register(hass: HomeAssistant) -> None:
    from homeassistant.components import websocket_api  # noqa: PLC0415

    @websocket_api.websocket_command({vol.Required("type"): WS_FIND, vol.Required("entity_id"): str,
                                      vol.Required("request"): dict, vol.Optional("movies"): bool,
                                      vol.Optional("fresh"): bool})
    @websocket_api.async_response
    async def ws_find(hass: HomeAssistant, connection, msg: dict) -> None:
        import sys  # noqa: PLC0415

        pkg = sys.modules[__package__]
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
        movies = bool(msg.get("movies"))
        request = _clean(msg["request"])
        action = str((msg["request"] or {}).get("action") or "")
        plays = not movies and action in MUSIC_PLAYS
        if action not in (MOVIE_READS if movies else MUSIC_READS) and not plays:
            connection.send_error(msg["id"], "not_allowed", f"{action or 'nothing'} is not a Browse read")
            return
        cdata = data["coordinator"].data or {}
        unit = (cdata.get("units") or {}).get(key) or {}
        rows = player_activities((cdata.get("offered") or {}).get(key))
        rows = [r for r in rows if (player_of(r)["movie_player"] > 0 if movies else player_of(r)["service"])]
        if not rows:
            connection.send_error(msg["id"], "not_supported", "This room plays no " + ("movies" if movies else "music"))
            return
        current = current_activity(unit.get("raw") or {})
        running = next((r for r in rows if int(r["id"]) == current), None)
        if plays and running is None:
            connection.send_error(msg["id"], "not_running", "Start the room's music first")
            return
        if running is not None:
            if not movies:
                request["expect"] = {"activity": int(running["id"]), "service": player_of(running)["service"]}
        else:
            request["activity"] = int(rows[0]["id"])
        request["action"] = action
        scope = {key.split("_")[0]: int(key.split("_")[1])}
        cache = find_cache(data)
        # what the answer is FOR: this unit, films or music, the activity it
        # runs (or the one named while idle), the exact request
        who = int(running["id"]) if running is not None else -int(rows[0]["id"])
        asks_now = str(request.get("fresh") or "") == "1"
        ckey = (key, movies, who, action, request_key({k: v for k, v in request.items() if k != "fresh"}))
        kept = 0
        hit = None if (plays or msg.get("fresh") or asks_now) else cache.get(ckey, _now())
        if hit is not None:
            answer, age = hit
            kept = 0 if cache.is_fresh(age) else max(1, int(age))
        else:
            try:
                answer = await (data["client"].unit_find_movies(scope, request) if movies
                                else data["client"].unit_find(scope, request))
            except ArtiControlError as err:
                connection.send_error(msg["id"], "control_error", str(err))
                return
            if plays:
                # a play changes what Recent says: never kept, and drops it
                cache.invalidate(lambda k: k[0] == key and k[3] in AFTER_PLAY)
                search_cache(data).invalidate(lambda k: k[0] == key)
            else:
                cache.put(ckey, answer, _now())
        served = int(answer.get("activity") or 0)
        row = next((r for r in rows if int(r["id"]) == served), None)
        player = (data.get("players") or {}).get(key)
        connection.send_result(msg["id"], {
            "service": str(answer.get("service") or ("movies" if movies else "")),
            "activity": served, "activity_name": str((row or {}).get("name") or ""),
            "idle": bool(answer.get("idle")),
            "kept": kept,
            "answer": transform(answer.get("answer"), served, player, player_of(row)["movie_player"] if (movies and row) else 0),
        })

    websocket_api.async_register_command(hass, ws_find)

    @websocket_api.websocket_command({vol.Required("type"): WS_SEARCH, vol.Required("entity_id"): str,
                                      vol.Required("query"): str, vol.Optional("movies"): bool,
                                      vol.Optional("fresh"): bool})
    @websocket_api.async_response
    async def ws_search(hass: HomeAssistant, connection, msg: dict) -> None:
        """The Browse page's quick search: the integration's own search (as
        search_media) with each result's ARTIST - the webapp's tile shows title
        and artist - and the activity that would play it."""
        import sys  # noqa: PLC0415

        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

        pkg = sys.modules[__package__]
        entity = er.async_get(hass).async_get(msg["entity_id"])
        data = key = None
        for d in hass.data.get(DOMAIN, {}).values():
            if isinstance(d, dict) and "ident" in d and entity is not None:
                parsed = pkg.parse_unit_uid(entity.unique_id, d["ident"])
                if parsed is not None:
                    data, key = d, f"{parsed[0]}_{parsed[1]}"
        player = (data or {}).get("players", {}).get(key) if data else None
        if player is None:
            connection.send_error(msg["id"], "not_found", "Not an ArtiCONTROL room")
            return
        classes = ["movie"] if msg.get("movies") else ["album", "playlist", "channel", "track", "artist"]
        cache = search_cache(data)
        unit = ((data["coordinator"].data or {}).get("units") or {}).get(key) or {}
        skey = (key, bool(msg.get("movies")), current_activity(unit.get("raw") or {}), " ".join(str(msg["query"]).lower().split()))
        hit = None if msg.get("fresh") else cache.get(skey, _now())
        kept = 0
        if hit is not None:
            found, age = hit
            kept = 0 if cache.is_fresh(age) else max(1, int(age))
        else:
            try:
                found = await player.search_results(msg["query"], classes)
            except HomeAssistantError as err:
                connection.send_error(msg["id"], "control_error", str(err))
                return
            cache.put(skey, found, _now())
        connection.send_result(msg["id"], {"kept": kept, "result": [{
            "title": r["title"], "subtitle": r["subtitle"], "image": player._thumbnail(r["image"]),
            "activity": r["activity"], "content_id": content_id(r["activity"], r["play"]), "content_type": r["content_type"],
        } for r in found]})

    websocket_api.async_register_command(hass, ws_search)
