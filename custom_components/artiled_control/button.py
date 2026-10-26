# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""The "All off" button per Control (S3c, #426).

A port of the webapp's house switch Off (app/api/set.php, entity "house",
state 0): the units read FRESH, then the Off ACTIVITY of every unit that has
one is started, one after the other. Hidden units are left out, as the webapp
leaves them out. ⚠ Never /activity stop: that skips the Off activity's own
steps (TV and amplifier off) - "off" means what it means in the webapp.

Like everything this integration creates, it reaches no voice assistant until
someone exposes it (_voice_defaults_once, Q1).
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArtiControlAuthError, ArtiControlError
from .const import DOMAIN
from .media_player import ownership_problem, run_command
from .units import _int, unit_names, units_from_lists


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ArtiControlAllOffButton(hass.data[DOMAIN][entry.entry_id])])


class ArtiControlAllOffButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "All off"
    _attr_icon = "mdi:power"

    def __init__(self, data: dict) -> None:
        super().__init__(data["coordinator"])
        self._data = data
        self._attr_unique_id = f"{data['ident']}_all_off"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, data["ident"])})

    async def async_press(self) -> None:
        data = self._data
        client = data["client"]
        problem = ownership_problem(data)
        if problem:
            raise HomeAssistantError(f"All off: {problem}")
        try:
            rooms, zones = await client.rooms(), await client.zones()
        except ArtiControlAuthError as err:
            raise HomeAssistantError(
                "All off: the Control no longer accepts our sign-in. "
                "Sign in again under Settings > Devices & services > ArtiCONTROL."
            ) from err
        except ArtiControlError as err:
            raise HomeAssistantError(f"All off: the Control's rooms could not be read - nothing was sent ({err})") from err
        units = [u for u in units_from_lists(rooms, zones) if not u["hidden"]]
        names = unit_names(units)
        failed: list[str] = []
        sent = 0
        for unit in units:
            off_id = _int(unit["raw"].get("off_activity"))
            if off_id <= 0:
                continue  # a unit without an Off activity has nothing to run
            name = names[unit["key"]]
            try:
                await run_command(data, unit["key"], name,
                                  lambda off_id=off_id: client.start_activity(off_id), confirm=False)
                sent += 1
            except HomeAssistantError as err:
                failed.append(str(err))
        if sent:
            data["coordinator"].confirm()
        if failed:
            raise HomeAssistantError(f"All off: {len(failed)} unit(s) did not switch off - " + "; ".join(failed))
