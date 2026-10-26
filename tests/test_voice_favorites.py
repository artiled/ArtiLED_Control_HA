#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for voice favourites (#426, Michel 2026-10-26).

Run:  python3 tests/test_voice_favorites.py     (exit 0 = all pass)

"Favourites are favourites in ANY room": a heart of the Control (an album, a
playlist, a film) becomes ONE voice command for the whole house - "Thriller" -
that plays in the RIGHT room, decided when it is said: a room where something
playing can play it (the most recently changed), else the room started last,
else the default room from Configure. In that room: on the running activity
when it can, else by starting the activity that owns it. The Favourites tab is
one list for the whole Control; "Make all favourites available to voice" makes
one scene per favourite by itself (cap, Repairs note).

⚠ WHY THIS FILE EXISTS.
- One entry per favourite everywhere (its play, or its album / film).
- The play is the Control's own object, passed through - never rebuilt.
- What the user did stands: added by hand is never removed by the sync, one
  removed is not added back, an un-expose stays, a typed name stays.
- A failed read is not "the hearts are gone".
- 0.3.9-0.3.11 made one scene PER ROOM: those become the house-wide one, the
  first keeping its entity id, aliases and exposure, the others removed
  cleanly.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
voice = S.load_pure("voice")
media = S.load_pure("media")
ids = S.load_pure("entity_ids")

ART = "/api/unit_player_art?kind=sonos&u=1"
SONOS_THRILLER = {"kind": "music", "source": "sonos_favorite", "key": "Thriller (Album)", "title": "Thriller"}
SONOS_THRILLER_PL = {"kind": "music", "source": "sonos_favorite", "key": "Thriller (Playlist)", "title": "Thriller"}
SONOS_RADIO = {"kind": "music", "source": "sonos_favorite", "key": "Radio 2", "title": "Radio 2"}
ROON_BAD = {"kind": "music", "source": "music_album", "key": "8", "title": "Bad"}
ROON_THRILLER = {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}
ROON_THRILLER = {"kind": "music", "source": "music_album", "key": "5", "title": "Thriller"}
ALIEN = {"kind": "movie", "title_id": 9, "title": "Alien"}


def heart(play, title, kind="Album", image=ART, subtitle=""):
    return {"play": play, "title": title, "subtitle": subtitle, "image": image, "type": kind}


def row(aid, service="", where="", movie=0):
    return {"id": aid, "name": f"A{aid}", "music_service": service, "music_where": where, "movie_player": movie}


