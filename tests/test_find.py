#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for Browse = the webapp's Find Music / Find Movies (0.3.16).

Run:  python3 tests/test_find.py     (exit 0 = all pass)

The Browse page sends the webapp's own Find requests through Home Assistant's
websocket (artiled_control/find); the integration forwards READS only to
/unit_find (/unit_find_movies) for that room - the running activity's player
with "expect", or its first music (movie) activity named - and hands the
answer back with every picture through the room's proxy and a content_id on
every row the Control marks playable (API 2.02 "play").
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
media = S.load_pure("media")
ART = "/api/unit_player_art?kind=roon&key=abc&v=2"


async def main() -> None:
    control = S.house(S.FakeControl())
    rows = {a["id"]: a for a in control.activities}
    rows[120].update(music_service="roon", music_where="Secondary", movie_player=0)
    rows[112].update(music_service="", music_where="", movie_player=7)
    for a in control.activities:
        a.setdefault("music_service", "")
        a.setdefault("music_where", "")
        a.setdefault("movie_player", 0)
    album = {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}
    hit = {"kind": "music", "source": "roon_item", "title": "Bad",
           "key": json.dumps({"kind": "album", "path": [{"root": "search", "input": "bad"}, {"title": "Albums"}, {"title": "Bad", "subtitle": "MJ"}]})}
    control.find_answers[("roon", "recent")] = {"sections": [
        {"title": "Recently played", "items": [{"id": 5, "title": "Thriller", "subtitle": "MJ", "image": ART, "play": album, "path": [{"x": 1}]},
                                               {"title": "Genres", "image": "/app/api/image.php?key=x", "path": [{"root": "genres"}]}]},
        {"title": "Recently added", "items": [{"title": "Bad", "subtitle": "MJ", "image": "https://i.scdn.co/image/b", "play": hit}]}],
        "error": ""}
    control.film_titles = [{"id": 9, "name": "Alien", "year": 1979, "image": "/api/unit_player_art?kind=movie_cover&id=9", "on": [7]},
                           {"id": 10, "name": "Blade Runner", "year": 1982, "image": "/api/unit_player_art?kind=movie_cover&id=10", "on": [8]}]
    hass, entry, control = await S.setup_integration(control)

    async def ask(entity, request, movies=False):
        conn = S.FakeConnection()
        msg = {"id": 1, "type": "artiled_control/find", "entity_id": entity, "request": request}
        if movies:
            msg["movies"] = True
        await hass.ws["artiled_control/find"](hass, conn, msg)
        return conn.results.get(1), conn.errors.get(1)

    print("the webapp's Find request, forwarded for the room")
    control.requests.clear()
    res, err = await ask("media_player.secondary", {"action": "recent", "zone": 99, "service": "sonos", "activity": 1})
    asked = [d for e, d in control.requests if e == "unit_find"]
    check("one /unit_find: the room's own unit, the webapp's action", len(asked) == 1 and asked[0].get("zone") == 12
          and asked[0].get("action") == "recent", str(asked))
    check("nothing runs: its music activity named; the browser's zone / service / activity never passed on",
          asked and asked[0].get("activity") == 120 and "service" not in asked[0], str(asked))
    sec = (res or {}).get("answer", {}).get("sections") or []
    first = sec[0]["items"][0] if sec else {}
    check("a row the Control marks playable gets the content_id play_media plays",
          media.parse_content_id(first.get("content_id")) == (120, album), str(first))
    check("a catalogue hit (roon_item, API 2.02) too", media.parse_content_id(sec[1]["items"][0].get("content_id"))[1]["source"] == "roon_item")
    check("a folder without a play: shown, not playable", "content_id" not in sec[0]["items"][1])
    urls = [first.get("image"), sec[1]["items"][0].get("image"), sec[0]["items"][1].get("image")]
    check("pictures through the room's proxy; one the browser could not load is dropped; never the token",
          urls[0].startswith("/api/media_player_proxy/media_player.secondary/") and urls[1].startswith("/api/media_player_proxy/")
          and urls[2] == "" and all(S.TOKEN not in u for u in urls), str(urls))
    check("the answer says the service and the activity", res.get("service") == "roon" and res.get("activity_name") == "Listen to Roon")

    control.zones[1]["active_activity"] = 120
    await S.poll(hass)
    control.requests.clear()
    await ask("media_player.secondary", {"action": "albums", "offset": 0, "q": "x"})
    asked = [d for e, d in control.requests if e == "unit_find"]
    check("it runs: expect names it, nothing else is named", asked and asked[0].get("expect") == {"activity": 120, "service": "roon"}
          and "activity" not in asked[0], str(asked))

    control.requests.clear()
    item = json.dumps({"path": [{"root": "artists"}, {"title": "MJ"}, {"title": "Bad"}]})
    res, err = await ask("media_player.secondary", {"action": "play_item", "item": item, "do": "Add Next"})
    asked = [d for e, d in control.requests if e == "unit_find"]
    check("0.3.21: the album page's play options (play_item, Roon's own action names) WHILE the activity runs - with expect",
          err is None and asked and asked[0].get("action") == "play_item" and asked[0].get("do") == "Add Next"
          and asked[0].get("item") == item and asked[0].get("expect") == {"activity": 120, "service": "roon"}, str((err, asked)))
    control.requests.clear()
    res, err = await ask("media_player.secondary", {"action": "favorite", "id": 5, "do": "1"})
    check("any other write is not a Browse action: refused, nothing sent", err is not None and err[0] == "not_allowed"
          and not [1 for e, _ in control.requests if e == "unit_find"])
    for read in ("about", "actions", "text", "covers", "sonos_about"):
        control.requests.clear()
        res, err = await ask("media_player.secondary", {"action": read, "path": "[]"})
        check(f"the album page's read '{read}' is forwarded", err is None and [1 for e, _ in control.requests if e == "unit_find"], str(err))
    control.zones[1]["active_activity"] = 0
    await S.poll(hass)
    control.requests.clear()
    res, err = await ask("media_player.secondary", {"action": "play_item", "item": item, "do": "Play Now"})
    check("nothing runs: play_item refused (the page plays through play_media, which starts the activity), nothing sent",
          err is not None and err[0] == "not_running" and not [1 for e, _ in control.requests if e == "unit_find"], str(err))
    control.zones[1]["active_activity"] = 120
    await S.poll(hass)
    res, err = await ask("media_player.bedroom_main", {"action": "recent"})
    check("a room that plays no music: said", err is not None and err[0] == "not_supported")

    find = sys.modules["artiled_control.find"]
    rows = find.transform({"favorites": [{"title": "Thriller", "subtitle": "Spotify", "provider": "Spotify", "key": "Thriller", "type": "Album"},
                                         {"title": "Bad", "subtitle": "Michael Jackson", "provider": "Spotify", "key": "Bad"}]}, 120, None)
    check("a Sonos favourite row's service is not shown as its artist; a real artist is",
          rows["favorites"][0]["subtitle"] == "" and rows["favorites"][1]["subtitle"] == "Michael Jackson")

    print("\nthe Browse page's quick search: title, ARTIST and the activity")
    control.find_answers[("roon", "albums")] = {"items": [
        {"id": 5, "title": "Thriller", "subtitle": "Michael Jackson", "image": ART, "path": [{"x": 1}]}], "count": 1, "next": 1, "error": ""}
    conn = S.FakeConnection()
    await hass.ws["artiled_control/search"](hass, conn, {"id": 3, "type": "artiled_control/search", "entity_id": "media_player.secondary",
                                                          "query": "thriller"})
    res = (conn.results.get(3) or {}).get("result") or []
    check("its artist (the webapp's tile shows title and artist) and the activity that plays it",
          res and res[0]["title"] == "Thriller" and res[0]["subtitle"] == "Michael Jackson" and res[0]["activity"] == 120
          and media.parse_content_id(res[0]["content_id"])[0] == 120 and res[0]["image"].startswith("/api/media_player_proxy/"), str(res))

    print("\nBrowse Movies: Find Movies' titles")
    control.find_off_named = True   # API 2.02: served while the Off activity runs
    res, err = await ask("media_player.living_room_main", {"action": "titles"}, movies=True)
    titles = (res or {}).get("answer", {}).get("titles") or []
    by = {t["name"]: t for t in titles}
    check("a film on this activity's player is playable (the movie play)",
          media.parse_content_id(by["Alien"].get("content_id")) == (112, {"kind": "movie", "title_id": 9, "title": "Alien"}))
    check("one on another player is listed, not playable", "content_id" not in by["Blade Runner"])
    check("its cover through the proxy", by["Alien"]["image"].startswith("/api/media_player_proxy/media_player.living_room_main/"))
    await S.stop(hass)
    check.done("find")


asyncio.run(main())
