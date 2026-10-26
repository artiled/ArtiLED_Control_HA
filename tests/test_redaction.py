#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests that the Control's token never leaves the integration: not in
diagnostics, not in a log line, not in an exception, not in an attribute.

Run:  python3 tests/test_redaction.py     (exit 0 = all pass)

⚠ WHY THIS FILE EXISTS. The token is the Control's box-wide key - whoever has
it can start any activity in the house. Diagnostics get pasted into forum
posts and support mails; log lines end up in bug reports; an art URL only
loads with token= in it. Each of those paths is exercised here with a
sentinel token, and every captured byte is searched for it.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
SENTINEL = S.TOKEN


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage() + (str(record.exc_info) if record.exc_info else ""))


async def main():
    capture = Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.DEBUG)

    control = S.house(S.FakeControl())
    control.zones[0].update(active_activity=110, now_playing={
        "state": "playing", "title": "Dune", "artist": "", "synopsis": "A long synopsis", "lyrics": "la la",
        "image_url": f"/api/unit_player_art?kind=roon&key=ab&token={SENTINEL}", "updated": 1.0, "position": 3})
    hass, entry, control = await S.setup_integration(control)
    S.load_package()
    import importlib
    diagnostics = importlib.import_module("artiled_control.diagnostics")
    api = importlib.import_module("artiled_control.api")

    print("diagnostics")
    body = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    text = str(body)
    check("the token is not in the diagnostics", SENTINEL not in text)
    check("the entry's token field says REDACTED", body["entry"]["token"] == "**REDACTED**")
    check("a token= inside a URL is redacted too", "token=REDACTED" in text)
    check("Now Playing text beyond title/artist is dropped (synopsis, lyrics)", "A long synopsis" not in text and "la la" not in text)
    check("the title is kept (it diagnoses things)", "Dune" in text)
    check("a password or username would be redacted", diagnostics.redact_snapshot({"password": "p", "username": "u"})
          == {"password": "**REDACTED**", "username": "**REDACTED**"})

    print("\nthe redact helper")
    check("token=... in any text", api.redact("http://192.0.2.10/x?a=1&token=abc&b=2") == "http://192.0.2.10/x?a=1&token=REDACTED&b=2")
    check("case-insensitive", "abc" not in api.redact("TOKEN=abc"))

    print("\nerrors and log lines on every failure path")
    control.fail.update({"rooms", "zones"})
    coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    for _ in range(4):
        await coordinator.async_refresh()
    check("a failing poll: the token is in no log line", not any(SENTINEL in line for line in capture.lines))
    check("...and not in the coordinator's exception", SENTINEL not in str(coordinator.last_exception))
    control.fail.clear()
    await coordinator.async_refresh()
    player = hass.entity("media_player.living_room_main")
    control.button_result = f"http error: GET http://192.0.2.10/?token={SENTINEL}"
    try:
        await player.async_media_stop()
        message = ""
    except S.HomeAssistantError as err:
        message = str(err)
    check("a key failure is an error...", "http error" in message)
    check("...whose text never carries the token, even when the device echoed it", SENTINEL not in message)
    control.errors["activity"] = (7, f"refused at http://192.0.2.10/api/x?token={SENTINEL}")
    try:
        await player.async_turn_on()
        message = ""
    except S.HomeAssistantError as err:
        message = str(err)
    check("an API error_text with a token in it is redacted", message and SENTINEL not in message)
    del control.errors["activity"]

    print("\nthe picture")
    control.zones[0]["now_playing"]["image_url"] = "/api/unit_player_art?kind=roon&key=ab"
    await coordinator.async_refresh()
    check("the attribute has no token", "token" not in (player.media_image_url or ""))
    check("no state attribute carries it", SENTINEL not in str(player.extra_state_attributes))
    image, kind = await player.async_get_media_image()
    check("the picture is fetched server-side, with the token, from the Control's own art route",
          image == b"\xff\xd8JPEG" and control.art_requests[-1].endswith(f"&token={SENTINEL}"))
    hass.session.boxes.pop("192.0.2.10")
    image, kind = await player.async_get_media_image()
    check("a failed fetch returns nothing", (image, kind) == (None, None))
    check("...and logs nothing with the token", not any(SENTINEL in line for line in capture.lines))
    hass.session.boxes["192.0.2.10"] = control
    control.zones[0]["now_playing"]["image_url"] = "https://image.example/poster.jpg"
    await coordinator.async_refresh()
    hass.session.outside["https://image.example/poster.jpg"] = b"PNG"
    await player.async_get_media_image()
    outside = [u for m, u in hass.session.log if m == "GET" and "image.example" in u]
    check("an outside picture is fetched WITHOUT the token", outside and all(SENTINEL not in u for u in outside))

    print("\nnothing anywhere")
    check("the token never appeared in any captured log line", not any(SENTINEL in line for line in capture.lines))
    await S.stop(hass)


asyncio.run(main())
check.done("redaction")
