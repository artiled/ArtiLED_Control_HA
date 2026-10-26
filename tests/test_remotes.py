#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for S3c-4: one `remote` entity per Control device (#426).

Run:  python3 tests/test_remotes.py     (exit 0 = all pass)

Michel 2026-10-26: one HA remote per Control device, remote.send_command with
the /commands ids, only the devices of offered units, each on its own HA
device named after the Control device, not exposed to voice.

⚠ WHY THIS FILE EXISTS.
- Which devices: the API has no "devices an activity uses", so the room's
  devices as the Control's own unit page lists them (/devices {zone|room}:
  the unit's own plus the shared ones; disabled left out), for the OFFERED
  units only.
- A command is one of the device's /commands ids (or its label), checked
  BEFORE anything is sent: a typo sends nothing.
- No area in front of the id (HA 2026.10 would), no voice assistant.
- Devices come and go with the Control - but a failed /devices read is not
  "the devices are gone".
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
remotes = S.load_pure("remotes")


def dev(did, name, room=0, zone=0, shared=0, disabled=0, power=""):
    return {"id": did, "name": name, "type": 1, "type_name": "Model", "brand": "Brand", "ip": "", "mac": "",
            "room": room, "zone": zone, "shared": shared, "disabled": disabled, "down": 0, "deep_standby": 0,
            "fail_count": 0, "sort": did, "icon": "fa-tv", "power": power, "power_last": power}


DEVICES = [
    dev(1, "Projector", room=1, zone=11, power="on"),
    dev(2, "Kitchen Amp", room=2, power="off"),
    dev(3, "Garage TV", room=4),                # Garage: only an Off activity - not offered
    dev(4, "Rack Switcher", room=1, shared=1),  # shared: on every unit's page
    dev(5, "Old Player", room=1, zone=11, disabled=1),
    dev(6, "Bedroom TV", room=3, zone=31),
    dev(7, "Secondary TV", room=1, zone=12),
    dev(8, "Living Room Light Bar", room=1),    # on the room, which has zones: no unit page shows it
]

COMMANDS = {
    1: [{"id": "power_on", "name": "Power On"}, {"id": "power_off", "name": "Power Off"}, {"id": "menu", "name": "Menu"},
        {"id": "input_hdmi1", "name": "HDMI 1"}],
    2: [{"id": "mute", "name": "Mute"}],
}


def pure() -> None:
    print("pure rules (remotes.py)")
    units = [{"kind": "zone", "id": 11, "key": "zone_11"}, {"kind": "room", "id": 2, "key": "room_2"},
             {"kind": "zone", "id": 31, "key": "zone_31"}, {"kind": "zone", "id": 12, "key": "zone_12"}]
    picked = remotes.unit_devices(DEVICES, units)
    check("the offered units' own devices + the shared ones, disabled left out (/devices #557)",
          [d["id"] for d in picked] == [1, 2, 4, 6, 7], str([d["id"] for d in picked]))
    check("nothing offered: not even the shared ones", remotes.unit_devices(DEVICES, []) == [])
    cmds = COMMANDS[1]
    check("a command by its id", remotes.command_id(cmds, "menu") == "menu")
    check("...or by its label, ignoring case", remotes.command_id(cmds, "hdmi 1") == "input_hdmi1")
    check("an unknown command is None", remotes.command_id(cmds, "warp") is None)
    check("power from the Control: on / off / not known",
          remotes.power_on(DEVICES[0]) is True and remotes.power_on(DEVICES[1]) is False and remotes.power_on(DEVICES[2]) is None)


