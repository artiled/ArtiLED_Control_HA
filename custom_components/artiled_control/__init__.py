# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""ArtiCONTROL integration - HA is an adapter; the Control stays authoritative.

AV only through Control (Michel 2026-10-25, Q2): customers and resellers reach
AV devices only through the Control. In Home Assistant that means through this
integration, never also through HA's own TV or Sonos integrations - so nothing
here ever looks at, un-exposes or touches another integration's entities.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import slugify

from . import favorites, find, health, identity, remotes
from .api import ArtiControlAuthError, ArtiControlClient, ArtiControlError
from .const import (
    ASSISTANTS,
    CONF_ASSIST_ALL,
    CONF_TOKEN,
    CONTROL_GLYPHS,
    DISCOVERY_PORT,
    DOMAIN,
    FOLLOW_AMBIGUITY_SECONDS,
    FRONTEND_BASE,
    FRONTEND_DIR,
    FRONTEND_URL,
    ICON_BASE,
    ICON_CACHE_DIR,
    PLATFORMS,
    STORAGE_VERSION,
)
from .coordinator import ArtiControlCoordinator
from .dashboard import async_create_dashboard
from .icons import ControlIcons
from .entity_ids import parse_unit_uid, pin_plan, unit_unique_id, voice_unique_id
from .voice import favorite_key
from .identity import FollowTracker, ident_verdict, is_ip_literal, qualified, verify_candidate
from .media_player import run_command
from .units import parse_unit_key, unit_key
from .voice import long_alias, long_spoken_name, name_taken, same_name, suggest_spoken_name, without_entry

_LOGGER = logging.getLogger(__name__)


# The most voice favourite scenes one Control gets (Michel 2026-10-26: Alexa
# and Home Assistant have limits); "Make all favourites available to voice"
# stops there and says how many it left out (Repairs).
_FAVORITES_CAP = 200


def _remote_devices(coordinator, shown) -> list[dict] | None:
    """The Control devices that get a remote: those of the offered units."""
    if coordinator.devices is None:
        return None
    units = [coordinator.data["units"][key] for key in shown if key in (coordinator.data or {}).get("units", {})]
    return remotes.unit_devices(coordinator.devices, units)


def _device_place(data: dict, row: dict) -> str:
    """The area a device's HA device is suggested into: its room's name."""
    room = int(row.get("room") or 0)
    rooms = (data or {}).get("rooms") or {}
    return str((rooms.get(room) or {}).get("name") or "") if room > 0 and not int(row.get("shared") or 0) else ""


def _shown_units(data: dict) -> list[str]:
    """The units Home Assistant offers: at least one activity other than Off
    (Michel 2026-10-25, N2), and not hidden on the Control."""
    return [
        key for key, unit in data["units"].items()
        if data["offered"].get(key) and not unit["hidden"]
    ]


async def _register_frontend(hass: HomeAssistant) -> None:
    """Serve the unit tile, player card, voice card and dashboard strategy from
    the integration itself - the Hub integration's _register_frontend, with
    ArtiCONTROL's own paths (const.py, plan 3.2).

    ⚠ ONCE per Home Assistant, not once per config entry: a second Control
    would otherwise register the same static path twice and raise.
    ⚠ The URL carries the integration version, or an upgrade stays invisible
    behind the browser's cached copy.
    ⚠ add_extra_js_url rather than a Lovelace resource: a dashboard STRATEGY
    has to be defined before the dashboard renders.
    """
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    hass.data[f"{DOMAIN}_frontend"] = True

    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version or "0"
    # ⚠ TWO static paths: the frontend directory ships in the package; the icon
    # cache is written at RUNTIME into the CONFIG directory (see icons.py).
    icon_dir = Path(hass.config.path(ICON_CACHE_DIR))
    await hass.async_add_executor_job(lambda: icon_dir.mkdir(parents=True, exist_ok=True))
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(FRONTEND_BASE, str(Path(__file__).parent / FRONTEND_DIR), True),
            StaticPathConfig(ICON_BASE, str(icon_dir), True),
        ]
    )
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}?v={version}")
    _LOGGER.debug("ArtiCONTROL frontend module registered at %s?v=%s", FRONTEND_URL, version)


