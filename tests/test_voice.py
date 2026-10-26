#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for the voice choice rules - which stored spoken name a remove or a
re-add replaces, which names clash, and when a choice may start anything.

Run:  python3 tests/test_voice.py     (exit 0 = all pass)

No Home Assistant required; voice.py and units.py are loaded straight from
their paths.

WHY THIS FILE EXISTS. The Hub's voice rules, plus three that are new here:
choices are keyed on the activity ID (a renamed activity must not break what
someone says), spoken names are unique across EVERY Control in the house (an
assistant must find exactly one thing), and a choice whose activity moved, was
disabled or became the Off activity refuses rather than re-binding itself.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ha_stubs import Checker, load_pure  # noqa: E402

voice = load_pure("voice")
units = load_pure("units")
check = Checker()

chosen = [
    {"kind": "zone", "id": 11, "activity": 110, "name": "Netflix in the Living Room"},
    {"kind": "zone", "id": 11, "activity": 112, "name": "Movie Time"},
    {"kind": "room", "id": 11, "activity": 110, "name": "Room eleven"},
    {"kind": "room", "id": 2, "activity": 20, "name": "Sonos in the Kitchen"},
]

print("remove / re-add matches unit + activity ID, only in its own unit")
left = voice.without_entry(chosen, "zone", 11, 110)
check("the entry for zone 11 / activity 110 goes", not any(t["kind"] == "zone" and t["activity"] == 110 for t in left))
check("the other activity in that zone stays", any(t["activity"] == 112 for t in left))
check("the same ids under ROOM 11 stay (a zone and a room can share an id)", any(t["kind"] == "room" and t["id"] == 11 for t in left))
check("exactly one entry is removed", len(left) == len(chosen) - 1)
check("ids given as text still match", len(voice.without_entry(chosen, "zone", "11", "110")) == len(chosen) - 1)
check("no stored list is an empty list", voice.without_entry(None, "zone", 1, 1) == [])
readd = voice.without_entry(chosen, "zone", 11, 110) + [{"kind": "zone", "id": 11, "activity": 110, "name": "Netflix"}]
check("re-adding is a rename, not a duplicate",
      len([t for t in readd if t["kind"] == "zone" and t["id"] == 11 and t["activity"] == 110]) == 1)

print("\nspoken names: ignore case, unique across Controls")
everything = [{**t, "control": "A"} for t in chosen] + [{"kind": "zone", "id": 5, "activity": 50, "name": "Cinema", "control": "B"}]
check("same_name ignores case and outer spaces", voice.same_name(" netflix IN the living room", "Netflix in the Living Room"))
check("a name taken on THIS Control is found", voice.name_taken("movie time", everything, "A", "zone", 11, 999) is not None)
check("a name taken on ANOTHER Control is found too", voice.name_taken("CINEMA", everything, "A", "zone", 11, 110) is not None)
check("the entry being replaced does not clash with itself",
      voice.name_taken("Netflix in the Living Room", everything, "A", "zone", 11, 110) is None)
check("...but the same ids on another Control DO clash",
      voice.name_taken("Cinema", everything, "A", "zone", 5, 50) is not None)
check("a free name is free", voice.name_taken("Something else", everything, "A", "zone", 11, 110) is None)
check("default spoken name", voice.default_spoken_name("Netflix", "Living Room Main") == "Netflix in the Living Room Main")
check("default spoken name drops the activity's verb", voice.default_spoken_name("Listen to Sonos", "Kitchen") == "Sonos in the Kitchen")

print("\nthe long form keeps the verb (Michel 2026-10-26)")
check("long form: the full activity name, 'the' before the room",
      voice.long_spoken_name("Listen to Sonos", "Kitchen") == "Listen to Sonos in the Kitchen"
      and voice.long_spoken_name("Netflix", "The Den") == "Netflix in The Den")
