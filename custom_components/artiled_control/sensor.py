# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Diagnostic sensor: Control version."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ArtiControlVersionSensor(data)])


class ArtiControlVersionSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Control version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(self, data: dict) -> None:
        super().__init__(data["coordinator"])
        self._attr_unique_id = f"{data['ident']}_version"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, data["ident"])})

    @property
    def native_value(self) -> str | None:
        # Read on the inventory cycle, so an upgrade of the Control shows here
        # without a reload.
        return self.coordinator.version or None
