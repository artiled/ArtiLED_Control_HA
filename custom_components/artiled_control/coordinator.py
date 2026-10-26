# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Data update coordinator - polls the Control's units, and follows its
inventory on a slower cycle."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArtiControlAuthError, ArtiControlClient, ArtiControlError, api_at_least, api_version
from .const import (
    API_ALL_ACTIVITIES,
    CONFIRM_DELAYS,
    DOMAIN,
    INVENTORY_INTERVAL_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)
from .identity import Fence, ident_verdict
from .inventory import deletable, presence, signature, snapshot, stamp_seen
from .units import (
    group_all_activities,
    offered_activities,
    unit_activity_rows,
    unit_names,
    units_from_lists,
)

_LOGGER = logging.getLogger(__name__)


class StaleResponse(Exception):
    """A response from before the last reconfigure, re-auth or follow."""


class ArtiControlCoordinator(DataUpdateCoordinator):
    """Two cycles in one.

    State, every 5 s: /rooms + /zones in parallel - on/off, starting, Now
    Playing, volume. The Control computes these at read time, so what we get is
    the truth, never invented.

    Inventory, every 60 s (and at once after setup, after a command that met an
    unknown activity, and when the unit set changes): /alive (still OUR
    Control?), /version, and every unit's activities - one call on API 2.00,
    one per unit before it.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ArtiControlClient,
        control_name: str,
        ident: str,
        memory: dict,
        fence: Fence,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {control_name}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.ident = ident
        self._fence = fence
        self._failures = 0
        self._inventory_at: float | None = None
        self.inventory_due = True
        # Counts inventory rounds (each attempt): what the voice favourites'
        # sync follows.
        self.inventory_round = 0
        # True while a DIFFERENT Control answers at our host. __init__ raises a
        # repair issue on it; address follow starts looking for ours.
        self.wrong_control = False
        self._pending: set[asyncio.Task] = set()
        # The inventory memory: when each unit and activity was last seen, on
        # the last-successful-round clock, plus the last GOOD activities.
        #
        # ⚠ THIS MUST OUTLIVE A RELOAD, so it is owned by hass.data and passed
        # in, not created here - the Hub integration's 2026-08-14 lesson. A
        # reload builds a NEW coordinator; a memory created here would start
        # empty, and since a disappearance is itself what triggers the reload,
        # anything just removed would be deleted on the spot.
        self._memory = memory
        self._memory.setdefault("seen", {})
        self._memory.setdefault("last_success", None)
        self._memory.setdefault("first_success", None)
        self._memory.setdefault("snapshot", None)
        self._memory.setdefault("activities", {})
        self._memory.setdefault("version", "")
        self._memory.setdefault("api", "")

    # ---- what the inventory says -------------------------------------------

    @property
    def inventory(self) -> frozenset | None:
        """What the Control HAS, from the last qualified round. __init__ reloads
        the entry when this moves. None until a round has qualified."""
        return signature(self._memory["snapshot"])

    @property
    def present(self) -> frozenset | None:
        """The only deletable set - see inventory.presence."""
        return presence(self._memory["seen"], self._memory["last_success"], self._memory["first_success"])

    @property
    def deletable(self) -> frozenset:
        return deletable(self._memory["seen"], self.present)

    @property
    def has_inventory(self) -> bool:
        return self._memory["snapshot"] is not None

    @property
    def version(self) -> str:
        return self._memory["version"]

    # ---- confirmation after a command ---------------------------------------

    def confirm(self) -> None:
        """Look again at +1 s and +3 s, so "Starting..." and then the result
        show quickly. State is never set optimistically.

        ⚠ Tracked, and cancelled on unload, reload and removal; a refresh that
        lands after a reconfigure is dropped by the fence anyway.
        """
        generation = self.client.generation
        for delay in CONFIRM_DELAYS:
            task = self.hass.async_create_task(self._later(delay, generation))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def _later(self, delay: float, generation: int) -> None:
        await asyncio.sleep(delay)
        if self._fence.is_current(generation):
            await self.async_refresh()

    def cancel_pending(self) -> None:
        for task in list(self._pending):
            task.cancel()
        self._pending.clear()

    # ---- polling ------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        generation = self.client.generation
        try:
            rooms, zones = await asyncio.gather(self.client.rooms(), self.client.zones())
            if not isinstance(rooms, list) or not isinstance(zones, list):
                raise ArtiControlError("Malformed rooms/zones answer")
            self._check_fence(generation)
            units = units_from_lists(rooms, zones)
            keys = frozenset(u["key"] for u in units)
            snap = self._memory["snapshot"]
            if snap is not None and keys != frozenset(snap["units"]):
                self.inventory_due = True
            if self.wrong_control:
                # Ask /alive again on every poll until it is ours again.
                self.inventory_due = True
            now = time.monotonic()
            if self.inventory_due or self._inventory_at is None or now - self._inventory_at >= INVENTORY_INTERVAL_SECONDS:
                try:
                    await self._read_inventory(units, generation)
                except (ArtiControlAuthError, StaleResponse):
                    raise
                except ArtiControlError as err:
                    if self.wrong_control:
                        raise
                    # A failed inventory read changes nothing - the state
                    # poll above still stands. Tried again next cycle.
                    _LOGGER.debug("Inventory round failed, keeping the last good one: %s", err)
                if not self.wrong_control:
                    await self._read_devices(generation)
        except StaleResponse:
            # ⚠ Dropped, never applied: it answered for the old host or token.
            _LOGGER.debug("Dropped a response from before the last reconfigure")
            if self.data is None:
                raise UpdateFailed("Superseded") from None
            return self.data
        except ArtiControlAuthError as err:
            await self._auth_failed(err)
        except ArtiControlError as err:
            # Keep serving the last known state through brief hiccups; only
            # mark everything unavailable after three failed polls in a row.
            self._failures += 1
            if self._failures < 3 and self.data:
                _LOGGER.debug("Control poll failed (%s/2), keeping last state: %s", self._failures, err)
                return self.data
            raise UpdateFailed(str(err)) from None
        self._failures = 0
        return self._build(rooms, zones, units)

    def _check_fence(self, generation: int) -> None:
        if not self._fence.is_current(generation):
            raise StaleResponse

    async def _auth_failed(self, err: Exception) -> None:
        """Error 2. Our token was refused - or someone else's Control now sits at
        our address and refuses it. Only the first is a re-auth: asking the
        customer to sign in again to a box that is not theirs would be wrong."""
        try:
            alive = await self.client.alive()
        except ArtiControlError:
            alive = {}
        if alive and ident_verdict(self.ident, alive.get("ident")) == "different":
            self.wrong_control = True
            raise UpdateFailed("A different Control answers at this address") from None
        raise ConfigEntryAuthFailed(str(err)) from None

    async def _read_inventory(self, units: list[dict], generation: int) -> None:
        """One inventory round. Only a round in which /alive, /version and EVERY
        unit's activities answered is used; anything less keeps the last good
        inventory, because a failed read is not an empty Control."""
        # Counted as a round whatever it brings, so a failing read is retried
        # on the next inventory cycle, not hammered every five seconds.
        self._inventory_at = time.monotonic()
        self.inventory_due = False
        self.inventory_round += 1
        alive = await self.client.alive()
        if ident_verdict(self.ident, alive.get("ident")) != "same":
            self.wrong_control = True
            raise ArtiControlError("A different Control answers at this address")
        self.wrong_control = False
        version = await self.client.version()
        self.client.api = api_version(version.get("api"))
        per_unit: dict[str, list[dict]] = {}
        if api_at_least(self.client.api, API_ALL_ACTIVITIES):
            rows = await self.client.activities({})
            if not _well_formed(rows):
                _LOGGER.debug("Malformed activities answer, keeping the last good inventory")
                return
            grouped = group_all_activities(rows)
            for unit in units:
                per_unit[unit["key"]] = unit_activity_rows(grouped.get(unit["key"]), unit["kind"])
        else:
            results = await asyncio.gather(
                *[self.client.activities({unit["kind"]: unit["id"]}) for unit in units],
                return_exceptions=True,
            )
            for unit, result in zip(units, results):
                if isinstance(result, ArtiControlAuthError):
                    raise result
                if isinstance(result, BaseException) or not _well_formed(result):
                    _LOGGER.debug("Partial inventory round (%s), keeping the last good one", unit["key"])
                    return
                per_unit[unit["key"]] = unit_activity_rows(result, unit["kind"])
        self._check_fence(generation)
        offered = {
            unit["key"]: offered_activities(per_unit.get(unit["key"]), unit["raw"].get("off_activity"))
            for unit in units
        }
        snap = snapshot(units, per_unit, offered)
        now = time.monotonic()
        self._memory["activities"] = per_unit
        self._memory["snapshot"] = snap
        self._memory["version"] = version.get("version", "")
        self._memory["api"] = version.get("api", "")
        self._memory["last_success"] = now
        if self._memory["first_success"] is None:
            self._memory["first_success"] = now
        stamp_seen(snap, self._memory["seen"], now)

    async def _read_devices(self, generation: int) -> None:
        """S3c: the devices and the Control's own down marks, on the inventory
        cycle (the Control checks them every 5 minutes). ⚠ A failed read
        keeps the last one: it is not "every device answers again"."""
        try:
            rows = await self.client.devices()
            self._check_fence(generation)
        except ArtiControlAuthError:
            raise
        except ArtiControlError as err:
            _LOGGER.debug("Devices read failed, keeping the last one: %s", err)
            return
        self._memory["devices"] = rows

    @property
    def find_when_off(self) -> bool | None:
        """Does this Control search a named activity while a unit's Off runs
        (the /unit_find API extension)? None = not known for its version yet."""
        known = self._memory.get("find_when_off")
        return known[1] if known and known[0] == self.version else None

    @find_when_off.setter
    def find_when_off(self, value: bool) -> None:
        self._memory["find_when_off"] = (self.version, bool(value))

    @property
    def devices(self) -> list[dict] | None:
        """The last /devices read, None when there has been none."""
        return self._memory.get("devices")

    def _note_rooms(self, units: list[dict]) -> None:
        """When each room last changed what it does, and last STARTED an
        activity (Michel 2026-10-26: a house-wide favourite plays where
        something plays now, the most recently changed; else in the room
        started last). From the polls - a start from anywhere counts. Kept in
        the entry memory, so a reload does not forget it. Wall-clock seconds."""
        sigs = self._memory.setdefault("room_sig", {})
        changed = self._memory.setdefault("room_changed", {})
        started = self._memory.setdefault("room_started", {})
        now = time.time()
        for unit in units:
            raw = unit["raw"]
            off = int(raw.get("off_activity") or 0)
            np = raw.get("now_playing") if isinstance(raw.get("now_playing"), dict) else {}
            p, st = int(raw.get("active_activity") or 0), int(raw.get("starting_activity") or 0)
            sig = (p, st, str(np.get("state") or ""), str(np.get("title") or ""))
            before = sigs.get(unit["key"])
            sigs[unit["key"]] = sig
            if before is None or before == sig:
                continue
            changed[unit["key"]] = now
            began = (st > 0 and st != off and st != before[1]) or (p > 0 and p != off and p != before[0])
            if began:
                started[unit["key"]] = now

    def room_times(self, key: str) -> tuple[float, float]:
        """(last change, last start) of a room, 0 when not seen."""
        return (float(self._memory.get("room_changed", {}).get(key) or 0),
                float(self._memory.get("room_started", {}).get(key) or 0))

    def _build(self, rooms: list, zones: list, units: list[dict]) -> dict:
        self._note_rooms(units)
        activities = self._memory["activities"]
        return {
            "rooms": {int(r["id"]): r for r in rooms},
            "zones": {int(z["id"]): z for z in zones},
            "units": {u["key"]: u for u in units},
            "names": unit_names(units),
            "activities": activities,
            "offered": {
                u["key"]: offered_activities(activities.get(u["key"]), u["raw"].get("off_activity"))
                for u in units
            },
        }


def _well_formed(rows) -> bool:
    return isinstance(rows, list) and all(isinstance(r, dict) and "id" in r for r in rows)
