# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""An inert Home Assistant, and a fake Control, for the runtime tests.

No Home Assistant install, no network, no Control. The integration is loaded AS
SHIPPED - nothing here is a copy of its code - against stand-ins for the few
Home Assistant pieces it touches. Each stand-in does only what the integration
relies on, and records what was done to it, so a test can ask "what was sent?"
and "what was exposed?" instead of trusting a log line.

The fake Control answers the same JSON envelope as the real API
({"success", "error", "error_text", "data"}) and records every request. Every
address is TEST-NET (192.0.2.x); no request ever leaves the process.

Used by test_commands.py, test_display.py, test_identity.py, test_lifecycle.py,
test_offered.py and test_redaction.py.
"""
from __future__ import annotations

import asyncio
import atexit
import enum
import importlib
import importlib.util
import json
import pathlib
import re
import shutil
import sys
import tempfile
import types
from urllib.parse import parse_qs, urlparse

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "artiled_control"

TOKEN = "T0kenForTestsOnly-0123456789abcdef"
IDENT = "c0ffee000001"


def _module(name: str, **attrs) -> types.ModuleType:
    mod = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules.get(parent) or _module(parent), child, mod)
    return mod


# ---------------------------------------------------------------------------
# aiohttp and voluptuous - only when the real ones are absent
# ---------------------------------------------------------------------------

def _install_aiohttp() -> None:
    try:
        import aiohttp  # noqa: F401
        return
    except ImportError:
        pass

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    class ClientSession:
        pass

    _module("aiohttp", ClientError=ClientError, ClientTimeout=ClientTimeout, ClientSession=ClientSession)


def _install_voluptuous() -> None:
    try:
        import voluptuous  # noqa: F401
        return
    except ImportError:
        pass

    class Invalid(Exception):
        pass

    class Marker:
        def __init__(self, key, default=None):
            self.key = key
            self.default = default

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return getattr(other, "key", other) == self.key

    class Schema:
        def __init__(self, schema, *args, **kwargs):
            self.schema = schema

        def __call__(self, value):
            return value

        def extend(self, more):
            return Schema({**self.schema, **more})

    class In:
        def __init__(self, container, msg=None):
            self.container = container

        def __call__(self, value):
            if value not in self.container:
                raise Invalid(f"{value} not in {self.container}")
            return value

    def passthrough(*args, **kwargs):
        return lambda value: value

    _module("voluptuous", Invalid=Invalid, Schema=Schema, Required=Marker, Optional=Marker,
            Any=passthrough, All=passthrough, Coerce=passthrough, In=In, Range=passthrough)


# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------

class HomeAssistantError(Exception):
    pass


class ConfigEntryNotReady(HomeAssistantError):
    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    pass


class ServiceValidationError(HomeAssistantError):
    pass


class UpdateFailed(Exception):
    pass


def callback(func):
    return func


def slugify(text) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_") or "unknown"


class ConfigEntryState(enum.Enum):
    NOT_LOADED = "not_loaded"
    LOADED = "loaded"
    SETUP_RETRY = "setup_retry"
    SETUP_ERROR = "setup_error"


class ConfigEntry:
    def __init__(self, entry_id, unique_id, data, title="Control"):
        self.entry_id = entry_id
        self.unique_id = unique_id
        self.data = dict(data)
        self.title = title
        self.state = ConfigEntryState.NOT_LOADED
        self._on_unload = []
        self.reauth_started = []
        self.background: list[str] = []

    def async_on_unload(self, func):
        self._on_unload.append(func)

    def async_start_reauth(self, hass, context=None, data=None):
        self.reauth_started.append(data)

    def async_create_background_task(self, hass, target, name, eager_start=True):
        """Recorded, never run: the icon fetch reads the Control's web root,
        which the fake Control does not serve. test_dashboard.py drives
        icons.py directly."""
        self.background.append(name)
        target.close()


class ServiceCall:
    def __init__(self, data):
        self.data = data


class Store:
    """A Store whose files live in hass.storage - inspectable, and dropped by
    async_remove like the real thing."""

    def __init__(self, hass, version, key):
        self.hass = hass
        self.key = key

    async def async_load(self):
        value = self.hass.storage.get(self.key)
        return json.loads(json.dumps(value)) if value is not None else None

    async def async_save(self, data):
        self.hass.storage[self.key] = json.loads(json.dumps(data))

    async def async_remove(self):
        self.hass.storage.pop(self.key, None)


class _ComputedName:
    """er.COMPUTED_NAME (HA 2026.10): the entity's own computed name inside its
    aliases list - null over the websocket."""

    def __repr__(self):
        return "COMPUTED_NAME"


COMPUTED_NAME = _ComputedName()


class RegistryEntry:
    def __init__(self, entity_id, unique_id, platform, config_entry_id, device_id=None):
        self.aliases = [COMPUTED_NAME]   # what HA 2026.10 gives a new entry
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.platform = platform
        self.config_entry_id = config_entry_id
        self.device_id = device_id
        self.hidden_by = None


class EntityRegistry:
    def __init__(self):
        self.entities: dict[str, RegistryEntry] = {}
        self.removed: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        # Home Assistant keeps a removed entry as a DeletedRegistryEntry keyed by
        # (domain, platform, unique_id), and async_get_or_create RESTORES its
        # entity_id (and options, aliases, name) when the same unique_id comes
        # back (core 2026.10.4 helpers/entity_registry.py async_get_or_create).
        self.deleted_entities: dict[tuple[str, str, str], types.SimpleNamespace] = {}
        self.saves = 0

    def async_schedule_save(self):
        self.saves += 1

    def async_get(self, entity_id):
        return self.entities.get(entity_id)

    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self.entities.values():
            if e.unique_id == unique_id and e.platform == platform and e.entity_id.startswith(domain + "."):
                return e.entity_id
        return None

    def register(self, domain, platform, unique_id, config_entry_id, suggested, device_id=None):
        existing = self.async_get_entity_id(domain, platform, unique_id)
        if existing:
            # As HA: an existing entry follows the entity's device (None detaches).
            self.entities[existing].device_id = device_id
            return self.entities[existing]
        base = f"{domain}.{slugify(suggested)}"
        entity_id, n = base, 2
        while entity_id in self.entities:
            entity_id = f"{base}_{n}"
            n += 1
        deleted = self.deleted_entities.pop((domain, platform, unique_id), None)
        if deleted is not None and deleted.entity_id not in self.entities:
            entity_id = deleted.entity_id
        entry = RegistryEntry(entity_id, unique_id, platform, config_entry_id, device_id)
        self.entities[entity_id] = entry
        return entry

    def async_update_entity(self, entity_id, new_entity_id=None, hidden_by=None, aliases=None, new_unique_id=None):
        if aliases is not None:
            self.entities[entity_id].aliases = list(aliases)
        if new_unique_id is not None:
            # As Home Assistant: the same entry (id, aliases, exposure) under a new unique_id.
            self.entities[entity_id].unique_id = new_unique_id
        entry = self.entities.pop(entity_id)
        if new_entity_id:
            self.renamed.append((entity_id, new_entity_id))
            entry.entity_id = new_entity_id
            # As Home Assistant: the live entity follows, its state moves.
            hass = _HASS_FOR_REGISTRY[0]
            if hass is not None:
                for live in hass.entities:
                    if live.entity_id == entity_id:
                        live.entity_id = new_entity_id
                        old = hass.states.get(entity_id)
                        hass.states._states.pop(entity_id, None)
                        if old is not None:
                            hass.states.set(new_entity_id, old)
        self.entities[entry.entity_id] = entry

    def async_remove(self, entity_id):
        entry = self.entities.pop(entity_id, None)
        if entry is not None:
            key = (entity_id.split(".", 1)[0], entry.platform, entry.unique_id)
            self.deleted_entities[key] = types.SimpleNamespace(entity_id=entity_id, unique_id=entry.unique_id)
        self.removed.append(entity_id)
        if _HASS_FOR_REGISTRY[0] is not None:
            _HASS_FOR_REGISTRY[0].log.append(("remove", entity_id))
            # As Home Assistant: a removed registry entry takes its entity and
            # its state with it (the platform listens for the removal).
            _HASS_FOR_REGISTRY[0].states._states.pop(entity_id, None)
            live = [e for e in _HASS_FOR_REGISTRY[0].entities if e.entity_id == entity_id]
            for e in live:
                _HASS_FOR_REGISTRY[0].entities.remove(e)
                for remove in getattr(e, "_remove_listeners", []):
                    remove()


class Device:
    def __init__(self, device_id, identifiers, config_entry_id, kwargs):
        self.id = device_id
        self.identifiers = set(identifiers)
        self.config_entry_id = config_entry_id
        self.kwargs = kwargs


class DeviceRegistry:
    def __init__(self):
        self.devices: dict[str, Device] = {}
        self.removed: list[set] = []

    def async_get_or_create(self, config_entry_id, identifiers, **kwargs):
        for device in self.devices.values():
            if device.identifiers & set(identifiers):
                device.kwargs.update(kwargs)
                return device
        device = Device(f"dev{len(self.devices) + len(self.removed) + 1}", identifiers, config_entry_id, kwargs)
        self.devices[device.id] = device
        return device

    def async_get(self, device_id):
        return self.devices.get(device_id)

    def async_get_device(self, identifiers):
        for device in self.devices.values():
            if device.identifiers & set(identifiers):
                return device
        return None

    def async_remove_device(self, device_id):
        device = self.devices.pop(device_id, None)
        if device:
            self.removed.append(device.identifiers)
            reg = _HASS_FOR_REGISTRY[0].entity_registry
            for entity_id in [e.entity_id for e in reg.entities.values() if e.device_id == device_id]:
                reg.async_remove(entity_id)


_HASS_FOR_REGISTRY: list = [None]


class FakeStates:
    def __init__(self):
        self._states = {}

    def get(self, entity_id):
        return self._states.get(entity_id)

    def set(self, entity_id, state):
        if hasattr(state, "entity_id") is False:
            try:
                state.entity_id = entity_id
            except AttributeError:
                pass
        self._states[entity_id] = state

    def async_all(self):
        return list(self._states.values())


class FakeServices:
    def __init__(self):
        self.handlers = {}

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, schema=None):
        self.handlers[(domain, service)] = handler

    async def call(self, domain, service, data):
        return await self.handlers[(domain, service)](ServiceCall(data))


class FakeBus:
    def __init__(self):
        self.once = []
        # Every event fired, as (event_type, data); and the listeners.
        self.fired: list[tuple[str, dict]] = []
        self.listeners: list[tuple[str, object]] = []

    def async_listen_once(self, event, func):
        self.once.append((event, func))

    def async_listen(self, event, func):
        pair = (event, func)
        self.listeners.append(pair)

        def remove():
            if pair in self.listeners:
                self.listeners.remove(pair)
        return remove

    def async_fire(self, event_type, event_data=None, context=None):
        data = dict(event_data or {})
        self.fired.append((event_type, data))
        for name, func in list(self.listeners):
            if name == event_type:
                func(types.SimpleNamespace(event_type=event_type, data=data, context=context))


# homeassistant/components/homeassistant/triggers/event.py, as a device
# trigger uses it: event_type + event_data (a subset match), the action called
# with {"trigger": {..., "event": event}}.
async def _event_attach(hass, config, action, trigger_info, *, platform_type="event"):
    wanted = dict(config.get("event_data") or {})

    def heard(event):
        if all(event.data.get(k) == v for k, v in wanted.items()):
            action({**trigger_info.get("variables", {}),
                    "trigger": {**trigger_info.get("trigger_data", {}), "platform": platform_type,
                                "event": {"event_type": event.event_type, "data": event.data}}})
    return hass.bus.async_listen(config["event_type"], heard)


class FakeConnection:
    """A websocket connection: what a command handler answered, by message id."""

    def __init__(self):
        self.results: dict = {}
        self.errors: dict = {}

    def send_result(self, msg_id, result=None):
        self.results[msg_id] = result

    def send_error(self, msg_id, code, message):
        self.errors[msg_id] = (code, message)


def _ws_command(schema):
    def wrap(func):
        func._ws_type = schema.get("type") if isinstance(schema, dict) else None
        if func._ws_type is None:
            for key, value in schema.items():
                if getattr(key, "key", key) == "type":
                    func._ws_type = value
        return func
    return wrap


def _ws_register(hass, handler):
    hass.ws[handler._ws_type] = handler


class FakeConfigEntries:
    def __init__(self, hass):
        self.hass = hass
        self.entries: dict[str, ConfigEntry] = {}
        self.reloads = 0
        self.updates = []

    def async_entries(self, domain=None):
        return list(self.entries.values())

    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)

    def async_update_entry(self, entry, data=None, **kwargs):
        if data is not None:
            entry.data = dict(data)
            self.updates.append(dict(data))

    async def async_forward_entry_setups(self, entry, platforms):
        for platform in platforms:
            module = importlib.import_module(f"artiled_control.{platform}")
            await module.async_setup_entry(self.hass, entry, self.hass.adder(entry, platform))

    async def async_unload_platforms(self, entry, platforms):
        for entity in [e for e in self.hass.entities if e._entry_id == entry.entry_id]:
            self.hass.entities.remove(entity)
            for remove in getattr(entity, "_remove_listeners", []):
                remove()
        return True

    async def setup(self, entry):
        """What Home Assistant does around async_setup_entry."""
        integration = sys.modules["artiled_control"]
        try:
            ok = await integration.async_setup_entry(self.hass, entry)
        except ConfigEntryNotReady:
            entry.state = ConfigEntryState.SETUP_RETRY
            raise
        except ConfigEntryAuthFailed:
            entry.state = ConfigEntryState.SETUP_ERROR
            raise
        entry.state = ConfigEntryState.LOADED if ok else ConfigEntryState.SETUP_ERROR
        return ok

    async def unload(self, entry):
        integration = sys.modules["artiled_control"]
        ok = await integration.async_unload_entry(self.hass, entry)
        for func in entry._on_unload:
            func()
        entry._on_unload = []
        entry.state = ConfigEntryState.NOT_LOADED
        return ok

    async def async_reload(self, entry_id):
        self.reloads += 1
        entry = self.entries[entry_id]
        if entry.state == ConfigEntryState.LOADED:
            await self.unload(entry)
        try:
            await self.setup(entry)
        except (ConfigEntryNotReady, ConfigEntryAuthFailed):
            pass

    def async_update_reload_and_abort(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


_CONFIG_DIR = tempfile.mkdtemp(prefix="articontrol-test-config-")
atexit.register(shutil.rmtree, _CONFIG_DIR, True)


class FakeHttp:
    def __init__(self):
        self.static: list = []

    async def async_register_static_paths(self, configs):
        for config in configs:
            if any(c.url_path == config.url_path for c in self.static):
                # Home Assistant raises on a second registration of a path.
                raise RuntimeError(f"static path {config.url_path} registered twice")
            self.static.append(config)


class StaticPathConfig:
    def __init__(self, url_path, path, cache_headers=True):
        self.url_path = url_path
        self.path = path
        self.cache_headers = cache_headers


def _add_extra_js_url(hass, url, es5=False):
    hass.extra_js.append(url)


async def _get_integration(hass, domain):
    manifest = json.loads((PKG_DIR / "manifest.json").read_text())
    return types.SimpleNamespace(version=manifest.get("version"), domain=domain)


class FakeHass:
    def __init__(self):
        self.data: dict = {}
        self.storage: dict = {}
        self.states = FakeStates()
        self.services = FakeServices()
        self.bus = FakeBus()
        self.entity_registry = EntityRegistry()
        self.device_registry = DeviceRegistry()
        self.issues: dict[str, dict] = {}
        # (assistant, entity_id) -> exposed. Absent = Home Assistant's default,
        # which for a media_player on Assist is EXPOSED (DEFAULT_EXPOSED_ASSISTANT).
        self.exposure: dict[tuple[str, str], bool] = {}
        self.expose_calls: list[tuple[str, str, bool]] = []
        # One ordered log of exposure changes and registry removals, so a test
        # can ask which came FIRST.
        self.log: list[tuple] = []
        self.entities: list = []
        self.config_entries = FakeConfigEntries(self)
        self.loop = asyncio.get_running_loop()
        self.tasks: list[asyncio.Task] = []
        self.session = None
        # The frontend registration (S2): static paths and extra JS, recorded.
        self.http = FakeHttp()
        self.extra_js: list[str] = []
        # websocket commands registered, by type
        self.ws: dict = {}
        # The config directory: a throwaway temp dir, never a real one.
        self.config = types.SimpleNamespace(path=lambda *parts: str(pathlib.Path(_CONFIG_DIR, *parts)))
        _HASS_FOR_REGISTRY[0] = self

    def async_create_task(self, coro, name=None):
        task = self.loop.create_task(coro)
        self.tasks.append(task)
        return task

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def config_path(self, *parts):  # pragma: no cover
        return "/nonexistent/" + "/".join(parts)

    def adder(self, entry, platform):
        def add(entities):
            for entity in list(entities):
                entity.hass = self
                entity._entry_id = entry.entry_id
                entity.platform_domain = platform
                device_id = None
                info = getattr(entity, "_attr_device_info", None)
                if info:
                    device = self.device_registry.async_get_device(info["identifiers"])
                    device_id = device.id if device else None
                    if device is None:
                        device = self.device_registry.async_get_or_create(entry.entry_id, info["identifiers"])
                        device_id = device.id
                device_name = device.kwargs.get("name") if info and device else None
                # The device's area: suggested at creation, or one the user
                # gave it later ("area").
                area_name = (device.kwargs.get("area") or device.kwargs.get("suggested_area")) if info and device else None
                own = getattr(entity, "_attr_name", None)
                if getattr(entity, "_attr_has_entity_name", False):
                    # ⚠ Home Assistant 2026.10 puts the device's AREA in front of
                    # a new entity's id (measured on the master 2026-10-26:
                    # button.workshop_control_all_off); the friendly name is
                    # "<device> <name>".
                    suggested = " ".join(p for p in (area_name, device_name, own) if p) or "unnamed"
                    friendly = " ".join(p for p in (device_name, own) if p)
                elif device_name:
                    # ⚠ Home Assistant 2026.10's LEGACY naming (has_entity_name
                    # False) on an entity WITH a device: the friendly name is
                    # "<device> <name>" and the entity_id "<area> <device> <name>"
                    # (helpers/entity_registry.py _async_get_full_entity_name,
                    # async_get_legacy_friendly_name, _async_generate_entity_id),
                    # the name's own device prefix stripped first. Measured on
                    # the master: "Kitchen Listen to Roon in Kitchen".
                    bare = own or ""
                    if bare.lower().startswith(device_name.lower() + " "):
                        bare = bare[len(device_name) + 1:]
                    suggested = " ".join(p for p in (area_name, device_name, bare) if p)
                    friendly = " ".join(p for p in (device_name, bare) if p)
                else:
                    suggested = own or "unnamed"
                    friendly = own
                entity._friendly = friendly
                reg = self.entity_registry.register(platform, "artiled_control", entity.unique_id,
                                                    entry.entry_id, suggested, device_id)
                entity.entity_id = reg.entity_id
                entity._remove_listeners = []
                if hasattr(entity, "coordinator"):
                    entity._remove_listeners.append(
                        entity.coordinator.async_add_listener(entity._handle_coordinator_update))
                self.entities.append(entity)
                entity.async_write_ha_state()
        return add

    def entity(self, entity_id):
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        return None

    async def settle(self):
        """Let scheduled work (a reload, a refresh) run to completion."""
        for _ in range(50):
            await asyncio.sleep(0)


class DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None, **kwargs):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self._listeners = {}
        self._next = 0

    def async_add_listener(self, update_callback, context=None):
        self._next += 1
        key = self._next
        self._listeners[key] = update_callback

        def remove():
            self._listeners.pop(key, None)
        return remove

    def async_update_listeners(self):
        for cb in list(self._listeners.values()):
            cb()

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
            self.last_exception = None
        except UpdateFailed as err:
            self.last_update_success = False
            self.last_exception = err
        except ConfigEntryAuthFailed as err:
            self.last_update_success = False
            self.last_exception = err
        self.async_update_listeners()

    async def async_request_refresh(self):
        await self.async_refresh()

    async def async_config_entry_first_refresh(self):
        await self.async_refresh()
        if isinstance(self.last_exception, ConfigEntryAuthFailed):
            raise self.last_exception
        if not self.last_update_success:
            raise ConfigEntryNotReady(str(self.last_exception))


class Entity:
    hass = None
    entity_id = None
    _attr_name = None
    _attr_has_entity_name = False
    _attr_unique_id = None
    _attr_device_info = None
    _attr_available = True
    _attr_extra_state_attributes = None

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name

    @property
    def available(self):
        return self._attr_available

    @property
    def extra_state_attributes(self):
        return self._attr_extra_state_attributes

    def async_write_ha_state(self):
        if self.hass is None:
            return
        self.writes = getattr(self, "writes", 0) + 1
        state = self.state if self.available else "unavailable"
        attrs = dict(self.extra_state_attributes or {}) if self.available else {}
        if getattr(self, "_friendly", None):
            attrs["friendly_name"] = self._friendly
        self.hass.states.set(self.entity_id, types.SimpleNamespace(state=state, attributes=attrs))

    @property
    def state(self):
        return None


class CoordinatorEntity(Entity):
    def __init__(self, coordinator, context=None):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success

    def _handle_coordinator_update(self):
        self.async_write_ha_state()


class MediaPlayerState(str, enum.Enum):
    OFF = "off"
    ON = "on"
    PLAYING = "playing"
    PAUSED = "paused"
    IDLE = "idle"


class MediaPlayerEntityFeature(enum.IntFlag):
    PAUSE = 1
    SEEK = 2
    VOLUME_SET = 4
    VOLUME_MUTE = 8
    PREVIOUS_TRACK = 16
    NEXT_TRACK = 32
    TURN_ON = 128
    TURN_OFF = 256
    PLAY_MEDIA = 512
    VOLUME_STEP = 1024
    SELECT_SOURCE = 2048
    STOP = 4096
    PLAY = 16384
    SHUFFLE_SET = 32768
    BROWSE_MEDIA = 131072
    REPEAT_SET = 262144
    SEARCH_MEDIA = 4194304


class RepeatMode(str, enum.Enum):
    ALL = "all"
    OFF = "off"
    ONE = "one"


class MediaType(str, enum.Enum):
    ALBUM = "album"
    MUSIC = "music"
    MOVIE = "movie"
    PLAYLIST = "playlist"


class MediaClass(str, enum.Enum):
    ALBUM = "album"
    ARTIST = "artist"
    CHANNEL = "channel"
    DIRECTORY = "directory"
    MOVIE = "movie"
    MUSIC = "music"
    PLAYLIST = "playlist"
    TRACK = "track"


class BrowseMedia:
    """homeassistant/components/media_player/browse_media.py (2026.10.4):
    the same keyword-only constructor and fields."""

    def __init__(self, *, media_class, media_content_id, media_content_type, title, can_play, can_expand,
                 children=None, children_media_class=None, thumbnail=None, not_shown=0, can_search=False,
                 search_media_classes=None):
        self.media_class = media_class
        self.media_content_id = media_content_id
        self.media_content_type = media_content_type
        self.title = title
        self.can_play = can_play
        self.can_expand = can_expand
        self.children = children
        self.children_media_class = children_media_class
        self.thumbnail = thumbnail
        self.not_shown = not_shown
        self.can_search = can_search
        self.search_media_classes = search_media_classes


class SearchMedia:
    """browse_media.py SearchMedia: a frozen, keyword-only dataclass."""

    def __init__(self, *, result, version=1):
        self.result = result
        self.version = version


class SearchMediaQuery:
    """browse_media.py SearchMediaQuery."""

    def __init__(self, *, search_query, media_content_type=None, media_content_id=None, media_filter_classes=None):
        self.search_query = search_query
        self.media_content_type = media_content_type
        self.media_content_id = media_content_id
        self.media_filter_classes = media_filter_classes


class MediaPlayerEntity(Entity):
    _attr_media_image_remotely_accessible = False
    # Home Assistant's own per-entity token for its image proxy (not the
    # Control's): what get_browse_image_url puts in the URL.
    access_token = "ha-entity-access-token"

    def get_browse_image_url(self, media_content_type, media_content_id, media_image_id=None):
        # As 2026.10.4's: Home Assistant's proxy path, the id quoted, its own token.
        from urllib.parse import quote, urlencode  # noqa: PLC0415
        query = {"token": self.access_token}
        if media_image_id:
            query["media_image_id"] = media_image_id
        return (f"/api/media_player_proxy/{self.entity_id}/browse_media/{media_content_type}/"
                f"{quote(media_content_id)}?{urlencode(query)}")


class Scene(Entity):
    @property
    def state(self):
        return getattr(self, "_last_activated", None)

    async def async_internal_activate(self, **kwargs):
        await self.async_activate(**kwargs)
        self._last_activated = "activated"
        self.async_write_ha_state()


class ButtonEntity(Entity):
    @property
    def state(self):
        return getattr(self, "_last_pressed", None)


class RemoteEntity(Entity):
    @property
    def state(self):
        on = getattr(self, "is_on", None)
        return None if on is None else ("on" if on else "off")


class SensorEntity(Entity):
    @property
    def state(self):
        return self.native_value


class EntityCategory(str, enum.Enum):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


class IssueSeverity(str, enum.Enum):
    WARNING = "warning"
    ERROR = "error"


def DeviceInfo(**kwargs):  # noqa: N802 - HA's is a TypedDict
    return dict(kwargs)


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def __init__(self):
        self.context = {}
        self.unique_id = None

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self, updates=None):
        for entry in self.hass.config_entries.async_entries():
            if entry.unique_id == self.unique_id:
                if updates:
                    entry.data = {**entry.data, **updates}
                raise _Abort("already_configured")

    def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {"type": "form", "step_id": step_id, "errors": errors or {},
                "description_placeholders": description_placeholders}

    def async_create_entry(self, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    def async_abort(self, reason):
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(self, entry, data=None, reason="reauth_successful"):
        entry.data = dict(data)
        return {"type": "abort", "reason": reason, "reload": True}


class _Abort(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class OptionsFlow:
    def async_show_form(self, step_id, data_schema=None, errors=None):
        return {"type": "form", "step_id": step_id}

    def async_create_entry(self, title, data):
        return {"type": "create_entry", "data": data}


def _expose(hass, assistant, entity_id, value):
    hass.exposure[(assistant, entity_id)] = bool(value)
    hass.expose_calls.append((assistant, entity_id, bool(value)))
    hass.log.append(("expose", assistant, entity_id, bool(value)))


def _should_expose(hass, assistant, entity_id):
    return hass.exposure.get((assistant, entity_id), assistant == "conversation")


def async_redact_data(data, to_redact):
    return {k: ("**REDACTED**" if k in to_redact else v) for k, v in data.items()}


def install() -> None:
    """Put the stand-ins in sys.modules. Idempotent."""
    _install_aiohttp()
    _install_voluptuous()
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "_artiled_stub", False):
        return
    _module("homeassistant", _artiled_stub=True)
    _module("homeassistant.const", CONF_HOST="host", CONF_PASSWORD="password", CONF_USERNAME="username",
            CONF_DEVICE_ID="device_id", CONF_DOMAIN="domain", CONF_PLATFORM="platform", CONF_TYPE="type",
            CONF_ENTITY_ID="entity_id", CONF_EVENT_DATA="event_data",
            EVENT_HOMEASSISTANT_STOP="homeassistant_stop")
    _module("homeassistant.core", HomeAssistant=FakeHass, ServiceCall=ServiceCall, callback=callback)
    _module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
            ConfigEntryNotReady=ConfigEntryNotReady, ConfigEntryAuthFailed=ConfigEntryAuthFailed,
            ServiceValidationError=ServiceValidationError)
    _module("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigEntryState=ConfigEntryState,
            ConfigFlow=ConfigFlow, OptionsFlow=OptionsFlow)
    _module("homeassistant.util", slugify=slugify)
    _module("homeassistant.helpers")
    _module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator,
            UpdateFailed=UpdateFailed, CoordinatorEntity=CoordinatorEntity)
    _module("homeassistant.helpers.device_registry", DeviceInfo=DeviceInfo,
            async_get=lambda hass: hass.device_registry,
            async_entries_for_config_entry=lambda reg, entry_id: [
                d for d in list(reg.devices.values()) if d.config_entry_id == entry_id])
    _module("homeassistant.helpers.entity_registry",
            async_get=lambda hass: hass.entity_registry,
            async_entries_for_config_entry=lambda reg, entry_id: [
                e for e in list(reg.entities.values()) if e.config_entry_id == entry_id],
            async_entries_for_device=lambda reg, device_id, include_disabled_entities=False: [
                e for e in list(reg.entities.values()) if e.device_id == device_id],
            RegistryEntryHider=types.SimpleNamespace(INTEGRATION="integration"),
            COMPUTED_NAME=COMPUTED_NAME)

    def create_issue(hass, domain, issue_id, **kwargs):
        hass.issues[issue_id] = kwargs

    def delete_issue(hass, domain, issue_id):
        hass.issues.pop(issue_id, None)

    _module("homeassistant.helpers.issue_registry", async_create_issue=create_issue,
            async_delete_issue=delete_issue, IssueSeverity=IssueSeverity)
    _module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: hass.session)
    _module("homeassistant.helpers.storage", Store=Store)
    _module("homeassistant.helpers.entity", EntityCategory=EntityCategory, Entity=Entity)
    _module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    _module("homeassistant.components")
    _module("homeassistant.components.media_player", MediaPlayerEntity=MediaPlayerEntity,
            MediaPlayerEntityFeature=MediaPlayerEntityFeature, MediaPlayerState=MediaPlayerState,
            MediaType=MediaType, MediaClass=MediaClass, RepeatMode=RepeatMode, BrowseMedia=BrowseMedia, SearchMedia=SearchMedia,
            SearchMediaQuery=SearchMediaQuery)
    _module("homeassistant.components.scene", Scene=Scene)
    _module("homeassistant.components.sensor", SensorEntity=SensorEntity)
    _module("homeassistant.components.button", ButtonEntity=ButtonEntity)
    _module("homeassistant.components.remote", RemoteEntity=RemoteEntity)
    _module("homeassistant.components.websocket_api", websocket_command=_ws_command,
            async_response=lambda func: func, async_register_command=_ws_register)
    _module("homeassistant.components.diagnostics", async_redact_data=async_redact_data)
    _module("homeassistant.components.homeassistant")
    import voluptuous as vol  # noqa: PLC0415 - the real one, or the stand-in above
    _module("homeassistant.components.device_automation", DEVICE_TRIGGER_BASE_SCHEMA=vol.Schema({
        vol.Required("platform"): "device", vol.Required("domain"): str, vol.Required("device_id"): str}))
    _module("homeassistant.components.homeassistant.triggers")
    _module("homeassistant.components.homeassistant.triggers.event",
            CONF_PLATFORM="platform", CONF_EVENT_TYPE="event_type", CONF_EVENT_DATA="event_data",
            TRIGGER_SCHEMA=lambda config: dict(config), async_attach_trigger=_event_attach)
    _module("homeassistant.components.frontend", add_extra_js_url=_add_extra_js_url)
    _module("homeassistant.components.http", StaticPathConfig=StaticPathConfig)
    _module("homeassistant.loader", async_get_integration=_get_integration)
    _module("homeassistant.components.homeassistant.exposed_entities",
            async_expose_entity=_expose, async_should_expose=_should_expose)


def load_package():
    """The integration, as shipped, under the package name artiled_control."""
    install()
    if "artiled_control" in sys.modules:
        return sys.modules["artiled_control"]
    spec = importlib.util.spec_from_file_location(
        "artiled_control", PKG_DIR / "__init__.py", submodule_search_locations=[str(PKG_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["artiled_control"] = mod
    spec.loader.exec_module(mod)
    # The UDP listener is the one piece that would touch the host. Inert here;
    # test_identity.py drives the follow logic through _heard directly.
    identity = importlib.import_module("artiled_control.identity")

    async def no_listen(loop, port, callback):
        _LISTENERS.append(callback)
        return None
    identity.async_listen = no_listen
    # The dashboard step finds no lovelace in this inert hass and says so at
    # WARNING on every setup; test_dashboard.py checks that path itself.
    import logging  # noqa: PLC0415
    logging.getLogger("artiled_control.dashboard").addHandler(logging.NullHandler())
    return mod


_LISTENERS: list = []


def load_pure(name: str):
    """One pure module by path, without the package (no Home Assistant)."""
    spec = importlib.util.spec_from_file_location(f"artiled_control_{name}", PKG_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# The fake Control
# ---------------------------------------------------------------------------

def room(rid, name, active=0, on=0, off=0, starting=0, starting_name="", disabled=0, **extra):
    row = {"id": rid, "name": name, "icon": "fa-couch", "sort": rid, "disabled": disabled,
           "active_activity": active, "on_activity": on, "off_activity": off,
           "activity_name": "", "activity_icon": "", "starting_activity": starting,
           "starting_name": starting_name, "now_playing": None, "volume": None,
           "volume_display": None, "volume_max": None, "volume_scale": None, "muted": None}
    row.update(extra)
    return row


def zone(zid, name, room_id, **kwargs):
    row = room(zid, name, **kwargs)
    row["room_id"] = room_id
    return row


def activity(aid, name, zone_id=0, room_id=0, buttons=None, play_pause="separate", disabled=0):
    return {"id": aid, "name": name, "icon": "fa-tv", "zone_id": zone_id, "room_id": room_id,
            "sort": aid, "disabled": disabled, "buttons": buttons, "play_pause": play_pause}


class FakeControl:
    """One Control at one TEST-NET address."""

    def __init__(self, ident=IDENT, name="Cinema Control", api="2.00", token=TOKEN):
        self.ident = ident
        self.name = name
        self.api = api
        self.version = "1.57"
        self.token = token
        self.rooms: list[dict] = []
        self.zones: list[dict] = []
        self.activities: list[dict] = []
        self.requests: list[tuple[str, dict]] = []
        self.art_requests: list[str] = []
        self.fail: set[str] = set()
        self.errors: dict[str, tuple[int, str]] = {}
        self.gates: dict[str, asyncio.Event] = {}
        self.button_result = "ok"
        # Unit keys ("zone_11") whose per-unit /activities read fails (error 8).
        self.activity_errors: set[str] = set()
        # Replace the /activities {} answer with this (a malformed one).
        self.activities_override = None
        # endpoint -> a whole answer envelope, returned as is after the token
        # check: for malformed "successful" replies.
        self.raw_answers: dict[str, dict] = {}
        self.users = {"installer": "secret-password"}
        # S3a, search and play (#107 doors). (service, action) -> the shared
        # Find Music answer /unit_find wraps; the Find Movies "titles" answer;
        # activity id -> its hearts' items (a tuple (code, text) = that error).
        self.find_answers: dict[tuple[str, str], dict] = {}
        self.film_titles: list[dict] = []
        self.favorites: dict[int, object] = {}
        # What the Activity Service says happened to a start's play.
        self.play_state = "sent"
        # The API extension (play-playlists): /unit_find and /unit_find_movies
        # take a named activity while the unit's Off activity is the active one.
        self.find_off_named = False
        # S3c /devices: the device rows (API 'device' structure); /commands
        # per device id.
        self.devices: list[dict] = []
        self.commands: dict[int, list[dict]] = {}
        # S3c /unit_player: (kind, id) -> the player's state answer; action ->
        # the state the player is in after that change (absent: unchanged).
        self.player_state: dict[tuple[str, int], dict] = {}
        self.player_answers: dict[str, dict] = {}

    def _unit_row(self, data):
        if "zone" in data:
            return next((z for z in self.zones if z["id"] == data["zone"]), None)
        if "room" in data:
            return next((r for r in self.rooms if r["id"] == data["room"]), None)
        return None

    def _door(self, data, wants):
        """The token doors' choice of player (player_door_where /
        unit_find_movies_where): the RUNNING activity's, or - only while
        nothing runs (active_activity 0) - the activity the request names."""
        row = self._unit_row(data)
        if row is None:
            return None, {"success": 0, "error": 5, "error_text": "Unknown unit", "data": ""}
        running = int(row.get("active_activity") or 0)
        named = data.get("activity") if isinstance(data.get("activity"), int) else 0
        idle = running <= 0 and named > 0
        if self.find_off_named and named > 0 and running == int(row.get("off_activity") or 0):
            idle = True   # the API extension: a named activity while the unit's Off runs
        chosen = named if idle else running
        if chosen <= 0:
            return None, {"success": 0, "error": 7, "error_text": "Nothing is playing in this zone", "data": ""}
        act = next((a for a in self.activities if a["id"] == chosen), None)
        if act is None or not wants(act):
            return None, {"success": 0, "error": 7, "error_text": "The running activity plays no music here", "data": ""}
        return (act, idle), None

    def answer(self, endpoint, data, token):
        if endpoint == "login":
            if self.users.get(data.get("username")) == data.get("password"):
                return {"success": 1, "error": 0, "error_text": "", "data": {"token": self.token}}
            return {"success": 0, "error": 1, "error_text": "Login failed", "data": ""}
        if token != self.token:
            return {"success": 0, "error": 2, "error_text": "Token Invalid", "data": ""}
        if endpoint in self.raw_answers:
            return json.loads(json.dumps(self.raw_answers[endpoint]))
        if endpoint in self.errors:
            code, text = self.errors[endpoint]
            return {"success": 0, "error": code, "error_text": text, "data": ""}
        if endpoint == "version":
            body = {"version": self.version}
            if self.api:
                body["api"] = self.api
            return _ok(body)
        if endpoint == "rooms":
            return _ok({"rooms": json.loads(json.dumps(self.rooms))})
        if endpoint == "zones":
            return _ok({"zones": json.loads(json.dumps(self.zones))})
        if endpoint == "activities":
            for key in self.activity_errors:
                kind, _, item = key.partition("_")
                if str(data.get(kind)) == item:
                    return {"success": 0, "error": 8, "error_text": "Service Unavailable", "data": ""}
            if not data and self.activities_override is not None:
                return _ok(self.activities_override)
            enabled = [a for a in self.activities if not a.get("disabled")]
            if "zone" in data:
                rows = [a for a in enabled if a["zone_id"] == int(data["zone"])]
            elif "room" in data:
                rows = [a for a in enabled if a["room_id"] == int(data["room"])]
            elif self.api:
                rows = enabled
            else:
                return {"success": 0, "error": 4, "error_text": "Invalid Data", "data": ""}
            return _ok({"activities": json.loads(json.dumps(rows))})
        if endpoint == "activity":
            ids = {a["id"] for a in self.activities}
            if data.get("action") == "start" and int(data.get("activity", 0)) not in ids:
                return {"success": 0, "error": 5, "error_text": "Activity not found", "data": ""}
            act = next((a for a in self.activities if a["id"] == int(data.get("activity", 0))), {})
            if data.get("play") is not None:
                return _ok({"activity": data.get("activity"), "zone": act.get("zone_id", 0), "room": act.get("room_id", 0),
                            "action": "start", "play": {"requested": data["play"], "state": self.play_state,
                                                        "start": "1"}})
            return _ok({"activity": data.get("activity"), "action": data.get("action"), "start": "accepted"})
        if endpoint == "button":
            return _ok({"zone": data.get("zone"), "button": data.get("button"), "result": self.button_result})
        if endpoint == "unit_find":
            found, fail = self._door(data, lambda a: a.get("music_service") and a.get("music_where"))
            if fail:
                return fail
            act, idle = found
            expect = data.get("expect")
            if expect is not None and (expect.get("activity") != act["id"] or expect.get("service") != act["music_service"]):
                return {"success": 0, "error": 7, "error_text": "The running activity changed: nothing was done",
                        "data": {"changed": 1}}
            answer = self.find_answers.get((act["music_service"], data.get("action")), {"items": [], "error": ""})
            unit = "zone" if "zone" in data else "room"
            return _ok({unit: data[unit], "activity": act["id"], "idle": idle, "service": act["music_service"],
                        "target": act["music_where"], "action": data.get("action"),
                        "answer": json.loads(json.dumps(answer))})
        if endpoint == "commands":
            did = data.get("device")
            if not any(d["id"] == did for d in self.devices):
                return {"success": 0, "error": 5, "error_text": "Device not found", "data": ""}
            return _ok({"device": did, "name": "", "commands": json.loads(json.dumps(self.commands.get(did, [])))})
        if endpoint == "command":
            return _ok({"device": data.get("device"), "command": data.get("command")})
        if endpoint == "devices":
            return _ok({"devices": json.loads(json.dumps(self.devices))})
        if endpoint == "unit_player":
            found, fail = self._door(data, lambda a: a.get("music_service") and a.get("music_where"))
            if fail:
                return fail
            act, _ = found
            kind = "zone" if "zone" in data else "room"
            key = (kind, data[kind])
            action = data.get("action", "state")
            base = {kind: data[kind], "activity": act["id"], "service": act["music_service"]}
            if action == "state":
                return _ok({**base, **json.loads(json.dumps(self.player_state.get(key, {})))})
            if action in self.player_answers:
                self.player_state[key] = dict(self.player_answers[action])
            value = {k: data[k] for k in ("seconds", "shuffle", "repeat") if k in data}
            return _ok({**base, "action": action, **value})
        if endpoint == "unit_find_movies":
            found, fail = self._door(data, lambda a: int(a.get("movie_player") or 0) > 0)
            if fail:
                return fail
            act, idle = found
            unit = "zone" if "zone" in data else "room"
            return _ok({unit: data[unit], "activity": act["id"], "idle": idle, "device": act["movie_player"],
                        "action": data.get("action"),
                        "answer": {"ok": True, "titles": json.loads(json.dumps(self.film_titles)), "players": []}})
        if endpoint == "activity_favorites":
            aid = data.get("activity")
            got = self.favorites.get(aid)
            if isinstance(got, tuple):
                return {"success": 0, "error": got[0], "error_text": got[1], "data": ""}
            if got is None:
                return {"success": 0, "error": 7, "error_text": "This activity plays no favourites here", "data": ""}
            return _ok({"activity": aid, "service": got["service"], "count": len(got["items"]),
                        "items": json.loads(json.dumps(got["items"]))})
        return {"success": 0, "error": 4, "error_text": "Unknown", "data": ""}


def _ok(data):
    return {"success": 1, "error": 0, "error_text": "", "data": data}


class FakeResponse:
    def __init__(self, body=None, status=200, raw=b"", headers=None):
        self._body = body
        self.status = status
        self._raw = raw
        self.headers = headers or {}

    async def json(self, content_type=None):
        return self._body

    async def read(self):
        return self._raw


class FakeSession:
    """aiohttp.ClientSession for the fake Controls, by address. Unknown
    addresses raise ClientError - nothing ever reaches a network."""

    def __init__(self):
        self.boxes: dict[str, FakeControl] = {}
        self.log: list[tuple[str, str]] = []
        self.outside: dict[str, bytes] = {}
        # outside address -> an HTTP status other than 200 (a dead cover)
        self.outside_status: dict[str, int] = {}

    def _box(self, url):
        host = urlparse(url).hostname
        return host, self.boxes.get(host)

    async def post(self, url, data=None, timeout=None):
        import aiohttp
        host, box = self._box(url)
        endpoint = urlparse(url).path.rsplit("/", 1)[-1]
        self.log.append(("POST", f"{host}/{endpoint}"))
        if box is None or endpoint in box.fail:
            raise aiohttp.ClientError(f"Cannot connect to host {host}")
        payload = json.loads((data or {}).get("data") or "{}")
        box.requests.append((endpoint, payload))
        gate = box.gates.get(endpoint)
        if gate is not None:
            await gate.wait()
        return FakeResponse(box.answer(endpoint, payload, (data or {}).get("token")))

    async def head(self, url, timeout=None, allow_redirects=True):
        """HEAD: only an outside address's status (a cover check)."""
        import aiohttp
        self.log.append(("HEAD", url))
        if url in self.outside_status:
            return FakeResponse(status=self.outside_status[url])
        if url in self.outside:
            return FakeResponse(status=200, headers={"Content-Type": "image/png"})
        raise aiohttp.ClientError(f"Cannot connect to {urlparse(url).hostname}")

    async def get(self, url, timeout=None, allow_redirects=True):
        import aiohttp
        host, box = self._box(url)
        parsed = urlparse(url)
        self.log.append(("GET", url))
        if parsed.path.endswith("/api/alive"):
            if box is None or "alive" in box.fail:
                raise aiohttp.ClientError(f"Cannot connect to host {host}")
            box.requests.append(("alive", {}))
            gate = box.gates.get("alive")
            if gate is not None:
                await gate.wait()
            return FakeResponse({"success": 1, "error": 0, "error_text": "",
                                 "data": {"name": box.name, "ident": box.ident}})
        if box is not None:
            box.art_requests.append(url)
            token = (parse_qs(parsed.query).get("token") or [""])[0]
            if parsed.path == "/api/unit_player_art" and token != box.token:
                return FakeResponse(status=404)
            return FakeResponse(status=200, raw=b"\xff\xd8JPEG", headers={"Content-Type": "image/jpeg"})
        if url in self.outside_status:
            return FakeResponse(status=self.outside_status[url])
        if url in self.outside:
            return FakeResponse(status=200, raw=self.outside[url], headers={"Content-Type": "image/png"})
        raise aiohttp.ClientError(f"Cannot connect to host {host}")


