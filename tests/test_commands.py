#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for which REQUEST each Home Assistant action sends to the Control.

Run:  python3 tests/test_commands.py     (exit 0 = all pass)

The real media_player entities, set up through the real async_setup_entry,
against a fake Control that records every request (tests/ha_stubs.py). No Home
Assistant install, no network.

⚠ WHY THIS FILE EXISTS. Off is the unit's Off activity, never /activity stop:
stop skips the Off activity's own steps (TV and amplifier off), so "off" in
Home Assistant would mean something other than off on the webapp (Michel
2026-10-25, Q5). A unit without an On or Off activity refuses and sends
NOTHING. A source name maps to an activity ID. A key the Control says reached
nothing is an error with the Control's own words, not a silent success.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()


def sent(control, endpoint=None):
    return [(e, d) for e, d in control.requests if endpoint is None or e == endpoint]


async def refuses(coro, words=""):
    try:
        await coro
    except S.HomeAssistantError as err:
        return words.lower() in str(err).lower()
    return False


async def main():
    control = S.house(S.FakeControl())
    control.zones[0].update(active_activity=110, volume=40, volume_display="40", volume_max=60)
    hass, entry, control = await S.setup_integration(control)
    data = hass.data["artiled_control"]["e1"]
    coordinator = data["coordinator"]
    main_zone = hass.entity("media_player.living_room_main")
    kitchen = hass.entity("media_player.kitchen")

    print("on / off / source -> /activity start <id>")
    control.requests.clear()
    await main_zone.async_turn_on()
    check("turn_on starts the On activity", sent(control, "activity")[-1][1] == {"action": "start", "activity": 110})
    await main_zone.async_turn_off()
    check("turn_off starts the Off activity", sent(control, "activity")[-1][1] == {"action": "start", "activity": 111})
    await kitchen.async_turn_off()
    check("turn_off on a room unit starts ITS Off activity", sent(control, "activity")[-1][1]["activity"] == 21)
    await main_zone.async_select_source("Kaleidescape")
    check("select_source starts that activity by id", sent(control, "activity")[-1][1]["activity"] == 112)
    await main_zone.async_select_source("Watch Netflix (2)")
    check("a duplicate name's label starts ITS id, not the first one", sent(control, "activity")[-1][1]["activity"] == 113)
    before = len(control.requests)
    check("an unknown source is an error", await refuses(main_zone.async_select_source("Nope"), "not an activity"))
    check("...and nothing is sent", len(control.requests) == before)
    check("Off is not offered as a source", "Off" not in main_zone.source_list)
    check("never /activity stop, anywhere", all(d.get("action") != "stop" for _, d in sent(control, "activity")))

    print("\nno On / Off activity -> refused, nothing sent (Q5)")
    control.zones[0].update(on_activity=0, off_activity=0)
    await coordinator.async_refresh()
    before = len(control.requests)
    check("turn_on without an On activity: 'No On activity for <unit>'",
          await refuses(main_zone.async_turn_on(), "No On activity for Living Room Main"))
    check("turn_off without an Off activity: 'No Off activity for <unit>'",
          await refuses(main_zone.async_turn_off(), "No Off activity for Living Room Main"))
    check("...and nothing was sent for either", len(control.requests) == before)
    control.zones[0].update(on_activity=110, off_activity=111)
    await coordinator.async_refresh()

    print("\nvolume, mute, transport -> /button with the unit's one number")
    control.requests.clear()
    await main_zone.async_set_volume_level(0.3)
    check("set_volume_level -> volume_30 on zone 11", sent(control, "button")[-1][1] == {"zone": 11, "button": "volume_30"})
    await main_zone.async_volume_up()
    check("volume_up -> volume_up", sent(control, "button")[-1][1]["button"] == "volume_up")
    check("...and the level is read again at once", sent(control)[-1][0] in ("rooms", "zones"))
    await main_zone.async_volume_down()
    check("volume_down -> volume_down", sent(control, "button")[-1][1]["button"] == "volume_down")
    await main_zone.async_mute_volume(True)
    check("mute -> discrete 'mute', never the toggle", sent(control, "button")[-1][1]["button"] == "mute")
    await main_zone.async_mute_volume(False)
    check("unmute -> discrete 'unmute'", sent(control, "button")[-1][1]["button"] == "unmute")
    for method, key in (("async_media_play", "play"), ("async_media_pause", "pause"), ("async_media_stop", "stop"),
                        ("async_media_next_track", "next"), ("async_media_previous_track", "previous")):
        await getattr(main_zone, method)()
        check(f"{method} -> {key}", sent(control, "button")[-1][1]["button"] == key)
    control.rooms[1].update(active_activity=20)
    await coordinator.async_refresh()
    await kitchen.async_media_play()
    check("a room unit's key goes to MINUS its id", sent(control, "button")[-1][1]["zone"] == -2)

    print("\nplay/pause on a toggle source (#570)")
    control.zones[0].update(active_activity=112)
    await coordinator.async_refresh()
    await main_zone.async_media_play_pause()
    check("media_play_pause on a toggle source sends play_pause", sent(control, "button")[-1][1]["button"] == "play_pause")
    await main_zone.async_media_pause()
    check("pause on a toggle-only source sends play_pause", sent(control, "button")[-1][1]["button"] == "play_pause")

    print("\na key that reached nothing is an error, in the Control's words")
    control.button_result = "no routing"
    check("'no routing' -> error naming it", await refuses(main_zone.async_media_stop(), "no routing"))
    control.button_result = "Telnet error: connection refused"
    check("a transport 'error:' -> error", await refuses(main_zone.async_media_stop(), "telnet error"))
    control.button_result = "OK"
    await main_zone.async_media_stop()
    check("anything else counts as delivered", True)

    print("\nan unknown activity makes the inventory due at once")
    coordinator.inventory_due = False
    control.activities = [a for a in control.activities if a["id"] != 110]
    check("a start the Control says is unknown is an error", await refuses(main_zone.async_select_source("Watch Netflix")))
    check("...and the inventory is read on the next poll", coordinator.inventory_due)

    coordinator.cancel_pending()
    await hass.settle()


