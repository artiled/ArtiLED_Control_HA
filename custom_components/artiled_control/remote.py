# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""One `remote` entity per Control device (S3c, #426) - see remotes.py.

remote.send_command sends the device's own /commands ids (or their labels)
through /command, each checked BEFORE anything is sent. On / off is the
Control's own power reading; turn_on / turn_off use the device's power_on /
power_off commands when it has them, and refuse otherwise.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArtiControlAuthError, ArtiControlError
from .const import DOMAIN
from .media_player import ownership_problem
from .remotes import command_id, power_on, remote_unique_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    rows = data.get("remote_devices") or []
    client = data["client"]

    async def commands_of(row):
        try:
            return await client.commands(int(row["id"]))
        except ArtiControlError:
            return None  # read again when a command is sent

    lists = await asyncio.gather(*(commands_of(row) for row in rows))
    async_add_entities(ArtiControlRemote(data, row, cmds) for row, cmds in zip(rows, lists))


class ArtiControlRemote(CoordinatorEntity, RemoteEntity):
    _attr_has_entity_name = True
    _attr_name = None
    # Long, and only changes with the device's model: kept out of the recorder.
    _unrecorded_attributes = frozenset({"commands"})

    def __init__(self, data: dict, row: dict, commands: list[dict] | None) -> None:
        super().__init__(data["coordinator"])
        self._data = data
        self._device_id = int(row["id"])
        self._commands = commands
        self._attr_unique_id = remote_unique_id(data["ident"], self._device_id)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._attr_unique_id)})

    @property
    def _row(self) -> dict | None:
        for row in self.coordinator.devices or []:
            if int(row.get("id") or 0) == self._device_id:
                return row
        return None

    @property
    def available(self) -> bool:
        return super().available and self._row is not None

    @property
    def is_on(self) -> bool | None:
        return power_on(self._row)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = self._row or {}
        return {
            "control_device_id": self._device_id,
            "commands": self._commands,
            "not_reachable": bool(int(row.get("down") or 0)),
        }

    async def _commands_now(self) -> list[dict]:
        if self._commands is None:
            try:
                self._commands = await self._data["client"].commands(self._device_id)
            except ArtiControlError as err:
                raise HomeAssistantError(f"{self._name()}: its commands could not be read - nothing was sent ({err})") from err
        return self._commands

    def _name(self) -> str:
        return str((self._row or {}).get("name") or f"Device {self._device_id}")

    async def _send(self, ids: list[str], repeats: int = 1, delay: float = 0.0) -> None:
        problem = ownership_problem(self._data)
        if problem:
            raise HomeAssistantError(f"{self._name()}: {problem}")
        client = self._data["client"]
        for n in range(max(1, repeats)):
            for i, cid in enumerate(ids):
                if (n or i) and delay > 0:
                    await asyncio.sleep(delay)
                try:
                    await client.command(self._device_id, cid)
                except ArtiControlAuthError as err:
                    raise HomeAssistantError(
                        f"{self._name()}: the Control no longer accepts our sign-in. "
                        "Sign in again under Settings > Devices & services > ArtiCONTROL.") from err
                except ArtiControlError as err:
                    raise HomeAssistantError(f"{self._name()}: {cid}: {err}") from err

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        commands = await self._commands_now()
        wanted = [str(c) for c in command]
        ids, unknown = [], []
        for text in wanted:
            cid = command_id(commands, text)
            (ids.append(cid) if cid else unknown.append(text))
        if unknown or not ids:
            # ⚠ Checked BEFORE anything is sent: a typo sends nothing at all.
            raise HomeAssistantError(
                f"{self._name()} has no command {', '.join(unknown) or '(none given)'} - nothing was sent. "
                f"Its commands: {', '.join(str(c.get('id')) for c in commands)}")
        await self._send(ids, int(kwargs.get("num_repeats") or 1), float(kwargs.get("delay_secs") or 0))
        self.coordinator.confirm()

    async def _power(self, cid: str) -> None:
        commands = await self._commands_now()
        if command_id(commands, cid) != cid:
            raise HomeAssistantError(f"{self._name()} has no {cid.replace('_', ' ')} command - nothing was sent")
        await self._send([cid])
        self.coordinator.confirm()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._power("power_on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._power("power_off")