def pure() -> None:
    print("pure rules")
    check("names without a room: title, then its kind, then its service",
          voice.favorite_names("Thriller", "Album", "roon") == ["Thriller", "Thriller album", "Thriller on Roon"])
    check("a film is a movie", voice.favorite_names("Alien", "Movie", "movies")[1] == "Alien movie")
    check("a clash takes the next name",
          voice.suggest_favorite_name("Thriller", "Playlist", "sonos", lambda n: n.lower() == "thriller") == "Thriller playlist")
    hearts = [{"play": SONOS_THRILLER, "title": "Thriller", "type": "Album", "service": "sonos"},
              {"play": SONOS_RADIO, "title": "Radio 2", "type": "Radio", "service": "sonos"},
              {"play": SONOS_RADIO, "title": "Radio 2", "type": "Radio", "service": "sonos"}]
    existing = [{"play": SONOS_THRILLER_PL, "name": "Mine", "auto": False},
                {"play": {"kind": "music", "source": "sonos_favorite", "key": "Gone", "title": "Gone"}, "name": "Gone", "auto": True}]
    plan = voice.plan_auto(hearts, existing, set(), 200, complete=True)
    check("one per favourite for the house (Radio 2 once)", [f["play"]["key"] for f in plan["add"]] == ["Thriller (Album)", "Radio 2"])
    check("an automatic one whose heart is gone is removed; one by hand never", [f["name"] for f in plan["remove"]] == ["Gone"])
    check("a round with a failed read removes nothing", voice.plan_auto(hearts, existing, set(), 200, complete=False)["remove"] == [])
    check("one the user removed is not added back",
          not voice.plan_auto(hearts, existing, {voice.favorite_key(SONOS_RADIO)}, 200)["add"][1:])
    capped = voice.plan_auto(hearts, existing, set(), 2)
    check("the cap counts every favourite scene: cap 2 - 1 kept = 1 added, 1 left out",
          len(capped["add"]) == 1 and capped["left_out"] == 1, str(capped))

    print("\none heart = one record: editions are one favourite (live, 132 scenes)")
    for edition, plain in (("1492: Conquest of Paradise [Music from the Original Soundtrack]", "1492: Conquest of Paradise"),
                           ("Antarctica (Remastered)", "Antarctica"),
                           ("Antarctica [Original Motion Picture Soundtrack]", "Antarctica"),
                           ("Avatar [Original Motion Picture Soundtrack]", "Avatar"),
                           ("Blade Runner (Music From The Original Soundtrack)", "Blade Runner")):
        check(f"{edition!r} is the record {plain!r} (the Control's music_albums_lookup_title)", media.record_title(edition) == plain)
    check("...but a live album or a remix is its own record",
          media.record_title("Alive (Live)") == "Alive (Live)" and media.record_title("Abbey Road - 2019 Remix") == "Abbey Road - 2019 Remix")
    eds = [{"activity": 1, "play": {"kind": "music", "source": "music_album", "key": str(n), "title": t}, "title": t,
            "subtitle": "Vangelis", "type": "Album", "content_type": "music"}
           for n, t in enumerate(("1492: Conquest of Paradise", "1492: Conquest of Paradise [Music from the Original Soundtrack]",
                                  "Antarctica", "Antarctica (Remastered)", "Antarctica [Original Motion Picture Soundtrack]",
                                  "Blade Runner", "Blade Runner (Music From The Original Soundtrack)"), start=1)]
    check("seven hearts, three records: three favourites", [r["title"] for r in media.dedupe(eds)] ==
          ["1492: Conquest of Paradise", "Antarctica", "Blade Runner"])

    check("artist_line: a service or a kind word is no artist; the provider named again neither; an artist is",
          media.artist_line({"subtitle": "Spotify"}) == "" and media.artist_line({"subtitle": "Station"}) == ""
          and media.artist_line({"subtitle": "Radio Veronica", "provider": "Radio Veronica"}) == ""
          and media.artist_line({"subtitle": "Michael Jackson", "provider": "Spotify"}) == "Michael Jackson")

    print("\nthe room it plays in (Michel 2026-10-26)")
    units = [
        {"key": "room_2", "name": "Kitchen", "rows": [row(20, "sonos", "K")], "current": 20, "playing": True, "changed": 100, "last_start": 50},
        {"key": "zone_12", "name": "Secondary", "rows": [row(120, "roon", "S"), row(122, "roon", "S")], "current": 122,
         "playing": True, "changed": 200, "last_start": 60},
        {"key": "zone_11", "name": "Main", "rows": [row(112, movie=7)], "current": 0, "playing": False, "changed": 0, "last_start": 0},
        {"key": "zone_13", "name": "Den", "rows": [row(130, "roon", "D")], "current": 130, "playing": True, "changed": 300, "last_start": 40},
    ]
    print("  0.3.25 (Michel: \"the same in all rooms ... but NOT in all services\"; Mycroft 8712)")
    k, a, why = media.voice_room(units, ROON_BAD)
    check("said without a room, three rooms playing: REFUSED, the reason names them, nothing guessed (0.3.24 chose the Den)",
          k == "" and a == 0 and "several rooms are playing" in why and "Den" in why and "Kitchen" in why, why)
    for u in units:
        u["playing"] = False
    k, a, why = media.voice_room(units, ROON_BAD)
    check("nothing playing: REFUSED - no 'last started', no default, no 'first that can'",
          k == "" and "nothing is playing" in why, why)
    units[1]["playing"] = True
    check("exactly ONE room playing (Secondary, on 122, a Roon activity): there, on its running activity",
          media.voice_room(units, ROON_BAD) == ("zone_12", 122, ""))
    units[1]["playing"], units[0]["playing"] = False, True
    k, a, why = media.voice_room(units, ROON_BAD)
    check("the one room playing is the Kitchen (Sonos only): a Roon favourite is REFUSED there, never sent to another room",
          k == "" and "Kitchen has no activity for Roon" in why, why)
    check("activity_in_room: the running activity when it is the favourite's service",
          media.activity_in_room(units[1], ROON_BAD) == 122)
    units[1]["current"] = 0
    check("activity_in_room: else the room's activity of that service (the room is switched)",
          media.activity_in_room(units[1], ROON_BAD) == 120)
    check("activity_in_room: no activity of that service in the room: 0", media.activity_in_room(units[0], ROON_BAD) == 0)
    variants = [{"activity": 20, "service": "sonos", "play": SONOS_THRILLER, "title": "Thriller", "subtitle": "Michael Jackson",
                 "type": "Album", "content_type": "music"},
                {"activity": 120, "service": "roon", "play": ROON_THRILLER, "title": "Thriller", "subtitle": "Michael Jackson",
                 "type": "Album", "content_type": "music"},
                {"activity": 122, "service": "roon", "play": ROON_THRILLER, "title": "Thriller", "subtitle": "Michael Jackson",
                 "type": "Album", "content_type": "music"}]
    kept = media.dedupe_per_service(variants)
    check("the same record on Sonos and on Roon: BOTH kept, each its own service's play (0.3.24 kept one winner)",
          [(r["service"], r["play"]["source"]) for r in kept] == [("sonos", "sonos_favorite"), ("roon", "music_album")])
    check("...and within one service still one (two Roon activities, one favourite)", len(kept) == 2)
    check("a play's service from its source, never translated", media.service_of_play(SONOS_THRILLER) == "sonos"
          and media.service_of_play(ROON_THRILLER) == "roon" and media.service_of_play(ALIEN) == "movies")
    zid = {"kind": "music", "source": "zidoo_album", "key": "zidoo:9:" + "a" * 40, "title": "X"}
    units[1]["playing"], units[0]["playing"] = True, False
    key, act, why = media.voice_room(units, zid)
    check("the one playing room cannot play it at all: refused, and why", key == "" and act == 0 and "no activity" in why, why)

    print("\nthe migration from per-room favourites")
    old = [{"kind": "room", "id": 2, "activity": 20, "play": SONOS_THRILLER, "name": "Thriller in the Kitchen", "auto": False},
           {"kind": "zone", "id": 12, "activity": 120, "play": SONOS_THRILLER, "name": "Thriller in the Secondary", "auto": False},
           {"kind": "room", "id": 2, "activity": 20, "play": SONOS_RADIO, "name": "My radio", "auto": True}]
    mig = voice.migrate_favorites(old, lambda f: f["name"].endswith(" in the Kitchen") or f["name"].endswith(" in the Secondary"))
    check("one per favourite: the first room's copy moves, the other room's goes",
          [voice.favorite_key(f["play"]) for f in mig["favorites"]] == [voice.favorite_key(SONOS_THRILLER), voice.favorite_key(SONOS_RADIO)]
          and len(mig["moved"]) == 2 and len(mig["retired"]) == 1 and mig["retired"][0]["id"] == 12)
    check("a generated room name is dropped (the house-wide one is suggested); a typed one stays",
          mig["favorites"][0]["name"] is None and mig["favorites"][1]["name"] == "My radio" and mig["favorites"][1]["auto"] is True)


