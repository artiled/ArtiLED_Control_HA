#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for S3a: search and play music and films (#426, Michel 2026-10-26).

Run:  python3 tests/test_search_play.py     (exit 0 = all pass)

The real media_player entities against a fake Control that answers the token
doors as the Control API does (/unit_find, /unit_find_movies,
/activity_favorites, /activity start with "play" - ArtiControlAPI.txt). No
Home Assistant install, no network.

⚠ WHY THIS FILE EXISTS.
- A play belongs to the ACTIVITY'S OWN player (its music_service /
  music_where / movie_player): a Roon album is never sent to a Sonos activity,
  and a refusal sends NOTHING.
- The play object sent is exactly the one the API document defines per source
  (music_album, sonos_favorite, spotify_album, heos_favorite, zidoo_album,
  movie title_id / copy) - a favourite's own play passes through unchanged.
- The running activity is preferred; otherwise the unit's first activity of
  that player, in Control order.
- A start is not proof of playback: the API's play state is reported as it
  is, and a play the Control says it did not play is an error.
- No URL Home Assistant shows carries the Control's token, address or art
  route; and nothing without artwork is shown (Michel).
- Search, play and browse are offered only on units that have a player.
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

F = S.MediaPlayerEntityFeature
PLAYER_FEATURES = F.SEARCH_MEDIA | F.PLAY_MEDIA | F.BROWSE_MEDIA


def sent(control, endpoint):
    return [d for e, d in control.requests if e == endpoint]


async def refuses(coro, words=""):
    try:
        await coro
    except S.HomeAssistantError as err:
        return words.lower() in str(err).lower()
    return False


ROON_ART = "/api/unit_player_art?kind=roon&key=abc&v=2"
FILM_ART = "/api/unit_player_art?kind=movie_cover&id=9&v=0123456789ab"


