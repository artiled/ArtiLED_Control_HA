#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for S3c-2: seek, shuffle and repeat via /unit_player (#426).

Run:  python3 tests/test_playback.py     (exit 0 = all pass)

A port of the webapp remote's progress bar, shuffle and repeat
(app/js/playback.js over app/api/playback.php -> player_door_handle, the same
door as /api/unit_player).

⚠ WHY THIS FILE EXISTS.
- Only while the unit's RUNNING activity plays a music service (the remote's
  $app_playback: activity > 0 and music_service != ''), and nothing starts.
- Shuffle and repeat show what the PLAYER says - after every change it is read
  back, never the asked value assumed. A read that did not arrive is NOT the
  old state: unknown, and nothing is offered.
- Seek only while the player says it can (can_seek; radio, HEOS and the Zidoo
  cannot), in whole seconds within the track.
- Read again every 15 s (the remote's PLAYBACK_STATE_MS) and when the running
  activity changes.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
units = S.load_pure("units")
F = S.MediaPlayerEntityFeature


def sent(control, endpoint):
    return [d for e, d in control.requests if e == endpoint]


async def refuses(coro, words=""):
    try:
        await coro
    except S.HomeAssistantError as err:
        return words.lower() in str(err).lower()
    return False


def pure() -> None:
    print("pure rules (units.py)")
    rows = [dict(S.activity(120, "Roon", zone_id=12), music_service="roon", music_where="Z"),
            dict(S.activity(110, "TV", zone_id=12), music_service="", music_where="")]
    raw = {"active_activity": 120, "starting_activity": 0, "off_activity": 121}
    check("a running music activity: its player's state is read", units.playback_activity(raw, rows) == 120)
    check("a running activity without a music service: not", units.playback_activity(dict(raw, active_activity=110), rows) == 0)
    check("off: not", units.playback_activity(dict(raw, active_activity=121), rows) == 0)
    check("while something starts: not", units.playback_activity(dict(raw, starting_activity=110), rows) == 0)
    check("an activity not in the rows: not", units.playback_activity(dict(raw, active_activity=999), rows) == 0)
    take = units.playback_view
    check("the player's own words (playback.js take)",
          take({"shuffle": True, "repeat": "one", "can_seek": True}) == {"shuffle": True, "repeat": "one", "can_seek": True})
    check("null / missing / nonsense = unknown",
          take({"shuffle": None, "repeat": "sometimes"}) == {"shuffle": None, "repeat": None, "can_seek": False})
    check("no answer = unknown", take(None) == {"shuffle": None, "repeat": None, "can_seek": False})
    check("seek seconds: whole, within the track (playback.js seekSeconds)",
          units.seek_seconds(42.6, 200) == 43 and units.seek_seconds(-5, 200) == 0 and units.seek_seconds(500, 200) == 200)


async def runtime() -> None:
    control = S.house(S.FakeControl())
    rows = {a["id"]: a for a in control.activities}
    rows[120].update(music_service="roon", music_where="Secondary")
    control.zones[1].update(active_activity=120,
                            now_playing={"state": "playing", "title": "Song", "artist": "A", "duration": 200,
                                         "position": 30, "updated": 1_000_000})
    control.player_state[("zone", 12)] = {"state": "playing", "shuffle": False, "repeat": "off", "can_seek": True,
                                          "position": 30, "duration": 200, "controllable": True}
    hass, entry, control = await S.setup_integration(control)
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    secondary = hass.entity("media_player.secondary")
    main = hass.entity("media_player.living_room_main")

    print("\nthe player's state is read for a running music activity")
    await coordinator.async_refresh()
    await hass.settle()
    reads = sent(control, "unit_player")
    check("/unit_player state for that unit", {"zone": 12, "action": "state"} in reads, str(reads))
    check("not for a unit without a music activity", all(r.get("zone") != 11 for r in reads))
    check("shuffle and repeat as the player says", secondary.shuffle is False and secondary.repeat == "off")
    feats = secondary.supported_features
    check("shuffle, repeat and seek offered", F.SHUFFLE_SET in feats and F.REPEAT_SET in feats and F.SEEK in feats)
    check("none of them on the TV unit", not (main.supported_features & (F.SHUFFLE_SET | F.REPEAT_SET | F.SEEK)))

    print("\nnot read again on every 5-second poll, but every 15 s")
    control.requests.clear()
    await coordinator.async_refresh()
    await hass.settle()
    check("a poll within 15 s reads nothing", not sent(control, "unit_player"))
    secondary._playback_at -= 16
    await coordinator.async_refresh()
    await hass.settle()
    check("after 15 s it is read again", sent(control, "unit_player") == [{"zone": 12, "action": "state"}])

    print("\nshuffle / repeat: sent, then the player's answer read back")
    control.requests.clear()
    control.player_answers["shuffle"] = {"state": "playing", "shuffle": True, "repeat": "off", "can_seek": True}
    await secondary.async_set_shuffle(True)
    check("shuffle on -> {unit, shuffle, 'on'}, then a state read",
          sent(control, "unit_player") == [{"zone": 12, "action": "shuffle", "shuffle": "on"}, {"zone": 12, "action": "state"}],
          str(sent(control, "unit_player")))
    check("...and it shows what the player says", secondary.shuffle is True)
    control.requests.clear()
    control.player_answers["repeat"] = {"state": "playing", "shuffle": True, "repeat": "off", "can_seek": True}
    await secondary.async_set_repeat(S.RepeatMode.ALL)
    check("repeat all -> {unit, repeat, 'all'}", sent(control, "unit_player")[0] == {"zone": 12, "action": "repeat", "repeat": "all"})
    check("a player that did not take it shows its own state, not the asked one", secondary.repeat == "off")

    print("\nseek: whole seconds, the bar moves on success")
    control.requests.clear()
    await secondary.async_media_seek(42.6)
    check("seek 42.6 -> 43 s", sent(control, "unit_player") == [{"zone": 12, "action": "seek", "seconds": 43}])
    check("the position shows the seeked second", secondary.media_position == 43)
    control.errors["unit_player"] = (7, "The player did not answer")
    check("a seek the Control refuses is an error, with its words",
          await refuses(secondary.async_media_seek(10), "did not answer"))
    check("a shuffle the Control refuses is an error", await refuses(secondary.async_set_shuffle(False), "did not answer"))
    check("...and the state read after it failing makes it unknown, never the old one",
          secondary.shuffle is None and secondary.repeat is None and not (secondary.supported_features & F.SHUFFLE_SET))
    del control.errors["unit_player"]

    print("\nthe player cannot seek (radio, HEOS, Zidoo)")
    control.player_state[("zone", 12)] = {"state": "playing", "shuffle": None, "repeat": None, "can_seek": False}
    secondary._playback_at -= 16
    await coordinator.async_refresh()
    await hass.settle()
    check("no seek, no shuffle, no repeat offered",
          not (secondary.supported_features & (F.SEEK | F.SHUFFLE_SET | F.REPEAT_SET)))
    control.requests.clear()
    check("a seek is refused, nothing sent", await refuses(secondary.async_media_seek(5), "cannot seek")
          and not [r for r in sent(control, "unit_player") if r.get("action") == "seek"])

    print("\nthe running activity changes -> read at once; off -> nothing")
    control.player_state[("zone", 12)] = {"state": "playing", "shuffle": False, "repeat": "all", "can_seek": True}
    control.zones[1].update(active_activity=121, now_playing=None)
    await coordinator.async_refresh()
    await hass.settle()
    check("off: no playback state, nothing offered", secondary.shuffle is None
          and not (secondary.supported_features & (F.SEEK | F.SHUFFLE_SET | F.REPEAT_SET)))
    control.requests.clear()
    control.zones[1].update(active_activity=120, now_playing={"state": "playing", "title": "S", "duration": 100, "position": 1})
    await coordinator.async_refresh()
    await hass.settle()
    check("started again: read at once, without waiting 15 s", sent(control, "unit_player") == [{"zone": 12, "action": "state"}])
    check("...showing its state", secondary.repeat == "all")

    await S.stop(hass)


async def main() -> None:
    pure()
    await runtime()


asyncio.run(main())
check.done("playback")
