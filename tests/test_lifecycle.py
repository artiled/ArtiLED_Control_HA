#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Inert Home Assistant lifecycle tests: setup, re-auth, unload, removal,
voice exposure, entity ids and the services.

Run:  python3 tests/test_lifecycle.py     (exit 0 = all pass)

The real integration through the real entry points, against the stand-ins in
tests/ha_stubs.py. No Home Assistant install, no network.

⚠ WHY THIS FILE EXISTS. These are the paths a customer meets once and cannot
undo: what reaches their voice assistant on day one, what happens to their
setup when a token is refused, whether removing the integration leaves dead
tiles in their Alexa app, and whether a service call with two Controls in the
house goes to the right one.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
ASSISTANTS = ("conversation", "cloud.alexa", "cloud.google_assistant")


async def refused(coro, words=""):
    try:
        await coro
    except S.HomeAssistantError as err:
        return words.lower() in str(err).lower()
    return False


async def setup_and_voice():
    print("setup")
    hass, entry, control = await S.setup_integration()
    integration = sys.modules["artiled_control"]
    check("loaded", entry.state == S.ConfigEntryState.LOADED)
    players = sorted(e.entity_id for e in hass.entities if e.entity_id.startswith("media_player."))
    check("one media_player per offered unit",
          players == ["media_player.bedroom_main", "media_player.kitchen", "media_player.living_room_main",
                      "media_player.secondary"], str(players))
    control_dev = hass.device_registry.async_get_device({("artiled_control", S.IDENT)})
    check("one Control device", control_dev is not None and control_dev.kwargs["model"] == "ArtiLED Control")
    zone_dev = hass.device_registry.async_get_device({("artiled_control", f"{S.IDENT}_zone_11")})
    check("a zone's device: named, via the Control, suggested into its ROOM's area",
          zone_dev.kwargs["name"] == "Living Room Main" and zone_dev.kwargs["via_device"] == ("artiled_control", S.IDENT)
          and zone_dev.kwargs["suggested_area"] == "Living Room")
    room_dev = hass.device_registry.async_get_device({("artiled_control", f"{S.IDENT}_room_2")})
    check("a room unit's device: suggested into its OWN area", room_dev.kwargs["suggested_area"] == "Kitchen")
    check("the version sensor reads the Control version", hass.states.get("sensor.cinema_control_control_version").state == "1.57")

    print("\nvoice: nothing exposed by default (Q1)")
    for e in hass.entities:
        check(f"{e.entity_id}: off for every assistant",
              all(hass.exposure.get((a, e.entity_id)) is False for a in ASSISTANTS))
    hass.exposure[("conversation", "media_player.kitchen")] = True   # the user exposes one
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("a later user choice stands after a reload (defaults are once per entity)",
          hass.exposure[("conversation", "media_player.kitchen")] is True)

    print("\nservices")
    control.requests.clear()
    await hass.services.call("artiled_control", "start_activity", {"unit": "kitchen", "activity": "listen to sonos"})
    check("start_activity by unit and activity NAME, ignoring case",
          control.requests[-1] == ("activity", {"action": "start", "activity": 20}))
    await hass.services.call("artiled_control", "start_activity", {"entity_id": "media_player.living_room_main", "activity": 112})
    check("start_activity by entity_id and activity id", control.requests[-1][1]["activity"] == 112)
    check("an ambiguous activity name is refused - never the first match",
          await refused(hass.services.call("artiled_control", "start_activity",
                                           {"unit": "Living Room Main", "activity": "Watch Netflix"}), "More than one"))
    check("an unknown unit is refused",
          await refused(hass.services.call("artiled_control", "start_activity", {"unit": "Attic", "activity": "x"}), "No room"))
    check("an Off-only unit is refused (not offered)",
          await refused(hass.services.call("artiled_control", "start_activity", {"unit": "room_4", "activity": "Off"}), "no activity"))

    await hass.services.call("artiled_control", "add_voice_activity", {"unit": "Kitchen", "activity": "Listen to Sonos"})
    await hass.settle()
    stored = hass.storage["artiled_control_e1_voice"]["activities"]
    check("add_voice_activity stores unit + activity ID + the default spoken name",
          stored == [{"kind": "room", "id": 2, "activity": 20, "name": "Sonos in the Kitchen"}], str(stored))
    scene = hass.entity("scene.sonos_in_the_kitchen")
    check("...and the entry reloads so the scene exists now", scene is not None)
    check("the new scene is exposed as its room is right now (Kitchen: Assist only) - adding it is the asking",
          hass.exposure.get(("conversation", "scene.sonos_in_the_kitchen")) is True
          and hass.exposure.get(("cloud.alexa", "scene.sonos_in_the_kitchen")) is False
          and hass.exposure.get(("cloud.google_assistant", "scene.sonos_in_the_kitchen")) is False)
    check("the room's Off activity cannot be given a voice name",
          await refused(hass.services.call("artiled_control", "add_voice_activity", {"unit": "Kitchen", "activity": "Off"})))
    check("a spoken name already in use is refused",
          await refused(hass.services.call("artiled_control", "add_voice_activity",
                                           {"unit": "Secondary", "activity": "Listen to Roon",
                                            "name": "sonos in the kitchen"}), "already"))
    await hass.services.call("artiled_control", "remove_voice_activity", {"name": "SONOS IN THE KITCHEN"})
    await hass.settle()
    check("remove_voice_activity by spoken name, ignoring case", hass.storage["artiled_control_e1_voice"]["activities"] == [])
    check("...and its scene is gone", hass.entity("scene.sonos_in_the_kitchen") is None)

    print("\nentity ids: pinned once, never over a user's rename")
    reg = hass.entity_registry
    reg.async_update_entity("media_player.kitchen", new_entity_id="media_player.my_kitchen")
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("a user's rename stands across a reload", "media_player.my_kitchen" in reg.entities and "media_player.kitchen" not in reg.entities)
    await S.stop(hass)
    return integration