def fixture() -> S.FakeControl:
    control = S.house(S.FakeControl())
    rows = {a["id"]: a for a in control.activities}
    # Living Room Main: Kaleidescape plays films on movie player 7.
    rows[112].update(music_service="", music_where="", movie_player=7)
    # Living Room Secondary: Roon.
    rows[120].update(music_service="roon", music_where="Secondary", movie_player=0)
    # Kitchen: Sonos.
    rows[20].update(music_service="sonos", music_where="Kitchen", movie_player=0)
    for a in control.activities:
        a.setdefault("music_service", "")
        a.setdefault("music_where", "")
        a.setdefault("movie_player", 0)
    control.find_answers[("roon", "albums")] = {"items": [
        {"id": 41, "title": "Thriller 25", "subtitle": "Michael Jackson", "image": ROON_ART, "path": [{"x": 1}]},
        {"id": 5, "title": "Thriller", "subtitle": "Michael Jackson", "image": ROON_ART, "path": [{"x": 2}]},
        {"id": 6, "title": "Thriller Live", "subtitle": "Various", "image": "", "path": [{"x": 3}]},
        {"id": 7, "title": "Hidden Thriller", "subtitle": "X", "image": ROON_ART, "path": [{"x": 4}], "hidden": True},
    ], "count": 4, "next": 4, "error": ""}
    control.film_titles = [
        {"id": 9, "name": "Alien", "year": 1979, "director": "Ridley Scott", "cast": "", "genres": "Sci-Fi",
         "image": FILM_ART, "on": [7]},
        {"id": 10, "name": "Blade Runner", "year": 1982, "director": "Ridley Scott", "cast": "", "genres": "",
         "image": FILM_ART, "on": [8]},
        {"id": 11, "name": "Gladiator", "year": 2000, "director": "Ridley Scott", "cast": "", "genres": "",
         "image": "", "on": [7]},
    ]
    control.favorites[20] = {"service": "sonos", "items": [
        {"play": {"kind": "music", "source": "sonos_favorite", "key": "Thriller (Album)", "title": "Thriller"},
         "title": "Thriller", "subtitle": "Spotify", "image": "https://i.scdn.co/image/abc", "type": "Album"},
        {"play": {"kind": "music", "source": "sonos_favorite", "key": "Radio 2", "title": "Radio 2"},
         "title": "Radio 2", "subtitle": "TuneIn", "image": "https://cdn.tunein/abc.png", "type": "Radio"},
        {"play": {"kind": "music", "source": "sonos_favorite", "key": "No art", "title": "No art"},
         "title": "No art", "subtitle": "", "image": "", "type": "Playlist"},
    ]}
    control.favorites[120] = {"service": "roon", "items": [
        {"play": {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"},
         "title": "Thriller", "subtitle": "Michael Jackson", "image": ROON_ART, "type": "Album"},
    ]}
    control.favorites[112] = {"service": "movies", "items": [
        {"play": {"kind": "movie", "title_id": 9, "title": "Alien"},
         "title": "Alien", "subtitle": "1979", "image": FILM_ART, "type": "Movie"},
    ]}
    return control


def next_round(hass) -> None:
    """As if the next inventory round (once a minute) had run."""
    hass.data["artiled_control"]["e1"]["coordinator"].inventory_round += 1


def all_urls(items) -> list[str]:
    out = []
    for it in items:
        out.append(str(it.thumbnail or ""))
        out.append(str(it.media_content_id))
        out.extend(all_urls(it.children or []))
    return out


def pure() -> None:
    print("pure rules (media.py)")
    check("a Roon activity has a music player",
          media.player_of({"music_service": "roon", "music_where": "Z"}) == {"service": "roon", "where": "Z", "movie_player": 0})
    check("a service without a target is no player (activity_play_player)",
          media.player_of({"music_service": "roon", "music_where": ""})["service"] == "")
    check("a service the play cannot name is no player",
          media.player_of({"music_service": "appletv", "music_where": "x"})["service"] == "")
    check("an older Control (no fields) has no player", not media.has_player({"id": 1, "name": "x"}))
    check("a movie player counts", media.has_player({"movie_player": 3}))

    shape = media.play_shape
    check("music_album: the key must be a number", shape({"kind": "music", "source": "music_album", "key": "abc", "title": "x"}) is None)
    check("music_album: an int key becomes text",
          shape({"kind": "music", "source": "music_album", "key": 5, "title": " T "}) ==
          {"kind": "music", "source": "music_album", "key": "5", "title": "T"})
    check("heos_favorite needs its name", shape({"kind": "music", "source": "heos_favorite", "key": "2", "title": ""}) is None)
    check("heos_favorite with its name", shape({"kind": "music", "source": "heos_favorite", "key": "2", "title": "Radio"}) is not None)
    check("spotify_album must be spotify:album:<id>",
          shape({"kind": "music", "source": "spotify_album", "key": "spotify:track:1", "title": "x"}) is None
          and shape({"kind": "music", "source": "spotify_album", "key": "spotify:album:4aB", "title": "x"}) is not None)
    check("zidoo_album must be zidoo:<device>:<sha1>",
          shape({"kind": "music", "source": "zidoo_album", "key": "zidoo:3:" + "a" * 40, "title": "x"}) is not None
          and shape({"kind": "music", "source": "zidoo_album", "key": "zidoo:album:x", "title": "x"}) is None)
    check("sonos_favorite: any name", shape({"kind": "music", "source": "sonos_favorite", "key": "My list", "title": "x"}) is not None)
    check("an unknown source is refused", shape({"kind": "music", "source": "tidal_album", "key": "1", "title": "x"}) is None)
    check("an unknown field is refused", shape({"kind": "music", "source": "music_album", "key": "1", "title": "x", "zone": 3}) is None)
    check("movie: title_id, copy kept when above 0",
          shape({"kind": "movie", "title_id": 9, "copy": 4}) == {"kind": "movie", "title_id": 9, "copy": 4}
          and shape({"kind": "movie", "title_id": 9, "copy": 0}) == {"kind": "movie", "title_id": 9})
    check("movie: no film, no play", shape({"kind": "movie", "title_id": 0}) is None and shape({"kind": "movie"}) is None)

    roon = {"id": 120, "music_service": "roon", "music_where": "S", "movie_player": 0}
    kale = {"id": 112, "music_service": "", "music_where": "", "movie_player": 7}
    album = {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}
    check("a Roon album fits a Roon activity", media.play_fits(roon, album) is None)
    check("a Sonos favourite does not fit a Roon activity",
          "plays roon" in (media.play_fits(roon, {"kind": "music", "source": "sonos_favorite", "key": "a", "title": "a"}) or ""))
    check("a film does not fit an activity without a movie player", media.play_fits(roon, {"kind": "movie", "title_id": 9}) is not None)
    check("music does not fit a films-only activity", media.play_fits(kale, album) is not None)

    cid = media.content_id(120, album)
    check("content id round trip", media.parse_content_id(cid) == (120, album))
    check("garbage content ids are no play",
          all(media.parse_content_id(x) is None for x in ("", "play:", "play:x:{}", "play:5:not json", "favorites:5",
                                                           "media-source://x", "play:0:" + json.dumps(album))))
    check("a content id with a malformed play is no play",
          media.parse_content_id("play:5:" + json.dumps({"kind": "music", "source": "music_album", "key": "x"})) is None)

    check("folding ignores case and accents (films.js fold)", media.fold("Amélie") == "amelie")
    def res(aid, play, title, sub="Michael Jackson"):
        return {"activity": aid, "play": play, "title": title, "subtitle": sub, "image": "x", "media_class": "album",
                "content_type": "music"}
    roon_t = res(120, {"kind": "music", "source": "music_album", "key": "1632", "title": "Thriller"}, "Thriller")
    zid_a = res(130, {"kind": "music", "source": "zidoo_album", "key": "zidoo:8:" + "7" * 40, "title": "Thriller"}, "Thriller")
    zid_b = res(130, {"kind": "music", "source": "zidoo_album", "key": "zidoo:8:" + "9" * 40, "title": "Thriller"}, "THRILLER")
    one = media.merge([zid_a, roon_t, zid_b], "michael jackson", prefer=120, order=[130, 120])
    check("live: Thriller on Roon and twice on the Zidoo -> ONE result, the running activity's copy (Roon)",
          [(r["title"], r["activity"]) for r in one] == [("Thriller", 120)], str(one))
    one = media.merge([zid_a, roon_t, zid_b], "michael jackson", prefer=0, order=[130, 120])
    check("...nothing running: the first owning activity in Control order (the Zidoo)", [r["activity"] for r in one] == [130])
    check("another album by the same artist stays", len(media.merge([roon_t, res(120, {"kind": "music", "source": "music_album",
          "key": "8", "title": "Bad"}, "Bad")], "jackson", prefer=120)) == 2)
    check("rank: exact, then starts, then contains, then elsewhere",
          [media.rank(t, "thriller") for t in ("Thriller", "Thriller 25", "The Thriller", "Bad")] == [0, 2, 3, 4])
    check("album match: title or artist, spaces and hyphens squeezed (app_music_album_matches)",
          media.album_matches({"title": "Synth Pop", "subtitle": ""}, "synthpop")
          and media.album_matches({"title": "X", "subtitle": "Björk"}, "BJÖRK")
          and not media.album_matches({"title": "X", "subtitle": "Y"}, "z"))
    check("current activity: the starting one, else the running one, never Off",
          media.current_activity({"active_activity": 5, "starting_activity": 0, "off_activity": 9}) == 5
          and media.current_activity({"active_activity": 5, "starting_activity": 6, "off_activity": 9}) == 6
          and media.current_activity({"active_activity": 5, "starting_activity": 9, "off_activity": 9}) == 0
          and media.current_activity({"active_activity": 9, "starting_activity": 0, "off_activity": 9}) == 0)
    roon2 = {"id": 122, "music_service": "roon", "music_where": "Other zone", "movie_player": 0}
    sonos = {"id": 20, "music_service": "sonos", "music_where": "K", "movie_player": 0}
    zid = lambda i, d: {"id": i, "music_service": "zidoo", "music_where": d, "movie_player": 0}  # noqa: E731
    zkey = {"kind": "music", "source": "zidoo_album", "key": "zidoo:3:" + "a" * 40, "title": "x"}
    kale2 = {"id": 114, "music_service": "", "music_where": "", "movie_player": 8}
    check("runs here: a Roon album on any running Roon activity",
          media.runs_here(roon2, roon, album))
    check("...not on a running Sonos activity", not media.runs_here(sonos, roon, album))
    check("...a Zidoo album only on the same Zidoo", media.runs_here(zid(1, "3"), zid(2, "3"), zkey)
          and not media.runs_here(zid(1, "4"), zid(2, "3"), zkey))
    check("...a film only on the same movie player",
          media.runs_here(dict(kale, id=115), kale, {"kind": "movie", "title_id": 9})
          and not media.runs_here(kale2, kale, {"kind": "movie", "title_id": 9}))
    moved = media.retarget([{"activity": 120, "play": album}, {"activity": 20, "play": {"kind": "music", "source": "sonos_favorite", "key": "a", "title": "a"}}],
                           roon2, [roon, roon2, sonos])
    check("retarget: what the running activity can play moves to it, the rest stays",
          [r["activity"] for r in moved] == [122, 20])
    check("retarget: nothing runs, nothing moves", media.retarget([{"activity": 120, "play": album}], None, [roon])[0]["activity"] == 120)
    mj = {"title": "Thriller", "subtitle": "Michael Jackson"}
    for q in ("Michael Jackson Thriller", "michael jacksons thriller", "thriller", "jackson", "Jackson's Thriller", "THRILLER michael"):
        check(f"every word, anywhere in title + artist: {q!r} finds Thriller (Michel 2026-10-26)", media.album_matches(mj, q))
    check("...and a word that is not there does not", not media.album_matches(mj, "michael jackson bad"))
    check("a film: title, director, cast, genres AND year", media.film_matches({"name": "Alien", "year": 1979, "director": "Ridley Scott"}, "alien 1979")
          and not media.film_matches({"name": "Alien", "year": 1979}, "alien 1986"))
    check("the Control's own album search is asked ONE word - the longest", media.search_word("Michael Jackson Thriller") == "thriller")
    check("rank: the exact title first, the artist's words last",
          media.rank("Thriller", "thriller") == 0 and media.rank("Thriller", "Michael Jackson Thriller") == 1
          and media.rank("Thriller 25", "Michael Jackson Thriller") == 4)
    check("film match: title, director, cast, genres (films.js matches)",
          media.film_matches({"name": "Alien", "director": "Ridley Scott"}, "ridley")
          and media.film_matches({"name": "Amélie"}, "amelie"))


async def runtime() -> None:
    control = fixture()
    hass, entry, control = await S.setup_integration(control)
    secondary = hass.entity("media_player.secondary")
    main = hass.entity("media_player.living_room_main")
    kitchen = hass.entity("media_player.kitchen")
    bedroom = hass.entity("media_player.bedroom_main")
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]

    print("\nfeatures: only where a player exists, also while off")
    check("Roon zone: search, play and browse", PLAYER_FEATURES in secondary.supported_features)
    check("Kitchen (Sonos, off): search, play and browse", PLAYER_FEATURES in kitchen.supported_features)
    check("Living Room Main (films, on): search, play and browse", PLAYER_FEATURES in main.supported_features)
    check("Bedroom (TV only): none of them", not (bedroom.supported_features & PLAYER_FEATURES))
    check("the Browse shortcut opens what the RUNNING activity plays: nothing running -> '' (the room decides)",
          main.extra_state_attributes.get("browse_kind") == "" and secondary.extra_state_attributes.get("browse_kind") == "")
    check("the dashboard's Browse Music / Browse Movies follow what each room plays",
          secondary.extra_state_attributes.get("plays_music") is True and secondary.extra_state_attributes.get("plays_movies") is False
          and main.extra_state_attributes.get("plays_movies") is True and main.extra_state_attributes.get("plays_music") is False
          and bedroom.extra_state_attributes.get("plays_music") is False and bedroom.extra_state_attributes.get("plays_movies") is False)

    print("\nsearch while nothing runs -> /unit_find naming the activity (the webapp's Albums search)")
    control.requests.clear()
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    asks = sent(control, "unit_find")
    check("one /unit_find", len(asks) == 1, str(asks))
    ask = asks[0] if asks else {}
    check("...the webapp's albums request: zone, action, q, the activity named (idle)",
          ask.get("zone") == 12 and ask.get("action") == "albums" and ask.get("q") == "thriller"
          and ask.get("activity") == 120 and "expect" not in ask, str(ask))
    titles = [r.title for r in found.result]
    check("exact title first (HassMediaSearchAndPlay plays the FIRST result)", titles[:1] == ["Thriller"], str(titles))
    check("the TITLE only - Assist speaks it: 'Playing Thriller' (Michel 2026-10-26)",
          titles == ["Thriller", "Thriller 25"], str(titles))
    check("no artwork, not shown", not any(t.startswith("Thriller Live") for t in titles))
    check("a hidden album is not shown", not any(t.startswith("Hidden") for t in titles))
    first = found.result[0]
    check("each result plays", first.can_play and not first.can_expand)
    check("media class album, type music", first.media_class == "album" and first.media_content_type == "music")
    check("its content id is that activity + the API's music_album play",
          media.parse_content_id(first.media_content_id) ==
          (120, {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}))
    check("the Roon album the hearts also list is one result, not two",
          titles.count("Thriller") == 1)

    control.requests.clear()
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="Michael Jackson Thriller"))
    check("'Michael Jackson Thriller' (Assist, live): the Control asked for 'thriller', Thriller found first",
          (sent(control, "unit_find") or [{}])[0].get("q") == "thriller" and [r.title for r in found.result][:1] == ["Thriller"],
          str([r.title for r in found.result]))
    for q in ("michael jacksons thriller", "jackson"):
        found = await secondary.async_search_media(S.SearchMediaQuery(search_query=q))
        check(f"{q!r} finds Thriller", "Thriller" in [r.title for r in found.result])

    print("\nno token, address or art route in anything Home Assistant shows")
    urls = all_urls(found.result)
    check("no Control token", all(S.TOKEN not in u for u in urls))
    check("no Control address", all("192.0.2.10" not in u for u in urls))
    check("no Control art route", all("unit_player_art" not in u for u in urls))
    check("thumbnails are Home Assistant's own proxy",
          all(r.thumbnail.startswith("/api/media_player_proxy/media_player.secondary/browse_media/") for r in found.result))
    kind, _, rest = first.thumbnail.split("/browse_media/", 1)[1].partition("/")
    image_id = rest.split("?", 1)[0]
    control.art_requests.clear()
    body, ctype = await secondary.async_get_browse_image(kind, image_id)
    check("the proxy fetches the cover server-side, token added there only",
          body == b"\xff\xd8JPEG" and len(control.art_requests) == 1
          and "/api/unit_player_art?kind=roon&key=abc&v=2&token=" in control.art_requests[0])
    check("an unknown picture id fetches nothing", await secondary.async_get_browse_image(kind, "nope") == (None, None))

    print("\nsearch while the activity runs -> its own player, with expect")
    control.zones[1]["active_activity"] = 120
    await coordinator.async_refresh()
    control.requests.clear()
    await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    ask = (sent(control, "unit_find") or [{}])[0]
    check("expect names the running activity and its service",
          ask.get("expect") == {"activity": 120, "service": "roon"} and "activity" not in ask, str(ask))

    print("\nsearch while the unit is OFF (its Off runs): an older Control refuses a named activity -> the hearts")
    control.zones[1]["active_activity"] = 121
    await coordinator.async_refresh()
    control.requests.clear()
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    asked = sent(control, "unit_find")
    check("first tried: /unit_find naming the activity (feature detection by asking)",
          len(asked) == 1 and asked[0].get("activity") == 120, str(asked))
    check("refused -> /activity_favorites of the player activity", sent(control, "activity_favorites") == [{"activity": 120}])
    check("the heart found", [r.title for r in found.result] == ["Thriller"])
    control.requests.clear()
    await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("remembered for this Control version: not asked again", not sent(control, "unit_find"))

    print("\na Control that searches a named activity while the room is Off (the API extension)")
    control.find_off_named = True
    control.version = "1.58"
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    await hass.settle()
    secondary = hass.entity("media_player.secondary")
    kitchen = hass.entity("media_player.kitchen")
    main = hass.entity("media_player.living_room_main")
    bedroom = hass.entity("media_player.bedroom_main")
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    control.requests.clear()
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("a new Control version is asked again, and searched while Off: the whole album list",
          [r.title for r in found.result] == ["Thriller", "Thriller 25"] and not sent(control, "activity_favorites"),
          str([r.title for r in found.result]))
    control.requests.clear()
    await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("...and keeps doing so", len(sent(control, "unit_find")) == 1 and not sent(control, "activity_favorites"))
    control.find_off_named = False
    control.version = "1.57"
    coordinator.inventory_due = True
    await coordinator.async_refresh()
    await hass.settle()
    secondary = hass.entity("media_player.secondary")
    kitchen = hass.entity("media_player.kitchen")
    main = hass.entity("media_player.living_room_main")
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    control.zones[1]["active_activity"] = 0
    await coordinator.async_refresh()

    print("\nSonos: its favourites, filtered by what was said, play passed through unchanged")
    control.requests.clear()
    found = await kitchen.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("Sonos asks /activity_favorites only", not sent(control, "unit_find") and sent(control, "activity_favorites") == [{"activity": 20}])
    check("only the matching favourite", [r.title for r in found.result] == ["Thriller"])
    check("the favourite's own play object, untouched",
          media.parse_content_id(found.result[0].media_content_id) ==
          (20, {"kind": "music", "source": "sonos_favorite", "key": "Thriller (Album)", "title": "Thriller"}))
    radio = await kitchen.async_search_media(S.SearchMediaQuery(search_query="radio"))
    check("a radio favourite is a channel", [r.media_class for r in radio.result] == ["channel"])

    print("\nfilms: the webapp's Find Movies titles, filtered as films.js does, only those that play on THIS player")
    control.zones[0]["active_activity"] = 0
    await coordinator.async_refresh()
    control.requests.clear()
    found = await main.async_search_media(S.SearchMediaQuery(search_query="ridley"))
    ask = (sent(control, "unit_find_movies") or [{}])[0]
    check("/unit_find_movies titles, naming the films activity", ask.get("action") == "titles" and ask.get("activity") == 112, str(ask))
    check("Alien (on player 7) found; Blade Runner (player 8) and Gladiator (no cover) not",
          [r.title for r in found.result] == ["Alien"], str([r.title for r in found.result]))
    check("a film: class movie, type movie, the API's movie play",
          found.result and found.result[0].media_class == "movie" and found.result[0].media_content_type == "movie"
          and media.parse_content_id(found.result[0].media_content_id) == (112, {"kind": "movie", "title_id": 9, "title": "Alien"}))
    control.requests.clear()
    await main.async_search_media(S.SearchMediaQuery(search_query="alien"))
    check("the film library is read once a round, as Find Movies reads it once (was: every search)",
          not sent(control, "unit_find_movies"))
    only_music = await main.async_search_media(S.SearchMediaQuery(search_query="ridley", media_filter_classes=["album"]))
    check("a class filter is kept (album: no films)", list(only_music.result) == [])

    print("\nplay -> /activity start with exactly the play")
    control.requests.clear()
    album = {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}
    await secondary.async_play_media("music", media.content_id(120, album))
    starts = sent(control, "activity")
    check("one start of the activity with the play",
          starts == [{"action": "start", "activity": 120, "play": album}], str(starts))
    check("the API's play state is reported, not 'playing'",
          secondary.extra_state_attributes.get("play") ==
          {"title": "Thriller", "activity_id": 120, "activity_name": "Listen to Roon", "state": "sent"},
          str(secondary.extra_state_attributes.get("play")))
    film = {"kind": "movie", "title_id": 9, "title": "Alien"}
    control.requests.clear()
    await main.async_play_media("movie", media.content_id(112, film))
    check("a film: the movie play to the films activity",
          sent(control, "activity") == [{"action": "start", "activity": 112, "play": film}])
    heos = {"kind": "music", "source": "sonos_favorite", "key": "Thriller (Album)", "title": "Thriller"}
    control.requests.clear()
    await kitchen.async_play_media("music", media.content_id(20, heos))
    check("a Sonos favourite: sonos_favorite by its key", sent(control, "activity") == [{"action": "start", "activity": 20, "play": heos}])

    print("\na play the Control says it did not play is an error, with its word")
    for state in ("failed", "discarded", "skipped", "unresolved"):
        control.play_state = state
        check(f"state {state} -> error", await refuses(secondary.async_play_media("music", media.content_id(120, album)), state))
    control.play_state = "attached"
    await secondary.async_play_media("music", media.content_id(120, album))
    check("attached (plays when the start finishes) is not an error, and is said",
          secondary.extra_state_attributes["play"]["state"] == "attached")
    control.play_state = "sent"

    print("\nrefusals: nothing is sent")
    control.requests.clear()
    sonos_fav = {"kind": "music", "source": "sonos_favorite", "key": "a", "title": "a"}
    check("a Sonos play on the Roon activity is refused",
          await refuses(secondary.async_play_media("music", media.content_id(120, sonos_fav)), "plays roon"))
    check("an activity of another unit is refused",
          await refuses(secondary.async_play_media("music", media.content_id(20, sonos_fav)), "not an activity"))
    check("the Off activity is refused",
          await refuses(secondary.async_play_media("music", media.content_id(121, album)), "not an activity"))
    check("a film on a unit's music activity is refused",
          await refuses(secondary.async_play_media("movie", media.content_id(120, film)), "plays no films"))
    check("something not ours (a URL) is refused",
          await refuses(secondary.async_play_media("music", "http://example.invalid/a.mp3"), "cannot play"))
    check("...and nothing was sent for any of them", not sent(control, "activity"))
    control.errors["activity"] = (4, "Invalid play: That album is no longer in Roon - nothing started")
    check("the Control's own refusal reaches the user",
          await refuses(secondary.async_play_media("music", media.content_id(120, album)), "no longer in Roon"))
    del control.errors["activity"]

    print("\nprefer the running activity; otherwise the first of that player, in Control order")
    control.activities.append(dict(S.activity(122, "Roon Background", zone_id=12, room_id=1),
                                   music_service="roon", music_where="Secondary", movie_player=0))
    control.favorites[122] = control.favorites[120]
    await S.poll(hass, inventory=True)
    # The new activity changed the inventory: the entry reloaded, so the
    # entities are new ones.
    secondary = hass.entity("media_player.secondary")
    kitchen = hass.entity("media_player.kitchen")
    main = hass.entity("media_player.living_room_main")
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    control.zones[1]["active_activity"] = 122
    await coordinator.async_refresh()
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("running: the result plays on the running activity",
          media.parse_content_id(found.result[0].media_content_id)[0] == 122)
    control.zones[1]["active_activity"] = 121
    await coordinator.async_refresh()
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("off: the first Roon activity in Control order", media.parse_content_id(found.result[0].media_content_id)[0] == 120)

    print("\nevery source failing is an error, not 'nothing found'")
    control.favorites[120] = (8, "The Roon albums could not be read now")
    control.favorites[122] = (8, "The Roon albums could not be read now")
    next_round(hass)  # the next inventory round: lists are read again (Michel: "on the inventory cycle")
    check("search says why", await refuses(secondary.async_search_media(S.SearchMediaQuery(search_query="x")), "could not be read"))
    control.favorites[120] = control.favorites[122] = {"service": "roon", "items": []}
    next_round(hass)  # the next inventory round: lists are read again (Michel: "on the inventory cycle")
    nothing = await secondary.async_search_media(S.SearchMediaQuery(search_query="x"))
    check("an honest empty answer is an empty result", list(nothing.result) == [])

    print("\nbrowse: the unit's favourites per activity, and the search")
    root = await kitchen.async_browse_media()
    check("root can search", root.can_search and root.can_expand and not root.can_play)
    check("one activity: its favourites straight away, artwork only",
          [c.title for c in root.children] == ["Thriller", "Radio 2"], str([c.title for c in root.children]))
    check("each child plays", all(c.can_play for c in root.children))
    root = await main.async_browse_media()
    check("Living Room Main: the films activity's hearts", [c.title for c in root.children] == ["Alien"])
    control.favorites[120] = {"service": "roon", "items": [
        {"play": album, "title": "Thriller", "subtitle": "MJ", "image": ROON_ART, "type": "Album"}]}
    control.favorites[122] = {"service": "roon", "items": []}
    next_round(hass)
    root = await secondary.async_browse_media()
    check("off, several activities but only one with hearts: its hearts straight away, no one-folder detour",
          [c.title for c in root.children] == ["Thriller"] and root.children[0].can_play, str([c.title for c in root.children]))
    folder = await secondary.async_browse_media("favorites", "favorites:120")
    check("its folder lists its hearts", [c.title for c in folder.children] == ["Thriller"])
    check("browse URLs carry no token, address or art route",
          all(S.TOKEN not in u and "192.0.2.10" not in u and "unit_player_art" not in u
              for u in all_urls([root, folder])))
    check("a folder of another unit is refused",
          await refuses(secondary.async_browse_media("favorites", "favorites:20"), "not an activity"))

    print("\nfavourites keep updating: read on every look, no reload, never a stale list (Michel 2026-10-26)")
    entry_data = hass.data["artiled_control"]["e1"]
    bad = {"kind": "music", "source": "music_album", "key": "8", "title": "Bad"}
    control.favorites[122] = {"service": "roon", "items": [
        {"play": bad, "title": "Bad", "subtitle": "MJ", "image": ROON_ART, "type": "Album"}]}
    next_round(hass)  # the next inventory round: lists are read again (Michel: "on the inventory cycle")
    root = await secondary.async_browse_media()
    check("a heart added on the Control is there on the next look",
          [c.title for c in root.children] == ["Listen to Roon", "Roon Background"], str([c.title for c in root.children]))
    control.favorites[120] = {"service": "roon", "items": []}
    next_round(hass)  # the next inventory round: lists are read again (Michel: "on the inventory cycle")
    root = await secondary.async_browse_media()
    check("a heart removed is gone on the next look (one source left: its hearts straight away)",
          [c.title for c in root.children] == ["Bad"], str([c.title for c in root.children]))
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="bad"))
    check("...and search finds the new heart", [r.title for r in found.result] == ["Bad"])
    check("...all without a reload (same entry, same entity)",
          hass.data["artiled_control"]["e1"] is entry_data and hass.entity("media_player.secondary") is secondary)

    print("\nactive-activity aware (Michel 2026-10-26)")
    control.favorites[120] = {"service": "roon", "items": [
        {"play": album, "title": "Thriller", "subtitle": "MJ", "image": ROON_ART, "type": "Album"}]}
    control.zones[1]["active_activity"] = 122
    control.find_answers[("roon", "albums")] = {"items": [], "count": 0, "next": 0, "error": ""}
    await coordinator.async_refresh()
    root = await secondary.async_browse_media()
    check("browse while one runs: its content at the root - its own heart, then the other Roon activity's, played here",
          [c.title for c in root.children] == ["Bad", "Thriller"]
          and {media.parse_content_id(c.media_content_id)[0] for c in root.children} == {122}, str([c.title for c in root.children]))
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="thriller"))
    check("search: a heart of the idle Roon activity plays on the RUNNING Roon activity (no new start of another source)",
          [r.title for r in found.result] == ["Thriller"]
          and media.parse_content_id(found.result[0].media_content_id)[0] == 122, str([r.title for r in found.result]))
    control.requests.clear()
    await secondary.async_play_media("music", media.content_id(120, album))
    check("play: an item found for the idle activity, while a Roon activity runs, plays on the running one",
          sent(control, "activity") == [{"action": "start", "activity": 122, "play": album}], str(sent(control, "activity")))
    control.zones[1]["active_activity"] = 0
    control.zones[1]["starting_activity"] = 122
    await coordinator.async_refresh()
    control.requests.clear()
    await secondary.async_play_media("music", media.content_id(120, album))
    check("...also while it is still starting (the Control attaches the play to that start)",
          sent(control, "activity")[-1]["activity"] == 122)
    control.zones[1]["starting_activity"] = 0
    control.zones[1]["active_activity"] = 121
    await coordinator.async_refresh()
    control.requests.clear()
    await secondary.async_play_media("music", media.content_id(120, album))
    check("nothing runs (Off): the owning activity starts, as before",
          sent(control, "activity")[-1]["activity"] == 120)

    control.zones[0]["active_activity"] = 110
    await coordinator.async_refresh()
    control.requests.clear()
    found = await main.async_search_media(S.SearchMediaQuery(search_query="alien"))
    check("the running activity cannot play it (Netflix, no player): the owner's, Kaleidescape, is named",
          [r.title for r in found.result] == ["Alien"]
          and media.parse_content_id(found.result[0].media_content_id)[0] == 112)
    await main.async_play_media("movie", media.content_id(112, film))
    check("...and started", sent(control, "activity")[-1] == {"action": "start", "activity": 112, "play": film})
    control.zones[0]["active_activity"] = 111
    await coordinator.async_refresh()

    await S.stop(hass)