def fixture() -> S.FakeControl:
    control = S.house(S.FakeControl())
    rows = {a["id"]: a for a in control.activities}
    rows[20].update(music_service="sonos", music_where="Kitchen", movie_player=0)
    rows[120].update(music_service="roon", music_where="Secondary", movie_player=0)
    rows[112].update(music_service="", music_where="", movie_player=7)
    control.activities.append(dict(S.activity(122, "Roon Background", zone_id=12, room_id=1),
                                   music_service="roon", music_where="Secondary", movie_player=0))
    for a in control.activities:
        a.setdefault("music_service", "")
        a.setdefault("music_where", "")
        a.setdefault("movie_player", 0)
    control.favorites[20] = {"service": "sonos", "items": [heart(SONOS_THRILLER, "Thriller", subtitle="Spotify"),
                                                           heart(SONOS_RADIO, "Radio 2", "Radio"),
                                                           heart(SONOS_THRILLER_PL, "Thriller", "Playlist", subtitle="Spotify")]}
    control.favorites[120] = {"service": "roon", "items": [heart(ROON_BAD, "Bad", subtitle="MJ")]}
    control.favorites[122] = {"service": "roon", "items": [heart(ROON_BAD, "Bad", subtitle="MJ")]}
    control.favorites[112] = {"service": "movies", "items": [heart(ALIEN, "Alien", "Movie", subtitle="1979")]}
    return control