taken = lambda name: voice.same_name(name, "Zidoo in the Kitchen")  # noqa: E731
check("suggestion: the short form while it is free",
      voice.suggest_spoken_name("Listen to Zidoo", "Kitchen", lambda n: False) == "Zidoo in the Kitchen")
check("suggestion: the short form taken (Listen to / Watch Zidoo) -> the verb stays",
      voice.suggest_spoken_name("Watch Zidoo", "Kitchen", taken) == "Watch Zidoo in the Kitchen")
check("alias: the long form when it differs from the spoken name",
      voice.long_alias("Sonos in the Kitchen", "Listen to Sonos in the Kitchen", [], lambda n: False)
      == "Listen to Sonos in the Kitchen")
check("alias: none when it IS the spoken name (any case)",
      voice.long_alias("listen to sonos in the kitchen", "Listen to Sonos in the Kitchen", [], lambda n: False) is None)
check("alias: none when the entity already has it (any case)",
      voice.long_alias("Sonos in the Kitchen", "Listen to Sonos in the Kitchen", ["LISTEN TO SONOS IN THE KITCHEN"],
                       lambda n: False) is None)
check("alias: none when something else answers to it",
      voice.long_alias("Sonos in the Kitchen", "Listen to Sonos in the Kitchen", [], lambda n: True) is None)

print("\nduplicate activity names: the ' (2)' rule never collides with a real 'X (2)'")
labels = units.unique_labels(["X", "X", "X (2)"])
check("X, X, X (2) -> three different labels", len({lbl.lower() for lbl in labels}) == 3, str(labels))
check("the real 'X (2)' keeps its own name", labels[2] == "X (2)")
src = units.source_list([{"id": 1, "name": "X"}, {"id": 2, "name": "X"}, {"id": 3, "name": "X (2)"}], 0)
check("each label still starts its own id", [i for _, i in src] == [1, 2, 3] and src[1][0] == "X (3)", str(src))

print("\nbinding: never re-bound silently")
offered_units = {"zone_11", "room_2"}
acts = {"zone_11": [{"id": 110}, {"id": 112}], "room_2": [{"id": 20}]}
off = {"zone_11": 111, "room_2": 21}
check("a valid choice has no problem", voice.binding_problem(chosen[0], offered_units, acts, off) is None)
check("the unit no longer offered -> 'unit'", voice.binding_problem(chosen[0], {"room_2"}, acts, off) == "unit")
check("the activity moved/disabled/deleted -> 'activity'",
      voice.binding_problem(chosen[0], offered_units, {"zone_11": [{"id": 112}]}, off) == "activity")
check("the activity is now the unit's Off -> 'off'",
      voice.binding_problem(chosen[0], offered_units, acts, {"zone_11": 110}) == "off")

print("\nthe integration uses these rules")
init = (pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "artiled_control" / "__init__.py").read_text()
check("__init__.py takes its voice rules from voice.py",
      "from .voice import long_alias, long_spoken_name, name_taken, same_name, suggest_spoken_name, without_entry" in init)
check("both voice services go through without_entry", init.count("without_entry(") == 2)
favs = (pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "artiled_control" / "favorites.py").read_text()
check("the whole store is written - activities, favourites and exclusions together - then the scenes follow IN PLACE "
      "(Michel 2026-10-26: no reload of the entry for one voice scene)",
      'data["voice_activities"] = chosen' in init and '"activities": data.get("voice_activities")' in favs
      and '"favorites": data.get("voice_favorites")' in favs and "await place(hass, data, ArtiControlVoiceActivity(data, choice))" in init
      and "await place(hass, data, ArtiControlVoiceFavorite(data, fav))" in favs)
scene = (pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "artiled_control" / "scene.py").read_text()
check("voice scenes are built from stored choices only", 'data["voice_activities"]' in scene)
check("the user's name is used verbatim", "_attr_has_entity_name = False" in scene)

check.done("voice")
