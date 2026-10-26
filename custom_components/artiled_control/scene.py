# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Voice activities: one activity, in one unit, under a name someone says.

⚠ START-ONLY (Michel 2026-10-25). Activating one starts that activity; there
is no off. "Turn off the living room" is the unit's media_player, which runs
the unit's Off activity.

A `scene` because it is the activate-only entity with the widest voice support:
Assist matches "activate X" / "turn on X" (HassTurnOn), Alexa and Google treat
it as a Scene. Its state is only "last activated", so it claims no on/off
state it cannot keep.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity_ids import favorite_unique_id, voice_unique_id
from .media import NOT_PLAYED
from .media_player import run_command
from .units import unit_key
from .voice import binding_problem, favorite_key

_PROBLEMS = {
    "unit": "the room is no longer offered on the Control",
    "off": "that activity is now the room's Off activity - turn the room off instead",
    "activity": "that activity is no longer in this room on the Control",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    entities = ([ArtiControlVoiceActivity(data, choice) for choice in data["voice_activities"]]
                + [ArtiControlVoiceFavorite(data, fav) for fav in data.get("voice_favorites") or []])
    # ⚠ Kept, so adding, renaming or removing ONE voice scene changes that one
    # entity in place - no reload of the whole entry (Michel 2026-10-26: "I
    # changed one text on favourites. Everything disappears").
    data["scene_adder"] = async_add_entities
    data["scenes"] = {e.unique_id: e for e in entities}
    async_add_entities(entities)


async def place(hass: HomeAssistant, data: dict, entity) -> None:
    """One voice scene added or renamed in place."""
    await place_many(hass, data, [entity])


async def place_many(hass: HomeAssistant, data: dict, entities: list) -> None:
    """Voice scenes added or renamed IN PLACE - the platform's own
    async_add_entities, the voice defaults applied once to the new ones - so
    a hundred favourites are not a hundred reloads (Michel 2026-10-26)."""
    new = []
    for entity in entities:
        have = (data.get("scenes") or {}).get(entity.unique_id)
        if have is not None:
            have.renamed(entity)
        else:
            new.append(entity)
    adder = data.get("scene_adder")
    if not new or adder is None:   # nothing new, or the platform is not set up: the next setup builds it
        return
    for entity in new:
        data.setdefault("scenes", {})[entity.unique_id] = entity
    adder(new)
    import sys  # noqa: PLC0415

    entry = hass.config_entries.async_get_entry(data["entry_id"])
    await sys.modules[__package__]._voice_defaults_once(hass, entry)


class ArtiControlVoiceActivity(CoordinatorEntity, Scene):
    """One stored voice choice.

    ⚠ OPT-IN, NEVER GENERATED - the Hub's lesson. Every unit x activity pair
    would be hundreds of entities, and `scene` is in Alexa's default exposure;
    an entity nobody can name is an entity nobody will ever say.

    ⚠ THE NAME IS THE USER'S. has_entity_name stays False, or Home Assistant
    would prefix the device name: "Living Room Netflix in the Living Room".

    ⚠ ON NO DEVICE (Michel 2026-10-26, master on HA 2026.10.4). Home
    Assistant 2026.10 names an entity that is on a device "<device> <name>"
    even with has_entity_name False (helpers/entity_registry.py
    async_get_legacy_friendly_name joins the DEVICE and ENTITY parts), and its
    entity_id "<area> <device> <name>" (_async_generate_entity_id). Attached to
    the unit's device it read "Kitchen Listen to Roon in Kitchen" as
    scene.kitchen_kitchen_listen_to_roon_in_kitchen. The spoken name IS the
    whole name, so the scene stands alone; which unit + activity it starts is in
    its unique_id and its attributes, and the dashboard finds it by unique_id.
    """

    _attr_has_entity_name = False

    def __init__(self, data: dict, choice: dict) -> None:
        super().__init__(data["coordinator"])
        self._data = data
        self._client = data["client"]
        self._kind = choice["kind"]
        self._item_id = int(choice["id"])
        self._activity_id = int(choice["activity"])
        self._choice = choice
        self._key = unit_key(self._kind, self._item_id)
        self._attr_name = choice["name"]
        self._attr_unique_id = voice_unique_id(data["ident"], self._kind, self._item_id, self._activity_id)

    def _problem(self) -> str | None:
        data = self.coordinator.data or {}
        units = data.get("units") or {}
        offered = {
            key for key, rows in (data.get("offered") or {}).items()
            if rows and key in units and not units[key]["hidden"]
        }
        off = {key: int(u["raw"].get("off_activity") or 0) for key, u in units.items()}
        return binding_problem(self._choice, offered, data.get("offered") or {}, off)

    @property
    def available(self) -> bool:
        # ⚠ Never re-bound silently: a moved, disabled or deleted activity, or
        # a unit that is no longer offered, makes this unavailable - it does
        # not go looking for something similar.
        return super().available and self._problem() is None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # So the Voice page can tell WHICH unit + activity this is: the name is
        # the user's and the entity_id derives from it, so neither parses back.
        return {"artiled_unit": self._key, "artiled_activity": self._activity_id}

    def renamed(self, other) -> None:
        """The same scene under a new spoken name, in place."""
        self._choice = other._choice
        self._attr_name = other._attr_name
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_activate(self, **kwargs: Any) -> None:
        problem = self._problem()
        if problem is not None:
            # Refused, and nothing is sent.
            raise HomeAssistantError(f"{self._attr_name}: {_PROBLEMS[problem]}")
        name = (self.coordinator.data or {}).get("names", {}).get(self._key) or self._key

        def still_valid() -> str | None:
            # The binding again at dispatch - a command ahead of this one may
            # have waited through an inventory round (HA-S2).
            later = self._problem()
            return _PROBLEMS[later] if later else None

        # Exactly one start; no preflight read, and never an Off.
        await run_command(self._data, self._key, name, lambda: self._client.start_activity(self._activity_id),
                          still_valid=still_valid)


class ArtiControlVoiceFavorite(CoordinatorEntity, Scene):
    """One heart of the Control under ONE spoken name for the whole house
    (Michel 2026-10-26: "favourites are favourites in ANY room"): "Thriller"
    plays it in the ONE room playing now (favorites.play_favorite ->
    media.voice_room; Home Assistant hands a scene no room, so none is
    guessed). Start-only, on no device, as a voice activity."""

    _attr_has_entity_name = False

    def __init__(self, data: dict, fav: dict) -> None:
        super().__init__(data["coordinator"])
        self._data = data
        self._fav = fav
        self._play = fav["play"]
        self._attr_name = fav["name"]
        self._attr_unique_id = favorite_unique_id(data["ident"], favorite_key(self._play))
        self._last: dict | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"artiled_play": self._play, "artiled_automatic": bool(self._fav.get("auto")),
                # where it played last, and what the Control said of it
                "played_in": (self._last or {}).get("room"), "play_state": (self._last or {}).get("state")}

    def renamed(self, other) -> None:
        """The same favourite under a new spoken name, in place."""
        self._fav = other._fav
        self._attr_name = other._attr_name
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_activate(self, **kwargs: Any) -> None:
        from .favorites import play_favorite  # noqa: PLC0415

        room, _, state = await play_favorite(self.hass, self._data, self._play, self._attr_name)
        self._last = {"room": room, "state": state}
        if state in NOT_PLAYED:
            raise HomeAssistantError(f"{self._attr_name}: the Control did not play it in {room} ({state})")