async def runtime() -> None:
    control = S.house(S.FakeControl())
    control.devices = [dict(d) for d in DEVICES]
    control.commands = {k: list(v) for k, v in COMMANDS.items()}
    hass, entry, control = await S.setup_integration(control)
    ids = sorted(e for e in hass.entity_registry.entities if e.startswith("remote."))

    print("\none remote per device of an offered unit")
    check("Projector, Kitchen Amp, Rack Switcher, Bedroom TV, Secondary TV - no area in front",
          ids == ["remote.bedroom_tv", "remote.kitchen_amp", "remote.projector", "remote.rack_switcher", "remote.secondary_tv"],
          str(ids))
    proj = hass.entity("remote.projector")
    reg = hass.entity_registry.async_get("remote.projector")
    device = hass.device_registry.async_get(reg.device_id) if reg else None
    check("on its own HA device, named after the Control device, under the Control",
          device is not None and device.kwargs.get("name") == "Projector"
          and device.kwargs.get("via_device") == ("artiled_control", control.ident))
    for assistant in ("conversation", "cloud.alexa", "cloud.google_assistant"):
        check(f"not exposed to {assistant}", hass.exposure.get((assistant, "remote.projector")) is False)
    check("on / off from the Control's power", proj.is_on is True and hass.entity("remote.kitchen_amp").is_on is False)
    attrs = proj.extra_state_attributes or {}
    check("its commands listed (id and label)", attrs.get("commands") == COMMANDS[1], str(attrs.get("commands")))

    print("\nsend_command -> /command with the /commands id")
    control.requests.clear()
    await proj.async_send_command(["menu", "HDMI 1"])
    sent = [d for e, d in control.requests if e == "command"]
    check("each command sent by its id, in order",
          sent == [{"device": 1, "command": "menu"}, {"device": 1, "command": "input_hdmi1"}], str(sent))
    control.requests.clear()
    await proj.async_send_command(["menu"], num_repeats=3, delay_secs=0)
    check("num_repeats: sent that many times", len([1 for e, _ in control.requests if e == "command"]) == 3)
    control.requests.clear()
    try:
        await proj.async_send_command(["menu", "warp"])
        refused = False
    except S.HomeAssistantError as err:
        refused = "warp" in str(err)
    check("an unknown command: refused by name, and NOTHING sent - not even the good one",
          refused and not [1 for e, _ in control.requests if e == "command"])
    control.errors["command"] = (8, "The device service could not be reached - nothing was sent")
    try:
        await proj.async_send_command(["menu"])
        said = False
    except S.HomeAssistantError as err:
        said = "could not be reached" in str(err)
    check("the Control's refusal, in its words", said)
    del control.errors["command"]

    print("\nturn on / off: the device's own power commands when it has them")
    control.requests.clear()
    await proj.async_turn_off()
    check("turn_off -> power_off", [d for e, d in control.requests if e == "command"] == [{"device": 1, "command": "power_off"}])
    try:
        await hass.entity("remote.kitchen_amp").async_turn_on()
        refused = False
    except S.HomeAssistantError:
        refused = True
    check("no power command: refused, nothing sent", refused and not [1 for e, d in control.requests if e == "command" and d["device"] == 2])

    print("\ndevices come and go with the Control")
    control.fail.add("devices")
    await S.poll(hass, inventory=True)
    check("a failed /devices read removes nothing", "remote.projector" in hass.entity_registry.entities)
    control.fail.discard("devices")
    control.devices.append(dev(9, "Cinema Lift", room=1, zone=11))
    control.devices = [d for d in control.devices if d["id"] != 7]
    await S.poll(hass, inventory=True)
    await hass.settle()
    ids = sorted(e for e in hass.entity_registry.entities if e.startswith("remote."))
    check("a new device of an offered unit appears; one gone is removed",
          "remote.cinema_lift" in ids and "remote.secondary_tv" not in ids, str(ids))
    log = [e for e in hass.log if (e[0] == "expose" and e[2] == "remote.secondary_tv") or e == ("remove", "remote.secondary_tv")]
    check("...un-exposed from every assistant FIRST, then removed",
          len(log) >= 4 and log[-1] == ("remove", "remote.secondary_tv")
          and {e[1] for e in log[:-1] if e[0] == "expose" and e[3] is False} >= {"conversation", "cloud.alexa", "cloud.google_assistant"},
          str(log))
    check("...and its HA device is gone too",
          hass.device_registry.async_get_device(identifiers={("artiled_control", f"{control.ident}_device_7")}) is None)
    check("the Control and the room devices are untouched",
          hass.device_registry.async_get_device(identifiers={("artiled_control", control.ident)}) is not None
          and "media_player.kitchen" in hass.entity_registry.entities)
    await S.stop(hass)


async def main() -> None:
    pure()
    await runtime()


asyncio.run(main())
check.done("remote")