async def assist_tick():
    print("\nthe setup tick 'Let Assist control all rooms': once, Assist only")
    hass, entry, control = await S.setup_integration(assist_all=True)
    players = [e.entity_id for e in hass.entities if e.entity_id.startswith("media_player.")]
    check("every offered room is exposed to Assist", all(hass.exposure.get(("conversation", p)) is True for p in players))
    check("...but not to Alexa or Google", all(hass.exposure.get(("cloud.alexa", p)) is False for p in players))
    check("the sensor is not exposed", hass.exposure.get(("conversation", "sensor.cinema_control_control_version")) is False)
    hass.exposure[("conversation", "media_player.secondary")] = False   # the user un-exposes one
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("the tick is not re-applied on reload: the user's un-expose stands",
          hass.exposure[("conversation", "media_player.secondary")] is False)
    await S.stop(hass)


async def reauth_and_unload():
    print("\nre-auth")
    control = S.house(S.FakeControl())
    control.token = "ATokenThatWasRotatedOnTheControl-01"
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    hass.session.boxes["192.0.2.10"] = control
    entry = S.ConfigEntry("e1", S.IDENT, {"host": "192.0.2.10", "token": S.TOKEN, "assist_all": False})
    hass.config_entries.entries["e1"] = entry
    try:
        await hass.config_entries.setup(entry)
        auth = False
    except S.ConfigEntryAuthFailed:
        auth = True
    check("a refused token at setup -> ConfigEntryAuthFailed (Home Assistant opens the re-auth form)", auth)

    hass, entry, control = await S.setup_integration()
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    control.token = "ATokenThatWasRotatedOnTheControl-01"
    await coordinator.async_refresh()
    check("a refused token while running -> ConfigEntryAuthFailed", isinstance(coordinator.last_exception, S.ConfigEntryAuthFailed))
    control.token = S.TOKEN
    await coordinator.async_refresh()
    stranger = S.house(S.FakeControl(ident="d00d00000009", token="SomeoneElsesTokenEntirely-000001"))
    hass.session.boxes["192.0.2.10"] = stranger
    await coordinator.async_refresh()
    check("...but a DIFFERENT Control refusing it is not a re-auth", isinstance(coordinator.last_exception, S.UpdateFailed))
    check("...it is a repair issue", "wrong_control_e1" in hass.issues)
    hass.session.boxes["192.0.2.10"] = control
    await coordinator.async_refresh()
    check("back to our Control: the issue clears", "wrong_control_e1" not in hass.issues)

    print("\nunload")
    player = hass.entity("media_player.kitchen")
    await player.async_turn_off()
    pending = list(coordinator._pending)
    check("a command leaves confirmations pending", len(pending) == 2)
    await hass.config_entries.unload(entry)
    await hass.settle()
    check("unload cancels them", all(t.cancelled() or t.done() for t in pending) and not coordinator._pending)
    check("the entry's data is gone", "e1" not in hass.data["artiled_control"])
    check("...but the inventory memory survives (an unload is half of every reload)", "e1" in hass.data["artiled_control_memory"])
    await S.stop(hass)


