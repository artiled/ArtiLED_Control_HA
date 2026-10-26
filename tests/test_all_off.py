#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for S3c-1: the "All off" button per Control (#426).

Run:  python3 tests/test_all_off.py     (exit 0 = all pass)

⚠ WHY THIS FILE EXISTS. All off is the webapp's house switch Off, ported
(app/api/set.php, entity 'house', state 0): it reads the units FRESH, and
starts the Off ACTIVITY of every unit that has one - hidden units left out,
as the webapp leaves them out - never /activity stop (that skips the Off
steps: TV and amplifier stay on). One unit that fails does not stop the rest;
the failure is reported with the unit's name. And it reaches no voice
assistant until someone exposes it (Q1, the Hub rule).
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()


async def refuses(coro, words=""):
    try:
        await coro
    except S.HomeAssistantError as err:
        return words.lower() in str(err).lower()
    return False


async def main() -> None:
    hass, entry, control = await S.setup_integration()
    ident = control.ident
    button = next((e for e in hass.entities if e.unique_id == f"{ident}_all_off"), None)
    check("one All off button per Control", button is not None)
    if button is None:
        check.done("all-off")
    device = hass.device_registry.async_get_device(identifiers={("artiled_control", ident)})
    reg = hass.entity_registry.async_get(button.entity_id)
    check("on the Control's own device", reg is not None and reg.device_id == device.id)
    check("named All off", button.name == "All off")

    print("\nnot heard by any assistant until someone asks (Q1)")
    for assistant in ("conversation", "cloud.alexa", "cloud.google_assistant"):
        check(f"not exposed to {assistant}", hass.exposure.get((assistant, button.entity_id)) is False)

    print("\npress -> every unit's Off activity, read fresh, hidden units left out")
    control.requests.clear()
    await button.async_press()
    reads = [e for e, _ in control.requests if e in ("rooms", "zones")]
    check("the rooms and zones are read at the press", "rooms" in reads and "zones" in reads)
    starts = [d for e, d in control.requests if e == "activity"]
    check("never /activity stop", all(d.get("action") == "start" for d in starts))
    check("the Off activity of every unit that has one, in Control order (Garage's too; not the hidden Study)",
          [d["activity"] for d in starts] == [111, 121, 21, 311, 40], str(starts))

    print("\na unit without an Off activity has nothing to run")
    control.zones[1].update(off_activity=0)
    control.requests.clear()
    await button.async_press()
    check("...it is skipped, the rest still go",
          [d["activity"] for e, d in control.requests if e == "activity"] == [111, 21, 311, 40])
    control.zones[1].update(off_activity=121)

    print("\none failing unit does not stop the rest, and is named")
    control.activities = [a for a in control.activities if a["id"] != 21]
    control.requests.clear()
    check("an error naming the unit that did not switch off", await refuses(button.async_press(), "Kitchen"))
    check("...after every other unit was sent its Off",
          [d["activity"] for e, d in control.requests if e == "activity"] == [111, 121, 21, 311, 40])

    print("\nthe Control unreachable: nothing guessed")
    control.fail.add("zones")
    control.requests.clear()
    check("refused, saying the Control could not be read", await refuses(button.async_press(), "could not"))
    check("...and nothing started", not [1 for e, _ in control.requests if e == "activity"])
    control.fail.discard("zones")

    await S.stop(hass)

    print("\nthe Control's device in an AREA (the master: button.workshop_control_all_off)")
    hass2 = S.FakeHass()
    hass2.session = S.FakeSession()
    hass2.device_registry.async_get_or_create("e1", {("artiled_control", S.IDENT)}, area="Workshop")
    hass2, entry2, control2 = await S.setup_integration(hass=hass2)
    ids = sorted(e for e in hass2.entity_registry.entities if e.startswith("button."))
    check("the id is button.<control name>_all_off - no area in front", ids == ["button.cinema_control_all_off"], str(ids))
    st = hass2.states.get("button.cinema_control_all_off")
    check("the friendly name has no area either", st is not None and "Workshop" not in st.attributes.get("friendly_name", ""),
          str(st and st.attributes.get("friendly_name")))
    others = [e for e in hass2.entity_registry.entities if "workshop" in e]
    check("no other entity of this Control got the area in front of its id", others == [], str(others))
    if "button.cinema_control_all_off" in hass2.entity_registry.entities:
        hass2.entity_registry.async_update_entity("button.cinema_control_all_off", new_entity_id="button.everything_off")
    await hass2.config_entries.async_reload("e1")
    await hass2.settle()
    check("pinned ONCE: a user's rename afterwards stands", "button.everything_off" in hass2.entity_registry.entities)
    await S.stop(hass2)
    check.done("all-off")


asyncio.run(main())