async def runtime() -> None:
    control = fixture()
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    hass.data["cloud"] = S.cloud(alexa=True, google=False)
    hass, entry, control = await S.setup_integration(control, hass=hass)
    integration = sys.modules["artiled_control"]
    coordinator = lambda: hass.data["artiled_control"]["e1"]["coordinator"]  # noqa: E731

    print("\nthe Favourites tab: ONE list for the whole Control")
    conn = S.FakeConnection()
    await hass.ws["artiled_control/favorites"](hass, conn, {"id": 1, "type": "artiled_control/favorites",
                                                             "entity_id": "media_player.kitchen"})
    result = conn.results.get(1) or {}
    items = result.get("items") or []
    check("every room's hearts, each once (Bad hearted by two Roon activities: once), no room grouping",
          [(i["title"], i["type"]) for i in items] ==
          [("Alien", "Movie"), ("Bad", "Album"), ("Thriller", "Album"), ("Radio 2", "Radio"), ("Thriller", "Playlist")]
          or sorted((i["title"], i["type"]) for i in items) ==
          sorted([("Alien", "Movie"), ("Bad", "Album"), ("Thriller", "Album"), ("Radio 2", "Radio"), ("Thriller", "Playlist")]),
          str([(i["title"], i["type"]) for i in items]))
    by = {(i["title"], i["type"]): i for i in items}
    check("each says the rooms that can play it", by[("Bad", "Album")]["rooms"] == ["Secondary"]
          and by[("Thriller", "Album")]["rooms"] == ["Kitchen"] and by[("Alien", "Movie")]["rooms"] == ["Living Room Main"])
    check("a suggested name without a room", by[("Thriller", "Album")]["suggest"] == "Thriller")
    check("a Sonos favourite's service is never its artist (\"Spotify\" -> nothing), a real artist stays",
          by[("Thriller", "Album")]["subtitle"] == "" and by[("Bad", "Album")]["subtitle"] == "MJ",
          str((by[("Thriller", "Album")]["subtitle"], by[("Bad", "Album")]["subtitle"])))
    check("pictures through Home Assistant's proxy, never the token",
          all((not i["image"]) or (i["image"].startswith("/api/media_player_proxy/") and S.TOKEN not in i["image"]) for i in items))
    check("the setting is off by default", result.get("all_favorites") is False)

    print("\nkept, then fresh (0.3.24): the hearts are not read again on every look")
    favs_read = lambda: [d for e, d in control.requests if e == "activity_favorites"]  # noqa: E731

    async def look(fresh=False):
        c = S.FakeConnection()
        msg = {"id": 1, "type": "artiled_control/favorites", "entity_id": "media_player.kitchen"}
        if fresh:
            msg["fresh"] = True
        await hass.ws["artiled_control/favorites"](hass, c, msg)
        return c.results.get(1) or {}

    control.requests.clear()
    again = await look()
    check("a second look within a minute: no /activity_favorites at all, the same list, not marked kept",
          not favs_read() and len(again.get("items") or []) == len(items) and again.get("kept") == 0, str(favs_read()))
    fav_mod = sys.modules["artiled_control.favorites"]
    real_now = fav_mod._now
    fav_mod._now = lambda: real_now() + 600
    control.requests.clear()
    stale = await look()
    check("ten minutes later: still drawn at once from what was kept, with its age",
          not favs_read() and stale.get("kept", 0) >= 599 and len(stale.get("items") or []) == len(items))
    fresh = await look(fresh=True)
    reads = favs_read()
    check("the page's fresh ask reads every player, once each, side by side (one per player, not per activity)",
          len(reads) == len({r["activity"] for r in reads}) and len(reads) == 3 and fresh.get("kept") == 0, str(reads))
    fav_mod._now = real_now

    print("\none list, grouped per service (0.3.25)")
    control.favorites[120]["items"].append(heart(ROON_THRILLER, "Thriller", subtitle="Michael Jackson"))
    grouped = await look(fresh=True)
    thr = [(i["service"], i["play"]["source"]) for i in grouped.get("items") or [] if i["title"] == "Thriller" and i["type"] == "Album"]
    check("Thriller on Sonos AND on Roon: both in the list, each under its service with its own play (0.3.24 kept one)",
          sorted(thr) == [("roon", "music_album"), ("sonos", "sonos_favorite")], str(thr))
    check("the services, named as the webapp names them, in the order they come",
          [x["name"] for x in grouped.get("services") or []] == ["Movies", "Roon", "Sonos"]
          or sorted(x["name"] for x in grouped.get("services") or []) == ["Movies", "Roon", "Sonos"], str(grouped.get("services")))
    control.favorites[120]["items"] = [h for h in control.favorites[120]["items"] if h["play"] != ROON_THRILLER]
    await look(fresh=True)

    print("\nadd one by hand: ONE scene, no room in its name")
    reloads = hass.config_entries.reloads
    await hass.services.call("artiled_control", "add_voice_favorite", {"play": SONOS_THRILLER, "title": "Thriller", "type": "Album"})
    await hass.settle()
    control.requests.clear()
    after = await look()
    check("after the add (which reloads the entry) the list is still there at once: kept per Control, not per entry",
          not favs_read() and len(after.get("items") or []) == len(items), str(favs_read()))
    check("added IN PLACE: no reload of the entry (Michel: 'Everything disappears')", hass.config_entries.reloads == reloads)
    scene = hass.entity("scene.thriller")
    check("scene 'Thriller'", scene is not None, str(sorted(e for e in hass.entity_registry.entities if e.startswith("scene."))))
    check("exposed like an added voice activity: Assist + Alexa (on), not Google (off)",
          hass.exposure.get(("conversation", "scene.thriller")) is True and hass.exposure.get(("cloud.alexa", "scene.thriller")) is True
          and hass.exposure.get(("cloud.google_assistant", "scene.thriller")) is False)
    await hass.services.call("artiled_control", "add_voice_favorite", {"play": SONOS_THRILLER_PL, "title": "Thriller", "type": "Playlist"})
    await hass.settle()
    check("a second 'Thriller': 'Thriller playlist'", hass.entity("scene.thriller_playlist") is not None)
    try:
        await hass.services.call("artiled_control", "add_voice_favorite",
                                 {"play": {"kind": "music", "source": "zidoo_album", "key": "zidoo:9:" + "a" * 40, "title": "X"}})
        refused = False
    except S.HomeAssistantError:
        refused = True
    check("a favourite no room can play is refused", refused)

    others = [e for e in hass.entities if e.entity_id.startswith("media_player.")]
    await hass.services.call("artiled_control", "add_voice_favorite", {"play": SONOS_THRILLER, "title": "Thriller", "name": "Thriller tonight"})
    await hass.settle()
    check("a rename: the same entity, its new name, no reload, nothing else rebuilt",
          hass.entity("scene.thriller") is scene and scene.name == "Thriller tonight" and hass.config_entries.reloads == reloads
          and all(e in hass.entities for e in others))
    await hass.services.call("artiled_control", "add_voice_favorite", {"play": SONOS_THRILLER, "title": "Thriller", "name": "Thriller"})
    await hass.settle()

    print("\nsaid without a room: the ONE room playing now, else refused (0.3.25)")

    async def refused_with(coro, words):
        try:
            await coro
        except S.HomeAssistantError as err:
            return words in str(err)
        return False

    control.requests.clear()
    check("nothing playing: the scene is REFUSED with the reason, nothing started (0.3.24 picked the Kitchen)",
          await refused_with(hass.entity("scene.thriller").async_internal_activate(), "nothing is playing in any room")
          and not [d for e, d in control.requests if e == "activity"])
    await hass.services.call("artiled_control", "add_voice_favorite", {"play": ROON_BAD, "title": "Bad"})
    await hass.settle()
    control.zones[1].update(active_activity=122, now_playing={"state": "playing", "title": "Song", "artist": "X"})
    await S.poll(hass)
    control.requests.clear()
    await hass.entity("scene.bad").async_internal_activate()
    check("a Roon activity PLAYS in Secondary (the one room playing): the play goes there, on it - no other start",
          [d for e, d in control.requests if e == "activity"] == [{"action": "start", "activity": 122, "play": ROON_BAD}])
    check("...and the scene says where it played", hass.entity("scene.bad").extra_state_attributes.get("played_in") == "Secondary")
    control.requests.clear()
    check("a Sonos favourite while only Secondary (no Sonos activity) plays: REFUSED, never sent to the Kitchen",
          await refused_with(hass.entity("scene.thriller").async_internal_activate(), "Secondary has no activity for Sonos")
          and not [d for e, d in control.requests if e == "activity"])
    control.rooms[1].update(active_activity=20, now_playing={"state": "playing", "title": "Radio", "artist": "Y"})
    await S.poll(hass)
    control.requests.clear()
    check("two rooms playing: REFUSED, naming both - say the room",
          await refused_with(hass.entity("scene.bad").async_internal_activate(), "several rooms are playing")
          and not [d for e, d in control.requests if e == "activity"])
    control.rooms[1].update(active_activity=21, now_playing=None)
    control.zones[1].update(active_activity=121, now_playing=None)
    await S.poll(hass)

    print("\nfrom a room's page: THAT room, the favourite's own service (artiled_control/play_favorite)")

    async def tap(entity, play):
        c = S.FakeConnection()
        await hass.ws["artiled_control/play_favorite"](hass, c, {"id": 1, "type": "artiled_control/play_favorite",
                                                                 "entity_id": entity, "play": play})
        return c.results.get(1), c.errors.get(1)

    control.requests.clear()
    res, err = await tap("media_player.secondary", ROON_BAD)
    check("Secondary is off: switched to its Roon activity with the play - the tap is the choice, no confirm",
          err is None and [d for e, d in control.requests if e == "activity"] == [{"action": "start", "activity": 120, "play": ROON_BAD}]
          and res["room"] == "Secondary", str((res, err)))
    control.requests.clear()
    res, err = await tap("media_player.kitchen", ROON_BAD)
    check("the Kitchen has no Roon activity: refused with the reason, NOTHING sent (no other room)",
          err is not None and "Kitchen has no activity for Roon" in err[1] and not [d for e, d in control.requests if e == "activity"],
          str(err))
    control.zones[1].update(active_activity=122, now_playing={"state": "playing", "title": "Song", "artist": "X"})
    await S.poll(hass)
    control.requests.clear()
    await tap("media_player.secondary", ROON_BAD)
    check("its running activity plays the service: played there, on it",
          [d for e, d in control.requests if e == "activity"] == [{"action": "start", "activity": 122, "play": ROON_BAD}])
    control.zones[1].update(active_activity=121, now_playing=None)
    await S.poll(hass)
    await hass.services.call("artiled_control", "add_voice_favorite", {"play": ALIEN, "title": "Alien", "type": "Movie"})
    await hass.settle()

    print("\nremove one: cleanly")
    uid = next(e.unique_id for e in hass.entity_registry.entities.values() if e.entity_id == "scene.thriller_playlist")
    hass.log.clear()
    reloads = hass.config_entries.reloads
    await hass.services.call("artiled_control", "remove_voice_favorite", {"play": SONOS_THRILLER_PL})
    await hass.settle()
    check("removed IN PLACE: no reload; the entity itself is gone", hass.config_entries.reloads == reloads
          and hass.entity("scene.thriller_playlist") is None)
    check("gone from the registry, un-exposed first, no deleted-entry record",
          "scene.thriller_playlist" not in hass.entity_registry.entities
          and any(e[0] == "expose" and e[2] == "scene.thriller_playlist" and e[3] is False for e in hass.log)
          and not any(k[2] == uid for k in hass.entity_registry.deleted_entities))

    print("\nthe setting: one scene per favourite of the house")
    reloads = hass.config_entries.reloads
    await hass.services.call("artiled_control", "set_voice_favorites_all", {"on": True})
    await hass.settle()
    check("on: the new scenes added in place - no reload storm", hass.config_entries.reloads == reloads)
    scenes = sorted(e for e in hass.entity_registry.entities if e.startswith("scene."))
    check("every favourite once: Thriller (by hand), Bad (by hand), Alien (by hand), Radio 2 and the playlist added",
          {"scene.thriller", "scene.bad", "scene.alien", "scene.radio_2", "scene.thriller_playlist"} <= set(scenes)
          and not any(s.endswith("_2") and s != "scene.radio_2" for s in scenes), str(scenes))
    print("\nper kind (Michel 2026-10-26): Albums, Playlists, Artists, Movies - one switch each")
    conn = S.FakeConnection()
    await hass.ws["artiled_control/favorites"](hass, conn, {"id": 5, "type": "artiled_control/favorites", "entity_id": "media_player.kitchen"})
    res = conn.results.get(5) or {}
    tabs = {(i["title"], i["type"]): i["tab"] for i in res.get("items") or []}
    check("each favourite under its kind: an album, Sonos playlists and radio under Playlists, a film under Movies",
          tabs.get(("Thriller", "Album")) == "albums" and tabs.get(("Thriller", "Playlist")) == "playlists"
          and tabs.get(("Radio 2", "Radio")) == "playlists" and tabs.get(("Alien", "Movie")) == "movies", str(tabs))
    check("the sub-tabs: Albums, Playlists (it has some), Movies - not Artists (none, and the Control is not 2.02)",
          res.get("tabs") == ["albums", "playlists", "movies"], str(res.get("tabs")))
    check("the one switch of before meant every kind it covered", set(res.get("kinds_on") or []) >= {"albums", "movies"})
    reloads = hass.config_entries.reloads
    await hass.services.call("artiled_control", "set_voice_favorites_all", {"on": False, "kind": "playlists"})
    await hass.settle()
    check("Playlists off: its automatic scenes go at once (Radio 2, the playlist), the albums stay, no reload",
          "scene.radio_2" not in hass.entity_registry.entities and "scene.thriller_playlist" not in hass.entity_registry.entities
          and "scene.thriller" in hass.entity_registry.entities and hass.config_entries.reloads == reloads)
    check("...stored per kind", "playlists" not in hass.config_entries.async_get_entry("e1").data.get("all_favorite_kinds")
          and "albums" in hass.config_entries.async_get_entry("e1").data.get("all_favorite_kinds"))
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("...and the next round does not bring them back", "scene.radio_2" not in hass.entity_registry.entities)
    await hass.services.call("artiled_control", "set_voice_favorites_all", {"on": True, "kind": "playlists"})
    await hass.settle()
    check("Playlists on again: they are back", "scene.radio_2" in hass.entity_registry.entities)

    auto = "scene.radio_2"
    hass.exposure[("cloud.alexa", auto)] = False   # the user un-exposes it
    control.favorites[20]["items"] = [h for h in control.favorites[20]["items"] if h["play"] != SONOS_THRILLER_PL]
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("a heart removed on the Control: its automatic scene goes", "scene.thriller_playlist" not in hass.entity_registry.entities)
    check("the user's un-expose stands", hass.exposure.get(("cloud.alexa", auto)) is False)
    control.favorites[20]["items"] = [h for h in control.favorites[20]["items"] if h["play"] != SONOS_THRILLER]
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("one added BY HAND stays when its heart goes", "scene.thriller" in hass.entity_registry.entities)
    control.favorites[112] = (8, "The films could not be read now")
    control.favorites[20]["items"] = [h for h in control.favorites[20]["items"] if h["play"] != SONOS_RADIO]
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("a round with a failed read removes nothing", auto in hass.entity_registry.entities)
    control.favorites[112] = {"service": "movies", "items": [heart(ALIEN, "Alien", "Movie")]}
    control.favorites[20]["items"].append(heart(SONOS_RADIO, "Radio 2", "Radio"))
    await hass.services.call("artiled_control", "remove_voice_favorite", {"name": "Radio 2"})
    await hass.settle()
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("one the user removed is not added back", auto not in hass.entity_registry.entities)

    print("\neditions with 'all favourites' on: one scene per record, named by its plain title")
    control.favorites[120]["items"] = [
        heart({"kind": "music", "source": "music_album", "key": "31", "title": "Antarctica"}, "Antarctica", subtitle="Vangelis"),
        heart({"kind": "music", "source": "music_album", "key": "32", "title": "Antarctica (Remastered)"}, "Antarctica (Remastered)", subtitle="Vangelis"),
        heart({"kind": "music", "source": "music_album", "key": "33", "title": "Blade Runner (Music From The Original Soundtrack)"},
              "Blade Runner (Music From The Original Soundtrack)", subtitle="Vangelis")]
    control.favorites[122] = control.favorites[120]
    await S.poll(hass, inventory=True)
    await hass.settle()
    scenes = sorted(e for e in hass.entity_registry.entities if e.startswith("scene."))
    check("Antarctica once (not 'Antarctica (Remastered)' as well); Blade Runner by its plain title",
          "scene.antarctica" in scenes and not any("remastered" in x or "antarctica_" in x for x in scenes)
          and "scene.blade_runner" in scenes and not any("soundtrack" in x for x in scenes), str(scenes))

    print("\nthe hard cap, with a Repairs note")
    have = len([e for e, r in hass.entity_registry.entities.items() if e.startswith("scene.") and "_voice_favorite_" in r.unique_id])
    integration._FAVORITES_CAP = have + 1
    control.favorites[20]["items"].append(heart({"kind": "music", "source": "sonos_favorite", "key": "Jazz", "title": "Jazz"}, "Jazz", "Radio"))
    control.favorites[20]["items"].append(heart({"kind": "music", "source": "sonos_favorite", "key": "Rock", "title": "Rock"}, "Rock", "Radio"))
    await S.poll(hass, inventory=True)
    await hass.settle()
    favs = [e for e, r in hass.entity_registry.entities.items() if e.startswith("scene.") and "_voice_favorite_" in r.unique_id]
    issue = hass.issues.get("favorites_capped_e1")
    check("never more than the cap; Repairs names how many were left out",
          len(favs) <= have + 1 and issue is not None and (issue.get("translation_placeholders") or {}).get("cap") == str(have + 1)
          and (issue.get("translation_placeholders") or {}).get("left_out") == "1", str((favs, issue)))
    integration._FAVORITES_CAP = 200
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("under the cap again: the note goes", "favorites_capped_e1" not in hass.issues)

    print("\nthe setting off: the automatic ones go, those added by hand stay")
    reloads = hass.config_entries.reloads
    autos = [e for e, r in hass.entity_registry.entities.items() if e.startswith("scene.") and "_voice_favorite_" in r.unique_id
             and e not in ("scene.thriller", "scene.bad", "scene.alien")]
    hass.log.clear()
    await hass.services.call("artiled_control", "set_voice_favorites_all", {"on": False})
    await hass.settle()
    check("off is STORED on the entry at once (live 0.3.13: the entry still said on)",
          hass.config_entries.async_get_entry("e1").data.get("all_favorites") is False)
    check("the automatic ones gone at once, in place - no reload, no waiting for the next round",
          autos and all(a not in hass.entity_registry.entities and hass.entity(a) is None for a in autos)
          and hass.config_entries.reloads == reloads, str(autos))
    check("...each un-exposed first (no Alexa ghost) and no deleted-entry record left",
          all(any(x[:3] == ("expose", "cloud.alexa", a) and x[3] is False for x in hass.log) for a in autos)
          and not any(v.entity_id in autos for v in hass.entity_registry.deleted_entities.values()))
    conn = S.FakeConnection()
    await hass.ws["artiled_control/favorites"](hass, conn, {"id": 9, "type": "artiled_control/favorites", "entity_id": "media_player.kitchen"})
    check("the Voice page reads the stored value back (off)", (conn.results.get(9) or {}).get("all_favorites") is False)
    await S.poll(hass, inventory=True)
    await hass.settle()
    check("...and the next inventory round does not bring them back", all(a not in hass.entity_registry.entities for a in autos))
    scenes = sorted(e for e in hass.entity_registry.entities if e.startswith("scene."))
    check("only the hand-made ones left", {"scene.thriller", "scene.bad", "scene.alien"} <= set(scenes)
          and "scene.jazz" not in scenes, str(scenes))
    import json  # noqa: PLC0415
    strings = json.loads((S.PKG_DIR / "strings.json").read_text())
    check("strings: the cap note, the setting and the default room", "favorites_capped" in strings.get("issues", {})
          and "all_favorites" in json.dumps(strings.get("options", {})) and "favorite_room" not in json.dumps(strings.get("options", {})))
    check("strings = en.json", strings == json.loads((S.PKG_DIR / "translations" / "en.json").read_text()))
    del coordinator
    await S.stop(hass)