def icon_names(data: dict) -> set[str]:
    """Every icon the dashboard can draw for this Control: the rooms' and
    zones' own, every activity's, and the glyphs of the player card's keys."""
    names: set[str] = set(CONTROL_GLYPHS)
    for scope in ("rooms", "zones"):
        for item in (data.get(scope) or {}).values():
            if item.get("icon"):
                names.add(str(item["icon"]))
    for rows in (data.get("activities") or {}).values():
        for row in rows or []:
            if isinstance(row, dict) and row.get("icon"):
                names.add(str(row["icon"]))
    return names


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        await _register_frontend(hass)
    except Exception as err:  # noqa: BLE001 - presentation must never block control
        _LOGGER.warning("ArtiCONTROL frontend module not registered: %s", err)

    ident = str(entry.unique_id or "")
    if not qualified(ident):
        # Every id is built from the stored ident; without one there is
        # nothing safe to build. The config flow never creates such an entry.
        _LOGGER.error("ArtiCONTROL entry %s has no Control identity; remove and add it again", entry.title)
        return False

    # Every setup is a new generation: a response still in flight for the
    # previous host or token is dropped when it lands (identity.Fence).
    fence = hass.data.setdefault(f"{DOMAIN}_fence", {}).setdefault(entry.entry_id, identity.Fence())
    generation = fence.bump()
    session = async_get_clientsession(hass)
    client = ArtiControlClient(session, entry.data[CONF_HOST], entry.data[CONF_TOKEN], generation)

    await _async_start_follow(hass)

    # Every setup and reload checks that the saved host still answers OUR
    # ident. Another Control there is never adopted: nothing is created or
    # refreshed, a repair issue says so, and address follow looks for ours.
    try:
        alive = await client.alive()
    except ArtiControlError as err:
        raise ConfigEntryNotReady(str(err)) from None
    if ident_verdict(ident, alive.get("ident")) != "same":
        _raise_issue(hass, entry, "wrong_control", host=entry.data[CONF_HOST])
        raise ConfigEntryNotReady(f"A different Control answers at {entry.data[CONF_HOST]}")
    ir.async_delete_issue(hass, DOMAIN, f"wrong_control_{entry.entry_id}")

    # Survives a reload on purpose - see ArtiControlCoordinator.__init__. Kept
    # at the top level of hass.data, not under hass.data[DOMAIN], because the
    # services iterate that dict's values expecting entry data.
    memory = hass.data.setdefault(f"{DOMAIN}_memory", {}).setdefault(entry.entry_id, {})
    # Dead-cover verdicts outlive a reload: a cover is checked once a week, not
    # again on every look after every reload.
    client.art_verdicts = memory.setdefault("art_verdicts", {})
    name = str(alive.get("name") or entry.title or "ArtiCONTROL")
    coordinator = ArtiControlCoordinator(hass, client, name, ident, memory, fence)
    await coordinator.async_config_entry_first_refresh()
    if not coordinator.has_inventory:
        # ⚠ Which units to create is decided from the activities. A Control
        # whose activities could not be read is not an empty Control - wait.
        raise ConfigEntryNotReady("The Control's activities could not be read yet")

    # S3c: Repairs for the devices the Control says are down - synced after
    # every poll, from the last /devices read (the inventory cycle's).
    @callback
    def _sync_health() -> None:
        data = coordinator.data or {}
        memory["device_issues"] = health.sync_issues(
            hass, entry.entry_id, name, client.host, coordinator.devices, data.get("names", {}),
            {rid: row.get("name") for rid, row in (data.get("rooms") or {}).items()},
            set(memory.get("device_issues") or ()),
        )

    entry.async_on_unload(coordinator.async_add_listener(_sync_health))
    _sync_health()

    # Voice choices: opt-in, never generated (see scene.py).
    voice_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_voice")
    voice: dict = await voice_store.async_load() or {}

    shown = _shown_units(coordinator.data)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry_data = {
        "client": client,
        "coordinator": coordinator,
        "entry_id": entry.entry_id,
        "ident": ident,
        "control_name": name,
        "shown_units": shown,
        "voice_activities": list(voice.get("activities") or []),
        # Voice favourites (favorites.py): {"kind","id","activity","play",
        # "name","auto"}; excluded = "<unit>|<play key>" the user removed.
        "voice_favorites": list(voice.get("favorites") or []),
        "voice_excluded": set(voice.get("excluded") or []),
        "voice_store": voice_store,
        # This setup's generation (identity.Fence). A command still waiting
        # for its unit when the entry unloads or reloads is not sent through
        # this old client (media_player.run_command).
        "fence": fence,
        "retired": False,
        # One ordering lock per unit, shared by its media_player and its voice
        # activities. See media_player.run_command.
        "locks": {},
    }
    # S3c: one remote per device of an offered unit (remotes.py). None when
    # /devices has not been read: then no remote is created or removed.
    entry_data["remote_devices"] = _remote_devices(coordinator, shown)

    # Register the Control and every shown unit device up front, fully named,
    # so entity_id generation never races platform setup order.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, ident)},
        manufacturer="ArtiLED B.V.",
        model="ArtiLED Control",
        name=name,
        sw_version=coordinator.version or None,
    )
    for key in shown:
        unit = coordinator.data["units"][key]
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, unit_unique_id(ident, unit["kind"], unit["id"]))},
            manufacturer="ArtiLED B.V.",
            model=f"ArtiCONTROL {unit['kind'].title()}",
            name=coordinator.data["names"][key],
            via_device=(DOMAIN, ident),
            # A zone into its room's area, a room into its own: area-aware
            # voice then works by itself ("pause the living room"). The
            # entity_id is pinned below, so the area never leaks into it.
            suggested_area=(unit["caption"] or None) if unit["kind"] == "zone" else unit["name"] or None,
        )

    for row in entry_data["remote_devices"] or []:
        place = _device_place(coordinator.data, row)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, remotes.remote_unique_id(ident, row["id"]))},
            manufacturer=str(row.get("brand") or "") or None,
            model=str(row.get("type_name") or "") or None,
            name=str(row.get("name") or f"Device {row['id']}"),
            via_device=(DOMAIN, ident),
            suggested_area=place or None,
        )

    # Voice favourites are house-wide since 0.3.13 (Michel 2026-10-26).
    await favorites.migrate(hass, entry, entry_data)
    await favorites.merge_records(hass, entry, entry_data)
    await _prune_remotes(hass, entry, entry_data)
    await _prune_deleted(hass, entry, entry_data)

    # Font Awesome paths for the icons THIS Control uses, read from its own web
    # root (icons.py). ⚠ Not awaited: an icon is cosmetic, and a slow box must
    # not delay setup; the iconset falls back to the ArtiCONTROL mark meanwhile.
    icons = ControlIcons(hass, session, f"http://{client.host}")
    entry.async_create_background_task(hass, icons.async_refresh(icon_names(coordinator.data)), f"{DOMAIN}_icons")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.cancel_pending)

    # 0.3.24: the house's favourites read once in the background at setup (and
    # after the reload a voice favourite makes), so the Favourites list draws
    # from kept answers the first time it is opened. A read; never awaited.
    async def _warm_hearts() -> None:
        try:
            await favorites.house_hearts(entry_data)
        except Exception as err:  # noqa: BLE001 - a warm-up only
            _LOGGER.debug("Favourites warm-up: %s", err)

    entry.async_create_background_task(hass, _warm_hearts(), f"{DOMAIN}_warm_hearts")

    # Follow the Control's inventory. A room the customer gives its first
    # activity appears on its own; one that loses its last goes unavailable at
    # once and is removed after the grace hour.
    entry_data["inventory"] = coordinator.inventory
    entry_data["deletable"] = coordinator.deletable

    @callback
    def _inventory_changed() -> None:
        current = coordinator.inventory
        if current is None:
            return
        doomed = coordinator.deletable
        # `deletable` is watched as well as `inventory` because it is what grows
        # when an item's grace finally runs out - and pruning only runs during
        # setup. Without this nothing would ever tidy up.
        if current == entry_data.get("inventory") and doomed == entry_data.get("deletable"):
            return
        entry_data["inventory"] = current
        entry_data["deletable"] = doomed
        # Reload rather than adding entities here: setup also registers devices
        # and prunes. Rebuilding that by hand is where the subtle bugs live.
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    @callback
    def _devices_changed() -> None:
        # A device of an offered unit added or gone on the Control: reload,
        # as for the inventory. Only from a /devices read that ANSWERED.
        now = _remote_devices(coordinator, _shown_units(coordinator.data))
        if now is None or entry_data["remote_devices"] is None:
            if now is not None and entry_data["remote_devices"] is None:
                entry_data["remote_devices"] = now
                hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
            return
        if {int(r["id"]) for r in now} != {int(r["id"]) for r in entry_data["remote_devices"]}:
            entry_data["remote_devices"] = now
            hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    entry.async_on_unload(coordinator.async_add_listener(_devices_changed))

    # "Make all favourites available to voice": one sync per inventory round.
    entry_data["favorites_round"] = coordinator.inventory_round

    @callback
    def _favorites_round() -> None:
        if coordinator.inventory_round == entry_data["favorites_round"]:
            return
        entry_data["favorites_round"] = coordinator.inventory_round
        if not entry.data.get(favorites.CONF_ALL_FAVORITES):
            return
        task = entry_data.get("favorites_task")
        if task is None or task.done():
            entry_data["favorites_task"] = hass.async_create_task(favorites.sync_auto(hass, entry_data))

    entry.async_on_unload(coordinator.async_add_listener(_favorites_round))

    @callback
    def _identity_changed() -> None:
        if coordinator.wrong_control:
            _raise_issue(hass, entry, "wrong_control", host=entry.data[CONF_HOST])
        else:
            ir.async_delete_issue(hass, DOMAIN, f"wrong_control_{entry.entry_id}")

    entry.async_on_unload(coordinator.async_add_listener(_inventory_changed))
    entry.async_on_unload(coordinator.async_add_listener(_identity_changed))
    await _pin_unit_ids(hass, entry, entry_data)
    # The dashboard, created once (dashboard.py). Wrapped inside as well:
    # presentation must never block control.
    try:
        await async_create_dashboard(hass, entry)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("ArtiCONTROL dashboard step failed: %s", err)
    await _voice_defaults_once(hass, entry)
    await _alias_backfill_once(hass, entry)
    await _assist_all_once(hass, entry)
    _register_services(hass)
    return True