async def browse_tree() -> None:
    """Michel 2026-10-26, live on the Kitchen: "it asks me first service. It is
    NOT service aware" and "I see doubles"; and "NO artwork for Oxygene 3"."""
    control = S.house(S.FakeControl())
    rows = {a["id"]: a for a in control.activities}
    rows[120].update(music_service="roon", music_where="Secondary", movie_player=0)
    control.activities.append(dict(S.activity(122, "Roon Background", zone_id=12, room_id=1),
                                   music_service="roon", music_where="Secondary", movie_player=0))
    control.activities.append(dict(S.activity(123, "Listen to Sonos", zone_id=12, room_id=1),
                                   music_service="sonos", music_where="Secondary", movie_player=0))
    for a in control.activities:
        a.setdefault("music_service", "")
        a.setdefault("music_where", "")
        a.setdefault("movie_player", 0)
    thr = {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}
    bad = {"kind": "music", "source": "music_album", "key": "8", "title": "Bad"}
    radio = {"kind": "music", "source": "sonos_favorite", "key": "Radio 2", "title": "Radio 2"}
    oxy = {"kind": "music", "source": "sonos_favorite", "key": "Oxygene 3", "title": "Oxygene 3"}
    control.favorites[120] = {"service": "roon", "items": [
        {"play": thr, "title": "Thriller", "subtitle": "MJ", "image": ROON_ART, "type": "Album"},
        {"play": bad, "title": "Bad", "subtitle": "MJ", "image": ROON_ART, "type": "Album"},
        {"play": thr, "title": "Thriller", "subtitle": "MJ", "image": ROON_ART, "type": "Album"}]}   # a double in one list
    control.favorites[122] = {"service": "roon", "items": [
        {"play": thr, "title": "Thriller", "subtitle": "MJ", "image": ROON_ART, "type": "Album"}]}   # the same heart again
    control.favorites[123] = {"service": "sonos", "items": [
        {"play": radio, "title": "Radio 2", "subtitle": "", "image": "https://cdn.example/radio2.png", "type": "Radio"},
        {"play": oxy, "title": "Oxygene 3", "subtitle": "Jarre", "image": "https://cdn.example/dead-oxygene.jpg", "type": "Album"}]}
    control.zones[1]["active_activity"] = 121   # Off
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    hass.session.outside["https://cdn.example/radio2.png"] = b"PNG"
    hass.session.outside_status["https://cdn.example/dead-oxygene.jpg"] = 404
    hass, entry, control = await S.setup_integration(control, hass=hass)
    secondary = hass.entity("media_player.secondary")
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    ids = lambda node: [media.parse_content_id(c.media_content_id)[0] for c in node.children if c.can_play]  # noqa: E731

    print("\nbrowse while the room is OFF: its playable activities, each opening its favourites")
    root = await secondary.async_browse_media()
    check("a folder per activity that has something of its own - not one only repeating another",
          [c.title for c in root.children] == ["Listen to Roon", "Listen to Sonos"], str([c.title for c in root.children]))
    roon = await secondary.async_browse_media("favorites", root.children[0].media_content_id)
    check("Listen to Roon: Thriller once (not twice), Bad", [c.title for c in roon.children] == ["Thriller", "Bad"],
          str([c.title for c in roon.children]))
    sonos = await secondary.async_browse_media("favorites", root.children[1].media_content_id)
    check("a cover that answers 404 (Oxygene 3, live) is never a broken tile: left out; Radio 2 kept",
          [c.title for c in sonos.children] == ["Radio 2"], str([c.title for c in sonos.children]))

    print("\nbrowse while an activity RUNS: its own content straight away, the rest under 'Other sources'")
    control.zones[1]["active_activity"] = 122
    await coordinator.async_refresh()
    root = await secondary.async_browse_media()
    plays = [c for c in root.children if c.can_play]
    check("no picker first: Roon Background's favourites at the top - and Listen to Roon's, which it plays too",
          [c.title for c in plays] == ["Thriller", "Bad"] and set(ids(root)) == {122}, str([(c.title, c.can_play) for c in root.children]))
    others = [c for c in root.children if not c.can_play]
    check("then ONE 'Other sources' folder", [c.title for c in others] == ["Other sources"])
    other = await secondary.async_browse_media(others[0].media_content_type, others[0].media_content_id) if others else None
    check("...holding only what the running one cannot play: Listen to Sonos (Listen to Roon would only repeat)",
          other is not None and [c.title for c in other.children] == ["Listen to Sonos"], str(other and [c.title for c in other.children]))
    check("the root still searches", root.can_search)
    found = await secondary.async_search_media(S.SearchMediaQuery(search_query="r"))
    titles = [r.title for r in found.result]
    check("search: one result per item (Thriller once), and never the dead cover",
          titles.count("Thriller") == 1 and "Oxygene 3" not in titles, str(titles))

    print("\nspeed (Michel: \"it took long\"): one read per list a round, a cover checked once")
    control.zones[1]["active_activity"] = 121
    await coordinator.async_refresh()
    control.requests.clear()
    hass.session.log.clear()
    root = await secondary.async_browse_media()
    for c in root.children:
        if c.can_expand:
            await secondary.async_browse_media(c.media_content_type, c.media_content_id)
    await secondary.async_search_media(S.SearchMediaQuery(search_query="radio"))
    reads = [d for e, d in control.requests if e == "activity_favorites"]
    check("the root, both folders and a search: each activity's hearts read ONCE (3 reads, was 3 per look = 12)",
          len(reads) == 3, str(reads))
    heads = [u for m, u in hass.session.log if m == "HEAD"]
    gets = [u for m, u in hass.session.log if m == "GET" and "cdn.example" in u]
    check("covers already checked are not checked again; a check is a HEAD, never a download",
          heads == [] and gets == [], str(hass.session.log))
    next_round(hass)
    control.requests.clear()
    await secondary.async_browse_media()
    check("the next inventory round reads them again", len([1 for e, _ in control.requests if e == "activity_favorites"]) == 3)
    control.zones[1]["active_activity"] = 122
    await coordinator.async_refresh()
    control.requests.clear()
    await secondary.async_browse_media()
    check("...and so does a change of the running activity", len([1 for e, _ in control.requests if e == "activity_favorites"]) == 3)
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    hass.session.log.clear()
    await hass.entity("media_player.secondary").async_browse_media()
    check("a reload does not check the covers again (verdicts kept a week)",
          not [1 for m, _ in hass.session.log if m == "HEAD"], str(hass.session.log))
    await S.stop(hass)