async def migration() -> None:
    """0.3.9-0.3.11 stores and scenes, per room."""
    print("\nthe migration from per-room favourites (0.3.9-0.3.11)")
    control = fixture()
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    ident = S.IDENT
    k_thr = voice.favorite_key(SONOS_THRILLER)
    old_kitchen = ids.room_favorite_unique_id(ident, "room", 2, k_thr)
    old_second = ids.room_favorite_unique_id(ident, "zone", 12, k_thr)
    hass.storage["artiled_control_e1_voice"] = {"activities": [], "excluded": ["room_2|" + voice.favorite_key(SONOS_RADIO)], "favorites": [
        {"kind": "room", "id": 2, "activity": 20, "play": SONOS_THRILLER, "name": "Thriller in the Kitchen", "auto": False},
        {"kind": "zone", "id": 12, "activity": 120, "play": SONOS_THRILLER, "name": "Thriller in the Secondary", "auto": True},
    ]}
    reg = hass.entity_registry
    kept = reg.register("scene", "artiled_control", old_kitchen, "e1", "thriller in the kitchen")
    kept.aliases = ["Michael's Thriller"]
    reg.register("scene", "artiled_control", old_second, "e1", "thriller in the secondary")
    hass.exposure[("cloud.alexa", "scene.thriller_in_the_kitchen")] = False   # the user had taken it from Alexa
    hass.storage["artiled_control_e1_flags"] = {"voice_defaults": [old_kitchen, old_second]}
    hass, entry, control = await S.setup_integration(control, hass=hass)
    new_uid = ids.favorite_unique_id(ident, k_thr)
    entry_now = reg.async_get("scene.thriller_in_the_kitchen")
    check("the first room's scene IS the house-wide one now: same entity id, new unique_id",
          entry_now is not None and entry_now.unique_id == new_uid)
    check("...its aliases kept", entry_now is not None and "Michael's Thriller" in (entry_now.aliases or []))
    check("...its exposure kept (not re-applied)", hass.exposure.get(("cloud.alexa", "scene.thriller_in_the_kitchen")) is False)
    check("the other room's copy removed, no deleted-entry record left",
          "scene.thriller_in_the_secondary" not in reg.entities and not any(k[2] == old_second for k in reg.deleted_entities))
    stored = hass.storage["artiled_control_e1_voice"]
    check("the store: one favourite, the house-wide name (the generated room name dropped)",
          [(f.get("name"), "kind" in f) for f in stored["favorites"]] == [("Thriller", False)], str(stored["favorites"]))
    check("an exclusion keeps only the favourite's key", stored["excluded"] == [voice.favorite_key(SONOS_RADIO)], str(stored["excluded"]))
    check("the scene shows the house-wide name", hass.entity("scene.thriller_in_the_kitchen").name == "Thriller")
    await S.stop(hass)