def _raise_issue(hass: HomeAssistant, entry: ConfigEntry, kind: str, **placeholders) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{kind}_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=kind,
        translation_placeholders={"name": entry.title, **placeholders},
    )


# ---- entity ids, voice defaults ---------------------------------------------


async def _pin_unit_ids(hass: HomeAssistant, entry: ConfigEntry, entry_data: dict) -> None:
    """Give each unit media_player the id media_player.<unit name>, and the
    Control's own entities <domain>.<control name>_<what>, once.

    ⚠ Home Assistant composes an entity_id from the device's AREA as well as its
    name, so a unit device in an area of the same name - which suggested_area
    sets up - would become media_player.living_room_living_room. The Hub's
    pickers met exactly that. Renamed explicitly, as the UI's own rename does,
    which keeps the entity's history.

    ⚠ Each one EXACTLY ONCE, on its first appearance, and never onto an id
    something else holds (entity_ids.pin_plan). A user's rename afterwards
    stands.
    """
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_flags")
    flags: dict = await store.async_load() or {}
    already: set[str] = set(flags.get("units_pinned") or [])
    registry = er.async_get(hass)
    names = entry_data["coordinator"].data["names"]
    ident = entry_data["ident"]

    control = slugify(str(entry_data.get("control_name") or ""))
    # The Control's own entities (S3c, measured on the master 2026-10-26:
    # button.workshop_control_all_off - its device's area in front).
    own = {f"{ident}_all_off": f"button.{control}_all_off",
           f"{ident}_version": f"sensor.{control}_control_version"} if control else {}

    for row in entry_data.get("remote_devices") or []:
        object_id = slugify(str(row.get("name") or ""))
        if object_id:
            own[remotes.remote_unique_id(ident, row["id"])] = f"remote.{object_id}"

    def wanted_for(unique_id: str) -> str | None:
        if unique_id in own:
            return own[unique_id]
        parsed = parse_unit_uid(unique_id, ident)
        if parsed is None:
            return None
        name = names.get(unit_key(*parsed))
        return f"media_player.{slugify(name)}" if name else None

    def taken(entity_id: str) -> bool:
        # The state machine too: not every entity is in the registry.
        return registry.async_get(entity_id) is not None or hass.states.get(entity_id) is not None

    try:
        entries = [
            (e.unique_id, e.entity_id)
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.entity_id.startswith(("media_player.", "button.", "sensor.", "remote."))
        ]
        renames, pinned = pin_plan(entries, wanted_for, already, taken)
        for entity_id, new_entity_id in renames.items():
            registry.async_update_entity(entity_id, new_entity_id=new_entity_id)
    except Exception as err:  # noqa: BLE001 - cosmetic; must never block setup
        _LOGGER.warning("Could not pin the unit entity ids: %s", err)
        return
    if pinned:
        flags["units_pinned"] = sorted(already | pinned)
        await store.async_save(flags)


