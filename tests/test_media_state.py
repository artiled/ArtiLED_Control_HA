#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for what a unit's media_player SHOWS: state, volume, keys, position,
title and artwork.

Run:  python3 tests/test_media_state.py     (exit 0 = all pass)

No Home Assistant required; units.py is loaded straight from its path.

⚠ WHY THIS FILE EXISTS. Each rule here is one a voice assistant or a person
acts on. Assist's "pause" only reaches a player whose state is playing, so a
state we cannot vouch for must never read as playing. A volume of 50 that the
Control made up (before API 2.00) would put a slider in the middle and say it
is true. A position re-published every poll fills the recorder. A cover that
blinks to nothing on every gap looks broken.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ha_stubs import Checker, load_pure  # noqa: E402

units = load_pure("units")
check = Checker()


def raw(active=3, off=9, starting=0, np=None, **extra):
    row = {"active_activity": active, "off_activity": off, "starting_activity": starting, "now_playing": np}
    row.update(extra)
    return row


# --------------------------------------------------------------------------
print("Now Playing state -> media_player state, for every value the daemon writes")
for np_state, expected in (("playing", "playing"), ("paused", "paused"), ("stopped", "on"), ("idle", "on"),
                           ("on", "on"), ("unknown", "on"), ("", "on"), ("buffering", "on")):
    check(f"'{np_state}' -> {expected}", units.media_state(raw(np={"state": np_state})) == expected)
check("no Now Playing at all -> on", units.media_state(raw(np=None)) == "on")
check("off wins over a stale 'playing'", units.media_state(raw(active=9, np={"state": "playing"})) == "off")
check("starting B over a playing A -> ON, never playing (the old activity's state is not carried over)",
      units.media_state(raw(active=3, starting=4, np={"state": "playing"})) == "on")
check("starting Off over a playing A -> off", units.media_state(raw(active=3, starting=9, np={"state": "playing"})) == "off")

print("\nNow Playing is suppressed while off or starting")
np = {"state": "playing", "title": "Dune", "image_url": "http://192.0.2.50/x.jpg"}
check("on: shown", units.now_playing(raw(np=np)) is np)
check("starting: not shown", units.now_playing(raw(starting=4, np=np)) is None)
check("off: not shown", units.now_playing(raw(active=9, np=np)) is None)

print("\ntitle and content type")
check("title first", units.media_title({"title": "Dune", "selected_title": "Alien"}) == "Dune")
check("selected_title when the title is empty (Kaleidescape menu)", units.media_title({"title": "", "selected_title": "Alien"}) == "Alien")
check("nothing -> None", units.media_title({"title": ""}) is None and units.media_title(None) is None)
check("an artist makes it music", units.is_music({"source": "appletv", "artist": "Adele"}))
check("roon / sonos are music without an artist", units.is_music({"source": "roon", "artist": ""}) and units.is_music({"source": "sonos"}))
check("a video source without an artist is a film", not units.is_music({"source": "kaleidescape", "artist": " "}))

# --------------------------------------------------------------------------
print("\nvolume: being able to set it is not knowing it (G2)")
v = units.volume_view(raw(volume=50, volume_display=None, volume_max=60))
check("pre-2.00 volume 50 with display null -> level UNKNOWN (None), never 0.5", v["level"] is None)
check("...and set/step stay offered", v["can_set"])
v = units.volume_view(raw(volume=None, volume_display=None, volume_max=60))
check("2.00 volume null with volume_max -> unknown, still settable", v["level"] is None and v["can_set"])
v = units.volume_view(raw(volume=70, volume_display="42", volume_max=60))
check("a known level -> 0.70", v["level"] == 0.7)
v = units.volume_view(raw(volume=None, volume_display=None, volume_max=None))
check("no volume device -> nothing offered", not v["can_set"] and v["level"] is None)
v = units.volume_view(raw(active=9, volume=70, volume_display="42", volume_max=60))
check("off -> no volume, whatever the fields say", not v["can_set"] and v["level"] is None)
check("muted true/false is read (G1)", units.volume_view(raw(volume=70, volume_display="42", muted=True))["muted"] is True
      and units.volume_view(raw(volume=70, volume_display="42", muted=False))["muted"] is False)