async def removal():
    print("\nremoval: un-expose first, then drop the stores")
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    hass.storage["artiled_control_e1_voice"] = {"activities": [{"kind": "room", "id": 2, "activity": 20, "name": "Sonos time"}]}
    hass, entry, control = await S.setup_integration(hass=hass)
    for e in hass.entities:
        hass.exposure[("cloud.alexa", e.entity_id)] = True
    await hass.config_entries.unload(entry)
    hass.log.clear()
    await sys.modules["artiled_control"].async_remove_entry(hass, entry)
    off = {e[2] for e in hass.log if e[0] == "expose" and e[1] == "cloud.alexa" and e[3] is False}
    check("every entity was un-exposed from Alexa", off >= {"media_player.kitchen", "scene.sonos_time"})
    check("the voice store is deleted", "artiled_control_e1_voice" not in hass.storage)
    check("the flags store is deleted", "artiled_control_e1_flags" not in hass.storage)
    check("the inventory memory is dropped", "e1" not in hass.data.get("artiled_control_memory", {}))


async def two_controls():
    print("\ntwo Controls: a service never guesses")
    hass, e1, c1 = await S.setup_integration()
    c2 = S.house(S.FakeControl(ident="beef00000002", name="Pool House"))
    await S.setup_integration(c2, host="192.0.2.11", entry_id="e2", hass=hass)
    check("both loaded", len(hass.data["artiled_control"]) == 2)
    check("no 'control' with two configured -> refused, naming the choices",
          await refused(hass.services.call("artiled_control", "start_activity", {"unit": "Kitchen", "activity": 20}), "Known:"))
    c2.requests.clear()
    await hass.services.call("artiled_control", "start_activity", {"control": "pool house", "unit": "Kitchen", "activity": 20})
    check("'control' by name picks that one", c2.requests and c2.requests[-1][0] == "activity")
    check("an unknown 'control' is refused",
          await refused(hass.services.call("artiled_control", "start_activity", {"control": "barn", "unit": "Kitchen", "activity": 20}), "No configured"))
    await hass.services.call("artiled_control", "add_voice_activity",
                             {"control": S.IDENT, "unit": "Kitchen", "activity": 20, "name": "Music"})
    await hass.settle()
    check("a spoken name used on the OTHER Control is refused (unique across Controls)",
          await refused(hass.services.call("artiled_control", "add_voice_activity",
                                           {"control": "Pool House", "unit": "Kitchen", "activity": 20, "name": "music"}), "already"))
    ids = [e.unique_id for e in hass.entity_registry.entities.values()]
    check("entities of the two Controls never collide", len(ids) == len(set(ids)))
    await S.stop(hass)


async def callbacks():
    print("\nexceptions raised by real entity callbacks read well and carry no token")
    hass, entry, control = await S.setup_integration()
    player = hass.entity("media_player.living_room_main")
    control.fail.add("activity")
    try:
        await player.async_turn_on()
        text = ""
    except S.HomeAssistantError as err:
        text = str(err)
    check("an unreachable Control on a command -> HomeAssistantError naming the unit", text.startswith("Living Room Main:"))
    check("...with no token in it", S.TOKEN not in text)
    control.fail.clear()
    control.token = "ATokenThatWasRotatedOnTheControl-01"
    try:
        await player.async_turn_on()
        text = ""
    except S.HomeAssistantError as err:
        text = str(err)
    check("a refused token on a command says how to sign in again", "Sign in again" in text)
    await S.stop(hass)


async def main():
    await setup_and_voice()
    await assist_tick()
    await reauth_and_unload()
    await removal()
    await two_controls()
    await callbacks()


asyncio.run(main())
check.done("lifecycle")