async def _voice_defaults_once(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Expose NOTHING we create to voice until someone asks for it.

    The Hub rule (Michel 2026-08-17, and again for Control 2026-10-25, Q1).
    ⚠ It cannot be left to Home Assistant: its default exposes every new
    media_player to Assist (DEFAULT_EXPOSED_ASSISTANT = {"conversation": True}).

    ⚠ ONCE PER ENTITY, recorded by unique_id - never on every setup. This writes
    an explicit setting, so re-applying it would overrule a customer who
    exposed a room on purpose. Per entity, so a unit added next month gets the
    same default.

    ⚠ A VOICE ACTIVITY IS THE EXCEPTION (Michel 2026-10-26). Adding one IS
    someone asking for voice, so its scene is ALWAYS exposed to Assist and to
    every cloud assistant switched on in Home Assistant (_cloud_assistants) -
    whatever its room's own exposure (this replaced "copy the room's
    exposure"). Once, like every default here: a later change by the user
    stands, and voice activities made before keep theirs.
    """
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_flags")
    flags: dict = await store.async_load() or {}
    already: set[str] = set(flags.get("voice_defaults") or [])
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    except ImportError:  # pragma: no cover - the helper moved or went away
        _LOGGER.debug("Voice-exposure helper unavailable; leaving defaults alone")
        return
    registry = er.async_get(hass)
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    ident = str(data.get("ident") or entry.unique_id or "")
    voice_scenes = {
        voice_unique_id(ident, c["kind"], c["id"], c["activity"])
        for c in data.get("voice_activities") or []
    } | {favorites.fav_uid(data, f) for f in data.get("voice_favorites") or []}
    heard_by = {"conversation"} | _cloud_assistants(hass)
    done: set[str] = set()
    try:
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.unique_id in already:
                continue
            done.add(entity.unique_id)
            voice = entity.unique_id in voice_scenes
            for assistant in ASSISTANTS:
                async_expose_entity(hass, assistant, entity.entity_id, voice and assistant in heard_by)
    except Exception as err:  # noqa: BLE001 - a default must never block setup
        _LOGGER.warning("Could not apply the voice defaults: %s", err)
        return
    if done:
        flags["voice_defaults"] = sorted(already | done)
        await store.async_save(flags)


def _cloud_assistants(hass: HomeAssistant) -> set[str]:
    """The cloud assistants switched on in Home Assistant: Home Assistant
    Cloud signed in, and its Alexa / Google setting on (cloud prefs
    alexa_enabled / google_enabled, 2026.10.4). Nothing without the cloud."""
    out: set[str] = set()
    try:
        cloud = hass.data.get("cloud")
        if cloud is None or not cloud.is_logged_in:
            return out
        prefs = cloud.client.prefs
        if prefs.alexa_enabled:
            out.add("cloud.alexa")
        if prefs.google_enabled:
            out.add("cloud.google_assistant")
    except Exception as err:  # noqa: BLE001 - a default must never block setup
        _LOGGER.debug("Could not read the cloud assistants: %s", err)
    return out


@callback
def async_expose_all_units(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """"Let Assist control all rooms": every offered unit media_player to Assist.

    Assist only - Alexa and Google stay per entity (plan 2.3). Returns how many.
    """
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    except ImportError:  # pragma: no cover
        return 0
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return 0
    registry = er.async_get(hass)
    wanted = {unit_unique_id(data["ident"], *parse_unit_key(key)) for key in data["shown_units"]}
    count = 0
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id in wanted:
            async_expose_entity(hass, "conversation", entity.entity_id, True)
            count += 1
    return count


async def _assist_all_once(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The setup tick, applied ONCE - after the voice defaults, which it
    overrides for Assist. Never again on a reload: a room someone un-exposes
    afterwards stays un-exposed."""
    if not entry.data.get(CONF_ASSIST_ALL):
        return
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_flags")
    flags: dict = await store.async_load() or {}
    if flags.get("assist_all_applied"):
        return
    try:
        async_expose_all_units(hass, entry)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not let Assist control all rooms: %s", err)
        return
    flags["assist_all_applied"] = True
    await store.async_save(flags)


# ---- removal ------------------------------------------------------------------


async def _forget_voice_entities(hass: HomeAssistant, entry_id: str, unique_ids: set[str]) -> None:
    """Leave nothing behind of removed voice scenes (Michel 2026-10-26).

    ⚠ Home Assistant keeps a removed registry entry as a DeletedRegistryEntry
    and RESTORES its entity_id, name, aliases and exposure options when the
    same unique_id is created again (core 2026.10.4 entity_registry.py
    async_get_or_create). A voice activity removed and added again must come
    back clean - as its spoken name - so those entries are dropped. And the
    once-only voice default is forgotten for it, so a re-add is treated as new
    and is exposed again as a new voice activity is.
    """
    if not unique_ids:
        return
    registry = er.async_get(hass)
    deleted = getattr(registry, "deleted_entities", None)
    if isinstance(deleted, dict):
        doomed = [key for key in deleted if len(key) == 3 and key[1] == DOMAIN and key[2] in unique_ids]
        for key in doomed:
            deleted.pop(key, None)
        if doomed and hasattr(registry, "async_schedule_save"):
            registry.async_schedule_save()
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}_flags")
    flags: dict = await store.async_load() or {}
    seen = list(flags.get("voice_defaults") or [])
    if any(uid in unique_ids for uid in seen):
        flags["voice_defaults"] = [uid for uid in seen if uid not in unique_ids]
        await store.async_save(flags)


def _unexpose(hass: HomeAssistant, entity_ids: list[str]) -> bool:
    """Tell every assistant these entities are going, BEFORE they go.

    ⚠ DELETING AN EXPOSED ENTITY STRANDS IT IN THE ASSISTANT FOREVER - the Hub's
    measured lesson: Alexa is never told, the tile stays "unresponsive", and
    nothing can remove it afterwards. Un-exposing first turns the removal into a
    change Home Assistant can report onward.

    Returns True if anything was actually un-exposed.
    """
    try:
        from homeassistant.components.homeassistant.exposed_entities import (
            async_expose_entity,
            async_should_expose,
        )
    except ImportError:  # pragma: no cover
        return False
    touched = False
    for entity_id in entity_ids:
        for assistant in ASSISTANTS:
            try:
                if not async_should_expose(hass, assistant, entity_id):
                    continue
                async_expose_entity(hass, assistant, entity_id, False)
                touched = True
            except Exception as err:  # noqa: BLE001 - never block a delete
                _LOGGER.debug("Could not un-expose %s: %s", entity_id, err)
    return touched


async def _prune_remotes(hass: HomeAssistant, entry: ConfigEntry, entry_data: dict) -> None:
    """Remove the remote (and its HA device) of a Control device that is no
    longer a device of an offered unit - decided only from a /devices read
    that ANSWERED (remote_devices None = not read: nothing removed).
    ⚠ Un-exposed first, as every removal."""
    rows = entry_data.get("remote_devices")
    if rows is None:
        return
    ident = entry_data["ident"]
    keep = {remotes.remote_unique_id(ident, r["id"]) for r in rows}
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    doomed = [
        d for d in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if any(domain == DOMAIN and i.startswith(f"{ident}_device_") and i not in keep for domain, i in d.identifiers)
    ]
    entities = [e.entity_id for d in doomed
                for e in er.async_entries_for_device(entity_registry, d.id, include_disabled_entities=True)]
    if _unexpose(hass, entities):
        await asyncio.sleep(0)
    for d in doomed:
        device_registry.async_remove_device(d.id)