def house(control: FakeControl) -> FakeControl:
    """The reference fixture: a Living Room with two zones (Main, Secondary),
    a Kitchen without zones, a Bedroom with a zone called Main too, a Garage
    with only an Off activity, and a hidden Study."""
    control.rooms = [
        room(1, "Living Room"),
        room(2, "Kitchen", active=21, on=20, off=21),
        room(3, "Bedroom"),
        room(4, "Garage", active=40, on=0, off=40),
        room(5, "Study", on=50, off=51, disabled=1),
    ]
    control.zones = [
        zone(11, "Main", 1, active=111, on=110, off=111),
        zone(12, "Secondary", 1, on=120, off=121),
        zone(31, "Main", 3, on=310, off=311),
    ]
    control.activities = [
        activity(110, "Watch Netflix", zone_id=11, room_id=1, buttons=["play", "pause", "next", "previous", "volume_up", "volume_down"]),
        activity(112, "Kaleidescape", zone_id=11, room_id=1, buttons=["play_pause", "stop"], play_pause="toggle"),
        activity(113, "Watch Netflix", zone_id=11, room_id=1),
        activity(111, "Off", zone_id=11, room_id=1),
        activity(120, "Listen to Roon", zone_id=12, room_id=1, buttons=None),
        activity(121, "Off", zone_id=12, room_id=1),
        activity(20, "Listen to Sonos", room_id=2),
        activity(21, "Off", room_id=2),
        activity(310, "Watch TV", zone_id=31, room_id=3),
        activity(311, "Off", zone_id=31, room_id=3),
        activity(40, "Off", room_id=4),
        activity(50, "Read", room_id=5),
        activity(51, "Off", room_id=5),
    ]
    return control