async def kinds_backward() -> None:
    print("\nthe setting as 0.3.9-0.3.14 stored it (one switch): Albums and Movies")
    favs = S.load_pure("voice")
    del favs
    hass, entry, control = await S.setup_integration(fixture())
    hass.config_entries.async_update_entry(entry, data={**entry.data, "all_favorites": True})
    fav_mod = sys.modules["artiled_control.favorites"]
    check("all_favorites on, no kinds stored: albums and movies", fav_mod.kinds_on(entry) == {"albums", "movies"})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "all_favorites": False})
    check("off: none", fav_mod.kinds_on(entry) == set())
    await S.stop(hass)


async def editions_migration() -> None:
    """0.3.13 on the master: 132 automatic scenes, one per EDITION, all with Alexa."""
    print("\n0.3.13's edition scenes merged into one per record, in place")
    control = fixture()
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    ident = S.IDENT
    titles = [("1492: Conquest of Paradise", "1492: Conquest of Paradise"),
              ("1492: Conquest of Paradise [Music from the Original Soundtrack]", "1492: Conquest of Paradise [Music from the Original Soundtrack]"),
              ("1492: Conquest of Paradise", "1492: Conquest of Paradise album"),
              ("Antarctica (Remastered)", "Antarctica (Remastered)"),
              ("Antarctica", "Antarctica"),
              ("Antarctica [Original Motion Picture Soundtrack]", "Antarctica [Original Motion Picture Soundtrack]"),
              ("Avatar", "Avatar"), ("Avatar [Original Motion Picture Soundtrack]", "Avatar [Original Motion Picture Soundtrack]"),
              ("Blade Runner", "Blade Runner"), ("Blade Runner (Music From The Original Soundtrack)", "Blade Runner (Music From The Original Soundtrack)")]
    favs, reg = [], hass.entity_registry
    for n, (title, name) in enumerate(titles):
        play = {"kind": "music", "source": "music_album", "key": str(500 + n), "title": title}
        favs.append({"play": play, "name": name, "auto": True, "title": title})
        e = reg.register("scene", "artiled_control", ids.favorite_unique_id(ident, voice.favorite_key(play)), "e1", name)
        hass.exposure[("cloud.alexa", e.entity_id)] = True
    hass.storage["artiled_control_e1_voice"] = {"activities": [], "favorites": favs, "excluded": []}
    hass.storage["artiled_control_e1_flags"] = {"voice_defaults": [r.unique_id for r in reg.entities.values()]}
    hass, entry, control = await S.setup_integration(control, hass=hass)
    scenes = sorted(e for e in reg.entities if e.startswith("scene."))
    check("one scene per record, the plain-title one kept (its entity id)",
          scenes == ["scene.1492_conquest_of_paradise", "scene.antarctica", "scene.avatar", "scene.blade_runner"], str(scenes))
    check("...still heard by Alexa", all(hass.exposure.get(("cloud.alexa", e)) is True for e in scenes))
    al = reg.async_get("scene.antarctica").aliases or []
    check("the editions' names are its aliases now",
          "Antarctica (Remastered)" in al and "Antarctica [Original Motion Picture Soundtrack]" in al, str(al))
    check("...'1492: Conquest of Paradise album' too",
          "1492: Conquest of Paradise album" in (reg.async_get("scene.1492_conquest_of_paradise").aliases or []))
    gone = "scene.antarctica_remastered"
    check("the others: un-exposed from Alexa FIRST (no ghost device), removed, no deleted-entry record",
          any(x[:3] == ("expose", "cloud.alexa", gone) and x[3] is False for x in hass.log)
          and gone not in reg.entities and not any("antarctica_remastered" in v.entity_id for v in reg.deleted_entities.values()))
    check("done at setup - not a reload per scene", hass.config_entries.reloads == 0)
    check("the store: four favourites", len(hass.storage["artiled_control_e1_voice"]["favorites"]) == 4)
    await S.stop(hass)


async def main() -> None:
    pure()
    await runtime()
    await migration()
    await editions_migration()
    await kinds_backward()


asyncio.run(main())
check.done("voice favourite")