async def _prune_deleted(hass: HomeAssistant, entry: ConfigEntry, entry_data: dict) -> None:
    """Remove units, and voice choices, that have been GONE for the grace hour.

    ⚠ Driven by `coordinator.present`, never by what the last poll returned:
    a unit that lost its last activity, or a voice choice whose activity was
    disabled, is kept through the hour and comes back complete if it returns.
    ⚠ EVERY removal is preceded by _unexpose().
    """
    coordinator = entry_data["coordinator"]
    present = coordinator.present
    if present is None:  # nothing deletable yet
        return
    ident = entry_data["ident"]
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    # Voice choices first, so their entities are named while they still exist.
    kept, gone = [], []
    for choice in entry_data["voice_activities"]:
        marker = ("activity", f"{choice['kind']}_{int(choice['id'])}:{int(choice['activity'])}")
        (kept if marker in present else gone).append(choice)
    if gone:
        doomed_ids = {voice_unique_id(ident, c["kind"], c["id"], c["activity"]) for c in gone}
        doomed = [
            e.entity_id for e in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
            if e.unique_id in doomed_ids
        ]
        if _unexpose(hass, doomed):
            await asyncio.sleep(0)
        for entity_id in doomed:
            entity_registry.async_remove(entity_id)
        await _forget_voice_entities(hass, entry.entry_id, doomed_ids)
        entry_data["voice_activities"] = kept
        await favorites.save(hass, entry_data, reload=False)
        _LOGGER.info("Removed %s voice activities gone from the Control for an hour", len(gone))

    keep = {ident} | {f"{ident}_{key}" for kind, key in present if kind == "unit"}
    # The remotes are _prune_remotes' to decide, never removed here.
    keep |= {i for d in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
             for domain, i in d.identifiers if domain == DOMAIN and i.startswith(f"{ident}_device_")}
    doomed_devices = [
        device
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if (ours := {i for domain, i in device.identifiers if domain == DOMAIN}) and not (ours & keep)
    ]
    # ⚠ Collect the entity ids BEFORE removing the device - async_remove_device
    # takes its entities with it, and by then there is nothing left to name.
    doomed_entities: list[str] = []
    for device in doomed_devices:
        doomed_entities.extend(
            e.entity_id
            for e in er.async_entries_for_device(entity_registry, device.id, include_disabled_entities=True)
        )
    if _unexpose(hass, doomed_entities):
        # ⚠ Yield before deleting: un-exposing schedules the cloud's own sync
        # task, and removing in the same tick gives it no chance to run.
        await asyncio.sleep(0)
    for device in doomed_devices:
        device_registry.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data:
        data["coordinator"].cancel_pending()
        # ⚠ Retire this generation (Mycroft 8662, HA-S2): a command queued
        # behind another for the same unit must not go out through the old
        # client once the entry that authorised it is gone or replaced.
        data["retired"] = True
        data["fence"].bump()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        # ⚠ hass.data[f"{DOMAIN}_memory"] is deliberately NOT dropped: an unload
        # is half of every reload, and forgetting when each item was last seen
        # is exactly what made the Hub's delete grace useless once.
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration is being removed for good.

    1. Un-expose every entity of the entry from every assistant FIRST, so no
       dead tile stays behind in the Alexa app.
    2. Delete the voice and flag stores, and the inventory memory.
    The dashboard is left in place on purpose (Michel 2026-10-25, Q4), as is
    the icon cache in the config directory (other Controls may still use it).
    """
    registry = er.async_get(hass)
    if _unexpose(hass, [e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)]):
        await asyncio.sleep(0)
    for suffix in ("voice", "flags"):
        try:
            await Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_{suffix}").async_remove()
        except Exception as err:  # noqa: BLE001 - tidy-up must not block removal
            _LOGGER.debug("Could not remove the %s store: %s", suffix, err)
    gone = hass.data.get(f"{DOMAIN}_memory", {}).pop(entry.entry_id, None) or {}
    health.clear_issues(hass, set(gone.get("device_issues") or ()))
    hass.data.get(f"{DOMAIN}_fence", {}).pop(entry.entry_id, None)
    for kind in ("wrong_control", "ambiguous", favorites.CAPPED_ISSUE):
        ir.async_delete_issue(hass, DOMAIN, f"{kind}_{entry.entry_id}")


# ---- address follow ---------------------------------------------------------


def _needs_follow(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Only an entry that is NOT reaching its Control looks for it elsewhere.
    A working entry is never moved by a packet."""
    if entry.state in (ConfigEntryState.SETUP_RETRY, ConfigEntryState.SETUP_ERROR):
        return True
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return False
    coordinator = data["coordinator"]
    return coordinator.wrong_control or not coordinator.last_update_success


async def _async_start_follow(hass: HomeAssistant) -> None:
    """Listen for Control announcements, once per Home Assistant."""
    if f"{DOMAIN}_follow" in hass.data:
        return
    state: dict = {"tracker": FollowTracker(FOLLOW_AMBIGUITY_SECONDS), "busy": set(), "transport": None}
    hass.data[f"{DOMAIN}_follow"] = state

    @callback
    def _heard(packet: dict, sender: str) -> None:
        now = time.monotonic()
        state["tracker"].heard(packet["ident"], sender, now)
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id != packet["ident"]:
                continue
            host = str(entry.data.get(CONF_HOST, ""))
            if not is_ip_literal(host) or sender == host or not _needs_follow(hass, entry):
                continue
            verdict, address = state["tracker"].verdict(packet["ident"], host, now)
            if verdict == "ambiguous":
                # ⚠ Never "the first Control found". Refuse, say so, change nothing.
                _raise_issue(hass, entry, "ambiguous", ident=packet["ident"])
                continue
            if verdict == "candidate" and entry.entry_id not in state["busy"]:
                state["busy"].add(entry.entry_id)
                hass.async_create_task(_follow(hass, entry, address, state))

    state["transport"] = await identity.async_listen(hass.loop, DISCOVERY_PORT, _heard)

    @callback
    def _stop(_event) -> None:
        if state["transport"] is not None:
            state["transport"].close()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)