async def setup_integration(control: FakeControl | None = None, host="192.0.2.10", entry_id="e1",
                            assist_all=False, hass: FakeHass | None = None):
    """A loaded entry against a fake Control. Returns (hass, entry, control)."""
    load_package()
    if hass is None:
        hass = FakeHass()
        hass.session = FakeSession()
    control = control or house(FakeControl())
    hass.session.boxes[host] = control
    entry = ConfigEntry(entry_id, control.ident, {"host": host, "token": control.token, "assist_all": assist_all},
                        title=control.name)
    hass.config_entries.entries[entry_id] = entry
    await hass.config_entries.setup(entry)
    return hass, entry, control


class Checker:
    """The Hub tests' check(): print, count, exit non-zero on any failure."""

    def __init__(self):
        self.failures: list[str] = []
        self.passed = 0

    def __call__(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            print(f"  ok   {name}")
        else:
            self.failures.append(name)
            print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))

    def done(self, suite):
        print()
        if self.failures:
            print(f"{len(self.failures)} FAILED: {', '.join(self.failures)}")
            sys.exit(1)
        print(f"all {suite} rules pass ({self.passed} checks)")
        sys.exit(0)


def cloud(alexa: bool, google: bool, logged_in: bool = True):
    """hass.data["cloud"] as Home Assistant Cloud keeps it (2026.10.4: the
    Cloud object; client.prefs.alexa_enabled / google_enabled)."""
    return types.SimpleNamespace(is_logged_in=logged_in, client=types.SimpleNamespace(
        prefs=types.SimpleNamespace(alexa_enabled=alexa, google_enabled=google)))


def age(hass, entry_id: str, seconds: float) -> None:
    """Move the inventory memory `seconds` into the past - as if that much time
    of successful rounds had passed since everything in it was last seen."""
    memory = hass.data["artiled_control_memory"][entry_id]
    for key in list(memory["seen"]):
        memory["seen"][key] -= seconds
    for field in ("last_success", "first_success"):
        if memory.get(field) is not None:
            memory[field] -= seconds


async def poll(hass, entry_id: str = "e1", inventory: bool = False) -> None:
    """One poll of the entry's CURRENT coordinator, then let any reload run."""
    coordinator = hass.data["artiled_control"][entry_id]["coordinator"]
    if inventory:
        coordinator.inventory_due = True
    await coordinator.async_refresh()
    await hass.settle()


async def stop(hass) -> None:
    """Cancel every pending confirmation, so a test ends cleanly."""
    for data in hass.data.get("artiled_control", {}).values():
        data["coordinator"].cancel_pending()
    for task in hass.tasks:
        if not task.done():
            task.cancel()
    await hass.settle()