check("muted null or absent -> None (older Control)", units.volume_view(raw(volume=70, volume_display="42", muted=None))["muted"] is None
      and units.volume_view(raw(volume=70, volume_display="42"))["muted"] is None)
check("volume_button rounds to the slider's 0-100", units.volume_button(0.305) == "volume_30" and units.volume_button(1.4) == "volume_100")

# --------------------------------------------------------------------------
print("\ntransport keys follow the running activity's buttons (#570)")
check("buttons null -> every key", units.transport_keys({"buttons": None}) == {"play", "pause", "stop", "next", "previous"})
check("no activity known -> every key", units.transport_keys(None) == {"play", "pause", "stop", "next", "previous"})
check("only the keys it uses", units.transport_keys({"buttons": ["play", "pause", "volume_up"]}) == {"play", "pause"})
check("a toggle source offers play AND pause through play_pause",
      units.transport_keys({"buttons": ["play_pause", "stop"], "play_pause": "toggle"}) >= {"play", "pause", "stop"})
check("no transport buttons -> no transport keys", units.transport_keys({"buttons": ["up", "down"]}) == set())

# --------------------------------------------------------------------------
print("\nposition: published only on news (recorder hygiene)")
base = {"state": "playing", "title": "T", "artist": "A", "album": "", "duration": 300}


def reading(position, updated, **over):
    return units.position_reading({**base, "position": position, "updated": updated, **over})


first = reading(10, 1000.0)
check("the first reading is news", units.position_is_news(None, first))
check("a steady play 5 s later is NOT news", not units.position_is_news(first, reading(15, 1005.0)))
check("a 1.5 s drift is not news", not units.position_is_news(first, reading(16.5, 1005.0)))
check("a seek (jump > 2 s) is news", units.position_is_news(first, reading(60, 1005.0)))
check("pause is news AT ONCE, even without a jump", units.position_is_news(first, reading(15, 1005.0, state="paused")))
paused = reading(15, 1005.0, state="paused")
check("paused and still: not news", not units.position_is_news(paused, reading(15, 1010.0, state="paused")))
check("resume is news", units.position_is_news(paused, reading(15, 1010.0)))
check("a new title is news even under 2 s", units.position_is_news(first, reading(15, 1005.0, title="Other")))
check("unknown (no daemon timestamp) is news", units.position_is_news(first, reading(15, 0)))
check("the timestamp is the DAEMON's, not the poll's", reading(10, 1234.5)["updated"] == 1234.5)
check("stopped carries no position", units.position_reading({**base, "state": "stopped", "position": 5, "updated": 1.0})["position"] is None)

# --------------------------------------------------------------------------
print("\nartwork: live first, kept through a gap, scoped to the activity")
on = raw(active=3, np={"state": "playing", "image_url": "/api/unit_player_art?kind=roon&key=ab"})
art, kept = units.artwork(None, on)
check("live cover shown", art == "/api/unit_player_art?kind=roon&key=ab" and kept == (3, art))
gap = raw(active=3, np={"state": "stopped", "image_url": ""})
art2, kept2 = units.artwork(kept, gap)
check("a gap (empty image_url, same activity) keeps the cover", art2 == art and kept2 == kept)
other = raw(active=4, np={"state": "stopped", "image_url": ""})
check("a NEW activity never inherits it", units.artwork(kept, other) == (None, None))
check("starting shows no picture and drops the kept one", units.artwork(kept, raw(active=3, starting=4, np={"image_url": "x"})) == (None, None))
check("off shows no picture", units.artwork(kept, raw(active=9, np={"image_url": "x"})) == (None, None))

check.done("media state")