async def _follow(hass: HomeAssistant, entry: ConfigEntry, address: str, state: dict) -> None:
    """Prove the candidate, then move the entry to it.

    ⚠ QUALIFICATION TAKES TIME, AND THE WORLD MOVES WHILE IT RUNS (Mycroft
    8662, HA-S1). A second address may claim the same ident, the user may
    reconfigure or sign in again, the old address may answer again, or the
    entry may be removed - all while /alive and /version are in flight. So what
    this task owned when it started (entry, generation, host, token) is
    captured, and checked again at the moment of PUBLICATION, together with the
    tracker's verdict as it stands then. Anything changed: nothing is written,
    no re-auth is opened; the next announcement (every 30 s) starts afresh.
    """
    session = async_get_clientsession(hass)
    entry_id = entry.entry_id
    owned = _follow_ownership(hass, entry)

    async def alive(host: str) -> dict:
        return await ArtiControlClient(session, host, "").alive()

    async def token_works(host: str) -> bool:
        try:
            await ArtiControlClient(session, host, owned["token"]).version()
        except ArtiControlAuthError:
            return False
        return True

    try:
        verdict = await verify_candidate(address, str(entry.unique_id), alive, token_works)
        if verdict not in ("follow", "reauth"):
            return
        if not _may_publish_follow(hass, entry_id, owned, address, state):
            _LOGGER.debug("%s: follow to %s dropped, the entry changed while it was proven", entry.title, address)
            return
        if verdict == "follow":
            _LOGGER.info("%s moved to %s; following it", entry.title, address)
            hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: address})
            ir.async_delete_issue(hass, DOMAIN, f"ambiguous_{entry_id}")
            await hass.config_entries.async_reload(entry_id)
        else:
            # Our Control, but the saved token is refused there: sign in again,
            # at the new address, same ident required.
            entry.async_start_reauth(hass, data={**entry.data, "follow_host": address})
    finally:
        state["busy"].discard(entry_id)