async def michels_doubles() -> None:
    """Michel 2026-10-26 on 0.3.13: still doubles on the Browse page. His case:
    Roon running in the Kitchen, Thriller on Roon AND twice on the Zidoo
    (two editions), hearts and library albums overlapping, over 100 items."""
    print("\nMichel's doubles: Roon running, Thriller on Roon + two Zidoo editions, 120+ items")
    control = S.house(S.FakeControl())
    control.activities = [a for a in control.activities if a["room_id"] != 2]
    control.activities += [
        dict(S.activity(24, "Listen to Roon", room_id=2), music_service="roon", music_where="Kitchen", movie_player=0),
        dict(S.activity(25, "Listen to Zidoo", room_id=2), music_service="zidoo", music_where="8", movie_player=0),
        dict(S.activity(21, "Off", room_id=2), music_service="", music_where="", movie_player=0),
    ]
    for a in control.activities:
        a.setdefault("music_service", "")
        a.setdefault("music_where", "")
        a.setdefault("movie_player", 0)
    control.rooms[1].update(active_activity=24, on_activity=24, off_activity=21)
    roon_thr = {"kind": "music", "source": "music_album", "key": "1632", "title": "Thriller"}
    zid_a = {"kind": "music", "source": "zidoo_album", "key": "zidoo:8:" + "7" * 40, "title": "Thriller"}
    zid_b = {"kind": "music", "source": "zidoo_album", "key": "zidoo:8:" + "9" * 40, "title": "Thriller"}
    many = [{"play": {"kind": "music", "source": "music_album", "key": str(2000 + n), "title": f"Album {n}"},
             "title": f"Album {n}", "subtitle": "Someone", "image": ROON_ART, "type": "Album"} for n in range(120)]
    control.favorites[24] = {"service": "roon", "items": [
        {"play": roon_thr, "title": "Thriller", "subtitle": "Michael Jackson", "image": ROON_ART, "type": "Album"}] + many}
    zart = "/api/unit_player_art?kind=zidoo&a=1"
    control.favorites[25] = {"service": "zidoo", "items": [
        {"play": zid_a, "title": "Thriller", "subtitle": "Michael Jackson", "image": zart, "type": "Album"},
        {"play": zid_b, "title": "Thriller", "subtitle": "Michael Jackson", "image": zart, "type": "Album"},
        {"play": {"kind": "music", "source": "zidoo_album", "key": "zidoo:8:" + "1" * 40, "title": "Dangerous"},
         "title": "Dangerous", "subtitle": "Michael Jackson", "image": zart, "type": "Album"}]}
    control.find_answers[("roon", "albums")] = {"items": [
        {"id": 1632, "title": "Thriller", "subtitle": "Michael Jackson", "image": ROON_ART, "path": [{"x": 1}]},
        {"id": 1633, "title": "Thriller 25", "subtitle": "Michael Jackson", "image": ROON_ART, "path": [{"x": 2}]}],
        "count": 2, "next": 2, "error": ""}
    hass, entry, control = await S.setup_integration(control)
    kitchen = hass.entity("media_player.kitchen")

    async def walk(node, depth=0):
        out = []
        for c in node.children or []:
            if c.can_play:
                out.append(c.title)
            elif c.can_expand and depth < 3:
                out += await walk(await kitchen.async_browse_media(c.media_content_type, c.media_content_id), depth + 1)
        return out
    every = await walk(await kitchen.async_browse_media())
    check("the whole Browse tree (root + Other sources): Thriller ONCE, though Roon and the Zidoo (twice) have it",
          every.count("Thriller") == 1, str([t for t in every if t == "Thriller"]))
    check("...the Zidoo's own album that Roon has not still reachable (Dangerous)", "Dangerous" in every)
    check("...and all 121 Roon hearts there, once each (paging has a full list to page)",
          len([t for t in every if t.startswith("Album ")]) == 120 and len(every) == len(set(every)), str(len(every)))
    found = await kitchen.async_search_media(S.SearchMediaQuery(search_query="Michael Jackson"))
    titles = [r.title for r in found.result]
    check("search 'Michael Jackson' (live): Thriller once, on Roon (the running activity)",
          titles.count("Thriller") == 1 and media.parse_content_id(
              next(r for r in found.result if r.title == "Thriller").media_content_id)[0] == 24, str(titles))
    await S.stop(hass)


async def main() -> None:
    pure()
    await runtime()
    await browse_tree()
    await michels_doubles()


asyncio.run(main())
check.done("search-and-play")
