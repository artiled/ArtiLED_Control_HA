#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for identity, address changes and recovery (plan section 4.3, CHA2).

Run:  python3 tests/test_identity.py     (exit 0 = all pass)

The pure rules from identity.py, then the real config flow and setup against
fake Controls at TEST-NET addresses (tests/ha_stubs.py). The UDP listener is
replaced by a direct call of its callback - nothing binds a socket.

⚠ WHY THIS FILE EXISTS. Identity is the Control's ident, never its address.
The Hub integration falls back to the host when there is no ident; copied here
it would give a Control that moved a second set of entities. And a packet on
the network is only a candidate: a different box at our old address must never
be adopted, two boxes claiming one ident must never be guessed between, and a
response from before a reconfigure must never be applied after it.
"""
from __future__ import annotations

import asyncio
import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
idn = S.load_pure("identity")
OLD, NEW, OTHER = "192.0.2.10", "192.0.2.20", "192.0.2.30"

# --------------------------------------------------------------------------
print("qualified identity")
check("a real ident is qualified", idn.qualified("c0ffee000001"))
check("'' is not", not idn.qualified(""))
check("None is not", not idn.qualified(None))
check("the discovery stand-in 000000000000000 is not", not idn.qualified("000000000000000"))
check("verdict same / different / unqualified",
      idn.ident_verdict("a1", "a1") == "same" and idn.ident_verdict("a1", "b2") == "different"
      and idn.ident_verdict("a1", "") == "unqualified")

print("\nthe discovery packet")
p = idn.parse_announcement(b"fna349fn|c0ffee000001|CTRL-001|192.0.2.20|Cinema | Control")
check("parsed", p == {"ident": "c0ffee000001", "type": "CTRL-001", "ip": "192.0.2.20", "name": "Cinema | Control"}, str(p))
check("a wrong magic is ignored", idn.parse_announcement(b"ealaeXa1|c0ffee000001|X|192.0.2.20|N") is None)
check("an all-zeros ident is ignored", idn.parse_announcement(b"fna349fn|000000000000000|X|192.0.2.20|N") is None)
check("garbage is ignored", idn.parse_announcement(b"\xff\xfe") is None and idn.parse_announcement(b"fna349fn") is None)
check("only an address is followed, never a hostname",
      idn.is_ip_literal("192.0.2.10") and not idn.is_ip_literal("control.local"))

print("\nthe tracker: one new address is a candidate, two are refused")
t = idn.FollowTracker(300)
t.heard("c0ffee000001", NEW, 1000.0)
check("one new address -> candidate", t.verdict("c0ffee000001", OLD, 1000.0) == ("candidate", NEW))
t.heard("c0ffee000001", OTHER, 1100.0)
check("a second address within 5 minutes -> ambiguous", t.verdict("c0ffee000001", OLD, 1100.0) == ("ambiguous", None))
check("...and later, once the first has gone quiet, the other is a candidate", t.verdict("c0ffee000001", OLD, 1350.0) == ("candidate", OTHER))
t2 = idn.FollowTracker(300)
t2.heard("x1", OLD, 1000.0)
t2.heard("x1", NEW, 1001.0)
check("the old address still announcing next to a new one -> ambiguous (two boxes, one ident)",
      t2.verdict("x1", OLD, 1001.0)[0] == "ambiguous")
t3 = idn.FollowTracker(300)
t3.heard("x1", OLD, 1000.0)
check("only the stored address -> nothing to do", t3.verdict("x1", OLD, 1000.0) == ("none", None))

print("\nverify_candidate")


async def pure_verify():
    async def alive_ok(a):
        return {"ident": "x1"}

    async def alive_other(a):
        return {"ident": "y2"}

    async def alive_down(a):
        raise OSError("down")

    async def token_ok(a):
        return True

    async def token_refused(a):
        return False

    check("ident matches + token works -> follow", await idn.verify_candidate(NEW, "x1", alive_ok, token_ok) == "follow")
    check("another ident -> different (never adopted)", await idn.verify_candidate(NEW, "x1", alive_other, token_ok) == "different")
    check("unreachable -> unreachable", await idn.verify_candidate(NEW, "x1", alive_down, token_ok) == "unreachable")
    check("our ident but the token refused -> reauth", await idn.verify_candidate(NEW, "x1", alive_ok, token_refused) == "reauth")

asyncio.run(pure_verify())

f = idn.Fence()
g1 = f.bump()
g2 = f.bump()
check("fence: only the newest generation is current", f.is_current(g2) and not f.is_current(g1))


# --------------------------------------------------------------------------
async def flows():
    S.load_package()
    cf = importlib.import_module("artiled_control.config_flow")
    print("\nconfig flow: identity is required and qualified")

    async def run_user(control, data=None):
        hass = S.FakeHass()
        hass.session = S.FakeSession()
        hass.session.boxes[OLD] = control
        flow = cf.ArtiControlConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(data or {"host": OLD, "username": "installer", "password": "secret-password"})
        return hass, flow, result

    _, _, r = await run_user(S.house(S.FakeControl(ident="")))
    check("no ident -> 'This Control has no identity yet'", r["errors"] == {"base": "no_identity"})
    _, _, r = await run_user(S.house(S.FakeControl(ident="000000000000000")))
    check("all-zeros ident -> refused", r["errors"] == {"base": "no_identity"})
    flaky = S.house(S.FakeControl())
    real_answer = flaky.answer

    def changing(endpoint, data, token):
        if endpoint == "zones":
            flaky.ident = "c0ffee000002"
        return real_answer(endpoint, data, token)
    flaky.answer = changing
    _, _, r = await run_user(flaky)
    check("an ident that changes between two /alive calls -> refused", r["errors"] == {"base": "no_identity"})
    old = S.house(S.FakeControl())
    for row in old.rooms + old.zones:
        row.pop("starting_activity")
    _, _, r = await run_user(old)
    check("no starting_activity in /rooms -> 'too old, update the Control first'", r["errors"] == {"base": "too_old"})
    _, _, r = await run_user(S.house(S.FakeControl()), {"host": OLD, "username": "installer", "password": "wrong"})
    check("a wrong password -> invalid_auth", r["errors"] == {"base": "invalid_auth"})
    _, _, r = await run_user(S.house(S.FakeControl()), {"host": "192.0.2.99", "username": "u", "password": "p"})
    check("nobody there -> cannot_connect", r["errors"] == {"base": "cannot_connect"})
    hass, flow, r = await run_user(S.house(S.FakeControl()))
    check("a good Control -> an entry", r["type"] == "create_entry")
    check("unique_id = the ident, never the host", flow.unique_id == S.IDENT)
    check("only host + token + the Assist and dashboard ticks are stored - never the password",
          set(r["data"]) == {"host", "token", "assist_all", "create_dashboard"} and "secret-password" not in str(r["data"]))
    check("the Assist tick is off by default", r["data"]["assist_all"] is False)
    check("the dashboard tick is on by default (S2, as the Hub's #525)", r["data"]["create_dashboard"] is True)

    print("\nthe same Control added again at a new address updates the entry, never a second one")
    hass, entry, control = await S.setup_integration()
    hass.session.boxes[NEW] = control
    flow = cf.ArtiControlConfigFlow()
    flow.hass = hass
    try:
        await flow.async_step_user({"host": NEW, "username": "installer", "password": "secret-password"})
        aborted = False
    except S._Abort as abort:
        aborted = abort.reason == "already_configured"
    check("aborted as already configured", aborted)
    check("...with the entry's host updated", entry.data["host"] == NEW)
    check("still one entry", len(hass.config_entries.entries) == 1)
    await S.stop(hass)

    print("\nreconfigure: a new address for the SAME Control")
    hass, entry, control = await S.setup_integration()
    hass.session.boxes[NEW] = control
    flow = cf.ArtiControlConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "e1"}
    r = await flow.async_step_reconfigure({"host": NEW})
    check("same ident + saved token works -> updated without a login",
          r["reason"] == "reconfigure_successful" and entry.data["host"] == NEW)
    other = S.house(S.FakeControl(ident="d00d00000009"))
    hass.session.boxes[OTHER] = other
    r = await flow.async_step_reconfigure({"host": OTHER})
    check("a DIFFERENT Control at the new address -> refused", r["errors"] == {"base": "token_refused"} or r["errors"] == {"base": "wrong_control"})
    other.token = control.token
    r = await flow.async_step_reconfigure({"host": OTHER})
    check("...even when it would accept our token: wrong_control", r["errors"] == {"base": "wrong_control"})
    check("the entry was not moved to it", entry.data["host"] == NEW)
    moved = S.house(S.FakeControl(token="AnotherTokenAfterAFactoryReset-0001"))
    hass.session.boxes["192.0.2.40"] = moved
    r = await flow.async_step_reconfigure({"host": "192.0.2.40"})
    check("our Control refusing the saved token -> asks for the login", r["errors"] == {"base": "token_refused"})
    r = await flow.async_step_reconfigure({"host": "192.0.2.40", "username": "installer", "password": "secret-password"})
    check("...and with it, updates host + token", entry.data["host"] == "192.0.2.40" and entry.data["token"] == moved.token)
    check("unique_id never changes", entry.unique_id == S.IDENT)

    print("\nre-auth: the same Control only")
    flow = cf.ArtiControlConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "e1"}
    await flow.async_step_reauth(dict(entry.data))
    r = await flow.async_step_reauth_confirm({"username": "installer", "password": "secret-password"})
    check("same ident -> signed in again", r["reason"] == "reauth_successful")
    hass.session.boxes["192.0.2.40"] = S.house(S.FakeControl(ident="d00d00000009"))
    flow = cf.ArtiControlConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "e1"}
    await flow.async_step_reauth(dict(entry.data))
    r = await flow.async_step_reauth_confirm({"username": "installer", "password": "secret-password"})
    check("another Control's login -> wrong_control", r == {"type": "abort", "reason": "wrong_control"})
    await S.stop(hass)


# --------------------------------------------------------------------------
async def setup_cases():
    print("\nsetup: a different Control at the saved address is never adopted")
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    stranger = S.house(S.FakeControl(ident="d00d00000009"))
    hass.session.boxes[OLD] = stranger
    entry = S.ConfigEntry("e1", S.IDENT, {"host": OLD, "token": S.TOKEN, "assist_all": False})
    hass.config_entries.entries["e1"] = entry
    try:
        await hass.config_entries.setup(entry)
        refused = False
    except S.ConfigEntryNotReady:
        refused = True
    check("setup waits (ConfigEntryNotReady)", refused)
    check("a repair issue says a different Control answers there", "wrong_control_e1" in hass.issues)
    check("nothing was read from it but /alive", [e for e, _ in stranger.requests] == ["alive"])
    check("no entities, no devices", not hass.entities and not hass.device_registry.devices)

    print("\naddress follow: moved, same Control -> followed, no new entities")
    ours = S.house(S.FakeControl())
    hass.session.boxes[NEW] = ours
    listener = S._LISTENERS[-1]
    listener({"ident": S.IDENT, "type": "CTRL", "ip": NEW, "name": "x"}, NEW)
    await hass.settle()
    check("the entry follows to the new address", entry.data["host"] == NEW)
    check("and is loaded there", entry.state == S.ConfigEntryState.LOADED)
    check("the repair issue is gone", "wrong_control_e1" not in hass.issues)
    ids_before = sorted(e.unique_id for e in hass.entity_registry.entities.values())
    eids_before = sorted(hass.entity_registry.entities)
    devices_before = len(hass.device_registry.devices)
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("reload after a follow: no duplicate entities", sorted(hass.entity_registry.entities) == eids_before)
    check("...the same unique_ids", sorted(e.unique_id for e in hass.entity_registry.entities.values()) == ids_before)
    check("...no duplicate devices", len(hass.device_registry.devices) == devices_before)
    check("every unique_id is built from the stored ident", all(u.startswith(S.IDENT) for u in ids_before))

    print("\na working entry is never moved by a packet")
    hass.session.boxes[OTHER] = ours
    listener({"ident": S.IDENT, "type": "CTRL", "ip": OTHER, "name": "x"}, OTHER)
    await hass.settle()
    check("still at its working address", entry.data["host"] == NEW)

    print("\ntwo addresses claiming our ident -> refused, nothing changed")
    hass.session.boxes[NEW] = S.house(S.FakeControl(ident="d00d00000009"))
    await S.poll(hass, inventory=True)
    await S.poll(hass, inventory=True)
    await S.poll(hass, inventory=True)
    clone = S.house(S.FakeControl())
    hass.session.boxes["192.0.2.41"] = clone
    hass.session.boxes["192.0.2.42"] = clone
    listener({"ident": S.IDENT, "type": "CTRL", "ip": "x", "name": "x"}, "192.0.2.41")
    listener({"ident": S.IDENT, "type": "CTRL", "ip": "x", "name": "x"}, "192.0.2.42")
    await hass.settle()
    check("the ambiguity is a repair issue", "ambiguous_e1" in hass.issues)
    check("the host did not move to either", entry.data["host"] == NEW)
    await S.stop(hass)

    print("\nour Control at a new address that refuses the saved token -> re-auth for that address")
    hass, entry, control = await S.setup_integration(entry_id="e2")
    control.fail.update({"rooms", "zones", "alive"})
    for _ in range(3):
        await S.poll(hass, "e2")
    hass.session.boxes["192.0.2.50"] = S.house(S.FakeControl(token="AnotherTokenAfterAFactoryReset-0001"))
    S._LISTENERS[-1]({"ident": S.IDENT, "type": "CTRL", "ip": "x", "name": "x"}, "192.0.2.50")
    await hass.settle()
    check("re-auth started with the new address", entry.reauth_started and entry.reauth_started[-1].get("follow_host") == "192.0.2.50")
    check("the host is not changed before the login", entry.data["host"] == OLD)
    await S.stop(hass)

    print("\na hostname is never replaced by an address")
    hass, entry, control = await S.setup_integration(host="control.example", entry_id="e3")
    control.fail.update({"rooms", "zones", "alive"})
    for _ in range(3):
        await S.poll(hass, "e3")
    hass.session.boxes[NEW] = S.house(S.FakeControl())
    S._LISTENERS[-1]({"ident": S.IDENT, "type": "CTRL", "ip": NEW, "name": "x"}, NEW)
    await hass.settle()
    check("still the hostname", entry.data["host"] == "control.example")
    await S.stop(hass)


async def fence_case():
    print("\ncredentials replaced while a response is pending -> the late response is dropped")
    hass, entry, control = await S.setup_integration()
    old_coordinator = hass.data["artiled_control"]["e1"]["coordinator"]
    before = old_coordinator.data["units"]["zone_11"]["raw"]["active_activity"]
    gate = asyncio.Event()
    control.gates["rooms"] = gate
    control.zones[0]["active_activity"] = 110          # what the OLD answer will say
    late = asyncio.ensure_future(old_coordinator.async_refresh())
    await hass.settle()
    moved = S.house(S.FakeControl())
    moved.zones[0]["active_activity"] = 112
    hass.session.boxes[NEW] = moved
    hass.config_entries.async_update_entry(entry, data={**entry.data, "host": NEW})
    await hass.config_entries.async_reload("e1")
    del control.gates["rooms"]
    gate.set()
    await late
    check("the old coordinator did not apply the late answer",
          old_coordinator.data["units"]["zone_11"]["raw"]["active_activity"] == before)
    new = hass.data["artiled_control"]["e1"]["coordinator"]
    check("the new coordinator shows the new host's state", new.data["units"]["zone_11"]["raw"]["active_activity"] == 112)
    check("the player shows the new state", hass.entity("media_player.living_room_main").source == "Kaleidescape")
    await S.stop(hass)


asyncio.run(flows())
asyncio.run(setup_cases())
asyncio.run(fence_case())
check.done("identity")