def _follow_ownership(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """What a follow task owns: the entry's generation, host and token now."""
    fence = hass.data.get(f"{DOMAIN}_fence", {}).get(entry.entry_id)
    return {
        "generation": fence.current if fence else None,
        "host": entry.data.get(CONF_HOST),
        "token": entry.data.get(CONF_TOKEN),
    }


def _may_publish_follow(hass: HomeAssistant, entry_id: str, owned: dict, address: str, state: dict) -> bool:
    """The publication boundary of a follow (HA-S1). True only when, NOW:
    the entry still exists, nothing reconfigured, re-authed or set it up again
    (same generation, host and token), it is still failing to reach its
    Control, and the tracker still sees exactly this one candidate."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return False
    if _follow_ownership(hass, entry) != owned:
        return False
    if not _needs_follow(hass, entry):
        return False
    verdict = state["tracker"].verdict(str(entry.unique_id), str(owned["host"]), time.monotonic())
    return verdict == ("candidate", address)


# ---- services -----------------------------------------------------------------

_CONTROL = vol.Optional("control")
START_SCHEMA = vol.Schema(
    {
        _CONTROL: str,
        vol.Optional("unit"): vol.Any(str, int),
        vol.Optional("entity_id"): vol.Any(str, [str]),
        vol.Required("activity"): vol.Any(str, int),
    }
)
ADD_VOICE_SCHEMA = vol.Schema(
    {
        _CONTROL: str,
        vol.Optional("unit"): vol.Any(str, int),
        vol.Optional("entity_id"): vol.Any(str, [str]),
        vol.Required("activity"): vol.Any(str, int),
        vol.Optional("name"): str,
    }
)
REMOVE_VOICE_SCHEMA = vol.Schema(
    {
        _CONTROL: str,
        vol.Optional("unit"): vol.Any(str, int),
        vol.Optional("entity_id"): vol.Any(str, [str]),
        vol.Optional("activity"): vol.Any(str, int),
        vol.Optional("name"): str,
    }
)


def _known(entries: dict) -> str:
    return ", ".join(sorted(str(d.get("control_name") or d.get("ident")) for d in entries.values()))


def entry_for_call(hass: HomeAssistant, call: ServiceCall) -> dict:
    """The Control a service call is for.

    ⚠ NEVER SILENTLY PICK ONE OF SEVERAL (the Hub's 2026-08-17 lesson). One
    Control: used. More than one and no `control`, or a `control` that matches
    nothing or more than one: refused, naming the choices.
    """
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise ServiceValidationError("ArtiCONTROL is not set up")
    entity_id = call.data.get("entity_id")
    if entity_id:
        entity_id = entity_id[0] if isinstance(entity_id, list) and len(entity_id) == 1 else entity_id
        if not isinstance(entity_id, str):
            raise ServiceValidationError("Give one entity_id")
        entity = er.async_get(hass).async_get(entity_id)
        if entity is None or entity.config_entry_id not in entries:
            raise ServiceValidationError(f"{entity_id} is not an ArtiCONTROL room")
        return entries[entity.config_entry_id]
    wanted = call.data.get("control")
    if wanted:
        key = str(wanted).strip().lower()
        matches = [
            d for d in entries.values()
            if key in (str(d.get("ident", "")).lower(), str(d.get("control_name", "")).lower())
        ]
        if len(matches) == 1:
            return matches[0]
        raise ServiceValidationError(
            f"{'More than one' if matches else 'No'} configured ArtiCONTROL matches '{wanted}'. Known: {_known(entries)}"
        )
    if len(entries) > 1:
        raise ServiceValidationError(
            "More than one ArtiCONTROL is configured, so this service needs a 'control' "
            f"to say which one. Known: {_known(entries)}"
        )
    return next(iter(entries.values()))


def unit_for_call(hass: HomeAssistant, data: dict, call: ServiceCall, shown_only: bool = True) -> str:
    """The unit key a call names: by entity_id, by key ("zone_3"), or by name.

    A name must match exactly one offered unit, ignoring case; ambiguous or
    unknown is refused - never the first match. `shown_only=False` (removing a
    voice choice) also accepts a unit that is no longer offered.
    """
    shown = data["shown_units"] if shown_only else list(data["coordinator"].data["units"])
    names = data["coordinator"].data["names"]
    entity_id = call.data.get("entity_id")
    if entity_id:
        entity_id = entity_id[0] if isinstance(entity_id, list) else entity_id
        entity = er.async_get(hass).async_get(entity_id)
        parsed = parse_unit_uid(entity.unique_id, data["ident"]) if entity else None
        if parsed is None:
            raise ServiceValidationError(f"{entity_id} is not an ArtiCONTROL room")
        key = unit_key(*parsed)
    else:
        wanted = call.data.get("unit")
        if wanted is None:
            raise ServiceValidationError("Give a unit (its name) or an entity_id")
        if parse_unit_key(str(wanted)) is not None:
            key = str(wanted)
        else:
            found = [k for k in shown if same_name(names.get(k), wanted)]
            if len(found) != 1:
                raise ServiceValidationError(
                    f"{'More than one room is' if found else 'No room is'} called '{wanted}'"
                    + (" - use the entity_id" if found else "")
                )
            key = found[0]
    if shown_only and key not in shown:
        raise ServiceValidationError(f"{names.get(key, key)} has no activity to start on the Control")
    return key


def activity_for_call(rows: list[dict], wanted, unit_name: str) -> dict:
    """An activity of this unit by id or by name (ignoring case, exactly one)."""
    text = str(wanted).strip()
    if text.isdigit():
        found = [r for r in rows if int(r["id"]) == int(text)]
    else:
        found = [r for r in rows if same_name(r.get("name"), text)]
    if len(found) != 1:
        raise ServiceValidationError(
            f"{'More than one activity is' if found else 'No activity is'} called '{wanted}' in {unit_name}"
            + (" - use its id" if found else "")
        )
    return found[0]


async def _retire_voice_entities(hass: HomeAssistant, data: dict, removed: list[dict]) -> None:
    """The registry entities of voice choices being removed - un-exposed FIRST,
    then deleted (Mycroft 8662, HA-S5).

    ⚠ Dropping the stored choice alone is not enough: the reload no longer
    knows it, so nothing would ever remove its registry entry, and an assistant
    that had it exposed keeps a dead target. Matched by exact unique_id within
    this entry, so nothing else of the unit is touched.
    """
    registry = er.async_get(hass)
    wanted = {voice_unique_id(data["ident"], c["kind"], c["id"], c["activity"]) for c in removed}
    doomed = [
        e.entity_id for e in er.async_entries_for_config_entry(registry, data["entry_id"])
        if e.unique_id in wanted
    ]
    if _unexpose(hass, doomed):
        await asyncio.sleep(0)
    for entity_id in doomed:
        registry.async_remove(entity_id)
    await _forget_voice_entities(hass, data["entry_id"], wanted)


def _name_in_use(hass: HomeAssistant, name: str, own_entity_id: str | None = None) -> bool:
    """Whether anything else in Home Assistant answers to this name: a state's
    friendly name or an alias in the entity registry, ignoring case."""
    for state in hass.states.async_all():
        if getattr(state, "entity_id", None) == own_entity_id:
            continue
        if same_name((getattr(state, "attributes", None) or {}).get("friendly_name"), name):
            return True
    for entity in list(er.async_get(hass).entities.values()):
        if entity.entity_id == own_entity_id:
            continue
        if any(isinstance(a, str) and same_name(a, name) for a in (getattr(entity, "aliases", None) or [])):
            return True
    return False


async def _alias_backfill_once(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Give voice activities made before 0.2.3 their long-form alias, ONCE.

    Michel 2026-10-26: the scenes already on the master ("Sonos in the
    Kitchen") must answer to "Listen to Sonos in the Kitchen" too, without
    being removed and added again. ⚠ Once per scene, recorded by unique_id in
    the flags store, and only when the scene has no alias of its own at all -
    a user's aliases, or a user who later removes ours, are never touched.
    """
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    choices = data.get("voice_activities") or []
    if not choices:
        return
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_flags")
    flags: dict = await store.async_load() or {}
    done: set[str] = set(flags.get("alias_backfill") or [])
    registry = er.async_get(hass)
    ident = data["ident"]
    coordinator_data = data["coordinator"].data or {}
    added: set[str] = set()
    for choice in choices:
        uid = voice_unique_id(ident, choice["kind"], choice["id"], choice["activity"])
        if uid in done:
            continue
        entity_id = registry.async_get_entity_id("scene", DOMAIN, uid)
        entry_row = registry.async_get(entity_id) if entity_id else None
        if entry_row is None:
            continue   # not created yet: tried again next setup
        added.add(uid)
        if any(isinstance(a, str) and a.strip() for a in (entry_row.aliases or [])):
            continue   # the user's own aliases stand
        key = unit_key(choice["kind"], choice["id"])
        row = next((r for r in (coordinator_data.get("activities") or {}).get(key) or []
                    if int(r.get("id", 0)) == int(choice["activity"])), None)
        unit_name = (coordinator_data.get("names") or {}).get(key)
        if row is None or not unit_name:
            continue
        _add_long_alias(hass, uid, choice["name"], long_spoken_name(row.get("name"), unit_name),
                        lambda n: False)
    if added:
        flags["alias_backfill"] = sorted(done | added)
        await store.async_save(flags)


@callback
def _add_long_alias(hass: HomeAssistant, unique_id: str, spoken: str, long_name: str, in_use) -> None:
    """Put the long form ("Listen to Sonos in the Kitchen") beside the spoken
    name as a Home Assistant alias (Michel 2026-10-26). Assist and Google use
    aliases; Alexa does not.

    ⚠ The user's own aliases stay, and so does HA's COMPUTED_NAME entry (HA
    2026.10: the entity's own name is a member of its aliases list; writing a
    list without it would take the spoken name itself away from Assist). Never
    an alias something else answers to. Cosmetic: never fails the service.
    """
    try:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("scene", DOMAIN, unique_id)
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is None:
            return
        current = entry.aliases
        alias = long_alias(spoken, long_name, list(current or []),
                           lambda n: in_use(n) or _name_in_use(hass, n, entity_id))
        if alias is None:
            return
        if isinstance(current, (set, frozenset)):   # before HA 2026.x: a set of strings
            registry.async_update_entity(entity_id, aliases=set(current) | {alias})
            return
        computed = getattr(er, "COMPUTED_NAME", None)
        aliases = list(current) if current else ([computed] if computed is not None else [])
        registry.async_update_entity(entity_id, aliases=[*aliases, alias])
    except Exception as err:  # noqa: BLE001 - an alias is a convenience
        _LOGGER.debug("Could not add the long name as an alias: %s", err)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "start_activity"):
        return

    async def start_activity(call: ServiceCall) -> None:
        """Start one activity of one room - for automations, by name or id.

        Start-only, like everything here. To turn a room off, turn its
        media_player off: that runs the room's Off activity.
        """
        data = entry_for_call(hass, call)
        key = unit_for_call(hass, data, call)
        name = data["coordinator"].data["names"][key]
        rows = data["coordinator"].data["activities"].get(key) or []
        activity = activity_for_call(rows, call.data["activity"], name)
        activity_id = int(activity["id"])

        def still_valid() -> str | None:
            # Checked again at dispatch: still an enabled activity of a unit
            # this entry still offers.
            current = data["coordinator"].data or {}
            if key not in _shown_units(current):
                return "the room is no longer offered on the Control"
            if not any(int(r["id"]) == activity_id for r in current.get("activities", {}).get(key) or []):
                return "that activity is no longer in this room on the Control"
            return None

        await run_command(data, key, name, lambda: data["client"].start_activity(activity_id), still_valid=still_valid)

    async def _save_voice(data: dict, chosen: list) -> None:
        """Persist the WHOLE list, then reload.

        ⚠ Write everything: saving a partial list would silently erase the rest.
        ⚠ Reload: the scene platform builds its entities at setup, so without
        it a new choice is not an entity until the next restart.
        """
        data["voice_activities"] = chosen
        await favorites.save(hass, data, reload=False)
        # ⚠ In place (Michel 2026-10-26): each added or renamed voice activity
        # is placed as ONE entity; removed ones are gone with their registry
        # entries (_retire_voice_entities) - never a reload of the whole entry.
        from .scene import ArtiControlVoiceActivity, place  # noqa: PLC0415

        for choice in chosen:
            await place(hass, data, ArtiControlVoiceActivity(data, choice))
        live = {voice_unique_id(data["ident"], c["kind"], c["id"], c["activity"]) for c in chosen}
        for uid in [u for u, e in (data.get("scenes") or {}).items() if "_voice_activity_" in u and u not in live]:
            data["scenes"].pop(uid, None)

    def _everything() -> list[dict]:
        return [
            {**choice, "control": d["ident"]}
            for d in hass.data.get(DOMAIN, {}).values()
            for choice in d.get("voice_activities") or []
        ]

    async def add_voice_activity(call: ServiceCall) -> None:
        """Give one unit + activity a spoken name, and an entity to answer to it."""
        data = entry_for_call(hass, call)
        key = unit_for_call(hass, data, call)
        unit_name = data["coordinator"].data["names"][key]
        # Only what the unit OFFERS: never its Off activity.
        activity = activity_for_call(data["coordinator"].data["offered"].get(key) or [], call.data["activity"], unit_name)
        kind, item_id = parse_unit_key(key)
        own_uid = voice_unique_id(data["ident"], kind, item_id, int(activity["id"]))
        own_entity = er.async_get(hass).async_get_entity_id("scene", DOMAIN, own_uid)

        def in_use(name: str) -> bool:
            # Another voice choice (any Control), or anything else in Home
            # Assistant answering to that name - never this choice's own scene.
            return (name_taken(name, _everything(), data["ident"], kind, item_id, int(activity["id"])) is not None
                    or _name_in_use(hass, name, own_entity))

        # No name given: the short form, or the long one when that is taken
        # ("Listen to Zidoo" / "Watch Zidoo" in one room, Michel 2026-10-26).
        spoken = (call.data.get("name") or "").strip() or suggest_spoken_name(activity.get("name"), unit_name, in_use)
        clash = name_taken(spoken, _everything(), data["ident"], kind, item_id, int(activity["id"]))
        if clash is not None:
            raise ServiceValidationError(
                f"'{spoken}' already starts another activity - choose another name"
            )
        chosen = without_entry(data["voice_activities"], kind, item_id, int(activity["id"]))
        chosen.append({"kind": kind, "id": item_id, "activity": int(activity["id"]), "name": spoken})
        await _save_voice(data, chosen)
        _add_long_alias(hass, own_uid, spoken, long_spoken_name(activity.get("name"), unit_name), in_use)

    async def remove_voice_activity(call: ServiceCall) -> None:
        data = entry_for_call(hass, call)
        if call.data.get("name") and "activity" not in call.data:
            chosen = [c for c in data["voice_activities"] if not same_name(c["name"], call.data["name"])]
        else:
            if "activity" not in call.data:
                raise ServiceValidationError("Give the spoken name, or a unit and its activity")
            key = (unit_for_call(hass, data, call, shown_only=False)
                   if (call.data.get("unit") or call.data.get("entity_id")) else None)
            if key is None:
                raise ServiceValidationError("Give a unit (its name) or an entity_id")
            kind, item_id = parse_unit_key(key)
            text = str(call.data["activity"]).strip()
            if text.isdigit():
                activity_id = int(text)
            else:
                rows = data["coordinator"].data["activities"].get(key) or []
                activity_id = int(activity_for_call(rows, text, data["coordinator"].data["names"][key])["id"])
            chosen = without_entry(data["voice_activities"], kind, item_id, activity_id)
        if len(chosen) == len(data["voice_activities"]):
            raise ServiceValidationError("No voice activity matches that")
        await _retire_voice_entities(hass, data, [c for c in data["voice_activities"] if c not in chosen])
        await _save_voice(data, chosen)

    hass.services.async_register(DOMAIN, "start_activity", start_activity, schema=START_SCHEMA)
    hass.services.async_register(DOMAIN, "add_voice_activity", add_voice_activity, schema=ADD_VOICE_SCHEMA)
    hass.services.async_register(DOMAIN, "remove_voice_activity", remove_voice_activity, schema=REMOVE_VOICE_SCHEMA)
    favorites.register(hass)
    find.register(hass)