asyncio.run(main())

print("\nkey_failed: a port of app_remote_key_failed")
S.install()
api = S.load_pure("api")
for reply, failed in (("no routing", True), ("No Command", True), ("0", True), ("error", True),
                      ("unknown command foo", True), ("IR error: busy", True), ("sony discover failed", True),
                      ("something error: x", True), ("ok", False), ("", False), ("PWON", False), ("errorless", False)):
    check(f"'{reply}' -> {'failed' if failed else 'delivered'}",
          api.key_failed({"success": 1, "data": {"result": reply}}) is failed)
check("an envelope with success 0 failed", api.key_failed({"success": 0}))
check("no envelope at all failed", api.key_failed(None))

print("\nthe token goes ONLY to the Control's own art route")
T = "secret"
check("own art route: token added", api.art_request("192.0.2.10", T, "/api/unit_player_art?kind=roon&key=1")
      == ("http://192.0.2.10/api/unit_player_art?kind=roon&key=1&token=secret", True))
check("another relative path: host, no token", api.art_request("192.0.2.10", T, "/media/app_rvolution.jpg")
      == ("http://192.0.2.10/media/app_rvolution.jpg", False))
check("an outside URL: fetched as given, no token", api.art_request("192.0.2.10", T, "https://image.example/p.jpg")
      == ("https://image.example/p.jpg", False))
check("a Roon bridge URL: no token", "secret" not in api.art_request("192.0.2.10", T, "http://192.0.2.77:3001/roonAPI/x")[0])
check("protocol-relative //host: not ours, refused", api.art_request("192.0.2.10", T, "//evil.example/x") is None)
check("api_version reads <major>.<two-digit minor>", api.api_version("2.00") == (2, 0) and api.api_version("") is None)
check("api_at_least compares as integers", api.api_at_least("2.10", (2, 0)) and not api.api_at_least("1.29", (2, 0)))

check.done("command")
