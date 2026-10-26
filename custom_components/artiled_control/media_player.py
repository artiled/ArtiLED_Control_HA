# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""One media_player per unit - a zone, or a room without zones.

On/off is the unit's On and Off activity, the source is the activity, and
volume, mute, transport and Now Playing go through the activity's routing -
exactly what the Control's webapp tile and remote do.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArtiControlAuthError, ArtiControlError
from .const import DOMAIN
from .entity_ids import unit_unique_id
from .cache import ART_KEEP, BROWSE_ART_PX, KeptCache, shrink_picture
from .media import (
    NOT_PLAYED,
    album_results,
    browse_tree,
    content_id,
    current_activity,
    favorite_results,
    favorites_id,
    film_results,
    merge,
    parse_content_id,
    parse_favorites_id,
    play_fits,
    player_activities,
    player_of,
    retarget,
    runs_here,
    search_jobs,
    search_word,
)
from .units import (
    activity_row,
    artwork,
    display_activity,
    is_music,
    is_toggle,
    legacy_id,
    media_state,
    media_title,
    now_playing,
    position_is_news,
    position_reading,
    source_list,
    starting,
    transport_keys,
    EVENT,
    PLAYBACK_READ_SECONDS,
    playback_activity,
    playback_view,
    seek_seconds,
    unit_events,
    unit_on,
    volume_button,
    volume_view,
)

_STATES = {
    "off": MediaPlayerState.OFF,
    "on": MediaPlayerState.ON,
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
}

# S3a: search, play and browse - only on a unit with an activity that plays
# music or films a play can name (media.has_player).
_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.SEARCH_MEDIA
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
)

# The "Other sources" folder of a running unit's browse root.
_OTHERS = "others"
# Outside covers checked per look, and how long a verdict holds (seconds).
_ART_CHECKS = 8
_ART_KEEP = 7 * 86400
_ART_RETRY = 3600

# How many browse/search pictures one unit remembers for Home Assistant's
# image proxy (an opaque id -> the Control's picture address).
_ART_IDS_MAX = 2000

_TRANSPORT = {
    "play": MediaPlayerEntityFeature.PLAY,
    "pause": MediaPlayerEntityFeature.PAUSE,
    "stop": MediaPlayerEntityFeature.STOP,
    "next": MediaPlayerEntityFeature.NEXT_TRACK,
    "previous": MediaPlayerEntityFeature.PREVIOUS_TRACK,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    # ⚠ Only OFFERED units, and not hidden ones (Michel 2026-10-25, N2): a unit
    # with nothing but its Off activity is not a room anyone can start anything
    # in. One that gets its first activity later appears on the next inventory
    # round, which reloads the entry.
    players = [ArtiControlMediaPlayer(data, key) for key in data["shown_units"]]
    # The Voice page's Favourites list draws its pictures through each unit's
    # own image proxy (websocket artiled_control/favorites).
    data["players"] = {p._key: p for p in players}
    async_add_entities(players)


def ownership_problem(data: dict) -> str | None:
    """Why this entry may no longer send anything, or None.

    The entry was unloaded, reloaded or reconfigured (its generation retired),
    or a different Control now answers at its address.
    """
    if data.get("retired") or not data["fence"].is_current(data["client"].generation):
        return "the ArtiCONTROL entry changed while this command waited - nothing was sent"
    if data["coordinator"].wrong_control:
        return "a different Control answers at this address - nothing was sent"
    return None


async def run_command(data: dict, key: str, unit_name: str, command, confirm: bool = True, still_valid=None):
    """Send one command for one unit, in order, then look again.

    ⚠ The per-unit lock is for ORDERING only: two of Home Assistant's own
    commands for the same unit go out one after the other. It is not a product
    lock - it is released before confirmation, never held through a wait, and
    adds no busy or refusal state. Nothing is replayed after a timeout or an
    uncertain reply: a start may have happened, and sending it again is the
    Control's decision to make, not ours.

    ⚠ CHECKED AGAIN AT DISPATCH (Mycroft 8662, HA-S2). A command may wait behind
    another; by the time its turn comes the entry may have reloaded, or the
    inventory may have moved the activity it was for. Ownership is re-checked
    for every command, and `still_valid()` (the caller's own binding, from
    what is already known - no extra read) for those that have one. Room Off
    has none: it is the unit's Off whatever runs.
    """
    coordinator = data["coordinator"]
    lock = data["locks"].setdefault(key, asyncio.Lock())
    async with lock:
        problem = ownership_problem(data) or (still_valid() if still_valid else None)
        if problem:
            raise HomeAssistantError(f"{unit_name}: {problem}")
        try:
            result = await command()
        except ArtiControlAuthError as err:
            raise HomeAssistantError(
                f"{unit_name}: the Control no longer accepts our sign-in. "
                "Sign in again under Settings > Devices & services > ArtiCONTROL."
            ) from err
        except ArtiControlError as err:
            if err.code == 5:
                # Unknown activity: the inventory is out of date - read it now.
                coordinator.inventory_due = True
            raise HomeAssistantError(f"{unit_name}: {err}") from err
    if confirm:
        coordinator.confirm()
    return result


def _browse_kind(row: dict | None) -> str:
    """"movies" for an activity that plays films, "music" for one that plays
    music, "" otherwise (then the room's own decides)."""
    player = player_of(row)
    return "movies" if player["movie_player"] > 0 else ("music" if player["service"] else "")


async def play_on_unit(data: dict, key: str, unit_name: str, activity_id: int, play: dict) -> tuple[int, str]:
    """Play one item in one unit: on the activity running NOW when its player
    can play it (no other source started - Michel 2026-10-26), else by
    starting the activity that owns it, with the item as its play
    (/activity start {"play"}). Refused - nothing sent - when the play does
    not belong to that activity's own player. -> (activity used, the API's
    play state). Shared by the media player and the voice favourites."""
    def offered() -> list[dict]:
        return ((data["coordinator"].data or {}).get("offered") or {}).get(key) or []

    raw = (((data["coordinator"].data or {}).get("units") or {}).get(key) or {}).get("raw") or {}
    current = current_activity(raw)
    running = next((r for r in offered() if int(r["id"]) == current), None)
    owner = next((r for r in offered() if int(r["id"]) == activity_id), None)
    if running is not None and owner is not None and int(running["id"]) != activity_id \
            and runs_here(running, owner, play):
        activity_id = int(running["id"])

    def problem() -> str | None:
        row = next((r for r in offered() if int(r["id"]) == activity_id), None)
        if row is None:
            return f"that is not an activity of {unit_name} - nothing was sent"
        why = play_fits(row, play)
        return f"{why} - nothing was sent" if why else None

    why = problem()
    if why:
        raise HomeAssistantError(f"{unit_name}: {why}")
    result = await run_command(data, key, unit_name, lambda: data["client"].start_with_play(activity_id, play),
                               still_valid=problem)
    return activity_id, str(result["play"].get("state") or "unknown")


class ArtiControlMediaPlayer(CoordinatorEntity, MediaPlayerEntity):
    """One unit. State always comes from the Control; the only local memory is
    the last published position (so a steady play is not a recorder write every
    poll) and the last cover of the running activity (so a gap keeps it)."""

    _attr_has_entity_name = True
    _attr_name = None
    # ⚠ The browser only ever sees Home Assistant's own proxy URL: never the
    # Control's token, never a LAN address. See async_get_media_image.
    _attr_media_image_remotely_accessible = False
    # Long and changing: kept out of the recorder.
    _unrecorded_attributes = frozenset({"activities", "buttons"})

    def __init__(self, data: dict, key: str) -> None:
        super().__init__(data["coordinator"])
        self._data = data
        self._client = data["client"]
        self._key = key
        unit = data["coordinator"].data["units"][key]
        self._kind = unit["kind"]
        self._item_id = unit["id"]
        self._attr_unique_id = unit_unique_id(data["ident"], self._kind, self._item_id)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._attr_unique_id)})
        self._kept_art: tuple | None = None
        self._art: str | None = None
        self._reading: dict | None = None
        self._position: int | None = None
        self._position_at: datetime | None = None
        # S3a: the browse/search pictures handed to Home Assistant, by an
        # opaque id. ⚠ Home Assistant's proxy URL carries only that id - never
        # the Control's address, art route or token (see _thumbnail).
        self._art_ids: OrderedDict[str, str] = OrderedDict()
        # The last play's title, activity and the API's own word for it.
        self._last_play: dict | None = None
        # Lists read from the Control for browse and search, per round (_cached).
        self._lists: dict[tuple, tuple] = {}
        # S3c: the running music player's shuffle / repeat / can_seek, for
        # the activity it was read for, and when (monotonic).
        self._playback: dict | None = None
        self._playback_for = 0
        self._playback_at = 0.0
        self._playback_task: asyncio.Task | None = None
        # S3b: the raw record of the last look, to tell what changed.
        self._prev_raw: dict | None = dict(self._raw) if self._raw else None
        self._refresh_memory()

    # ---- what the coordinator says about this unit --------------------------

    @property
    def _unit(self) -> dict | None:
        return (self.coordinator.data or {}).get("units", {}).get(self._key)

    @property
    def _raw(self) -> dict:
        unit = self._unit
        return unit["raw"] if unit else {}

    @property
    def _offered(self) -> list[dict]:
        return (self.coordinator.data or {}).get("offered", {}).get(self._key) or []

    @property
    def _unit_name(self) -> str:
        return (self.coordinator.data or {}).get("names", {}).get(self._key) or self._key

    @property
    def _activity(self) -> dict | None:
        rows = (self.coordinator.data or {}).get("activities", {}).get(self._key)
        return activity_row(rows, display_activity(self._raw))

    @property
    def available(self) -> bool:
        # A hidden unit, or one that lost its last activity, is unavailable AT
        # ONCE; the reload and the grace hour decide the rest (N3).
        unit = self._unit
        return super().available and unit is not None and not unit["hidden"] and bool(self._offered)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_memory()
        self._fire_events()
        self._playback_due()
        super()._handle_coordinator_update()

    # ---- S3c: seek, shuffle, repeat (the webapp remote's playback.js) -------

    def _playback_due(self) -> None:
        """Read the player's state when the running music activity changed,
        or every PLAYBACK_READ_SECONDS while it runs (the remote's
        PLAYBACK_STATE_MS). Not on every 5-second poll."""
        if self.hass is None or not self.coordinator.last_update_success:
            return
        rows = (self.coordinator.data or {}).get("activities", {}).get(self._key)
        wanted = playback_activity(self._raw, rows)
        if not wanted:
            self._playback, self._playback_for = None, 0
            return
        fresh = time.monotonic() - self._playback_at < PLAYBACK_READ_SECONDS
        if wanted == self._playback_for and fresh:
            return
        if self._playback_task is not None and not self._playback_task.done():
            return
        self._playback_task = self.hass.async_create_task(self._read_playback(wanted))

    async def _read_playback(self, activity_id: int) -> None:
        try:
            data = await self._client.unit_player(self._scope, {"action": "state"})
        except ArtiControlError:
            data = None
        # ⚠ An answer for another activity, or none, is NOT the old state.
        if isinstance(data, dict) and int(data.get("activity") or 0) != activity_id:
            data = None
        self._playback = playback_view(data)
        self._playback_for = activity_id
        self._playback_at = time.monotonic()
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def _playback_now(self) -> dict | None:
        """The player's state, only while that activity still runs."""
        rows = (self.coordinator.data or {}).get("activities", {}).get(self._key)
        if not self._playback or playback_activity(self._raw, rows) != self._playback_for:
            return None
        return self._playback

    @property
    def shuffle(self) -> bool | None:
        return (self._playback_now or {}).get("shuffle")

    @property
    def repeat(self) -> RepeatMode | None:
        value = (self._playback_now or {}).get("repeat")
        return RepeatMode(value) if value else None

    async def _player_change(self, request: dict) -> None:
        """A change, then the player's own state read back - the attributes
        show what it did, never what was asked (playback.js change())."""
        activity_id = self._playback_for
        try:
            await self._send(lambda: self._client.unit_player(self._scope, request), confirm=False)
        finally:
            if activity_id:
                await self._read_playback(activity_id)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        if (self._playback_now or {}).get("shuffle") is None:
            raise HomeAssistantError(f"{self._unit_name}: the player has not said its shuffle - nothing was sent")
        await self._player_change({"action": "shuffle", "shuffle": "on" if shuffle else "off"})

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        if (self._playback_now or {}).get("repeat") is None:
            raise HomeAssistantError(f"{self._unit_name}: the player has not said its repeat - nothing was sent")
        await self._player_change({"action": "repeat", "repeat": str(getattr(repeat, "value", repeat))})

    async def async_media_seek(self, position: float) -> None:
        duration = self.media_duration
        if not (self._playback_now or {}).get("can_seek") or not duration:
            raise HomeAssistantError(f"{self._unit_name}: this player cannot seek - nothing was sent")
        seconds = seek_seconds(position, duration)
        await self._send(lambda: self._client.unit_player(self._scope, {"action": "seek", "seconds": seconds}),
                         confirm=False)
        # The webapp moves the bar to the seeked second on success (seeked()).
        self._position = seconds
        self._position_at = datetime.now(timezone.utc)
        self.async_write_ha_state()

    def _fire_events(self) -> None:
        """S3b: activity started / stopped, paused / resumed, on the bus as
        artiled_control_event with this unit's device - what the device
        triggers listen for (device_trigger.py). Only from a SUCCESSFUL poll
        compared with the last one: a failed poll changes nothing."""
        if not self.coordinator.last_update_success or self._unit is None or self.hass is None:
            return
        raw = self._raw
        rows = (self.coordinator.data or {}).get("activities", {}).get(self._key)
        found = unit_events(self._prev_raw, raw, rows)
        self._prev_raw = dict(raw)
        if not found:
            return
        device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self._attr_unique_id)})
        for event in found:
            self.hass.bus.async_fire(EVENT, {
                "device_id": device.id if device else None,
                "entity_id": self.entity_id,
                "unit": self._unit_name,
                **event,
            })

    def _refresh_memory(self) -> None:
        raw = self._raw
        self._art, self._kept_art = artwork(self._kept_art, raw)
        reading = position_reading(now_playing(raw))
        if position_is_news(self._reading, reading):
            self._reading = reading
            if reading is None or reading["position"] is None:
                self._position = None
                self._position_at = None
            else:
                self._position = reading["position"]
                # ⚠ The daemon's own time of reading, never the poll's.
                self._position_at = datetime.fromtimestamp(reading["updated"], tz=timezone.utc)

    # ---- state --------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        return _STATES[media_state(self._raw)]

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )
        if player_activities(self._offered):
            # Also while off: playing starts the activity that plays it.
            features |= _PLAYER_FEATURES
        if not unit_on(self._raw):
            return features
        for key in transport_keys(self._activity):
            features |= _TRANSPORT.get(key, MediaPlayerEntityFeature(0))
        playback = self._playback_now or {}
        if isinstance(playback.get("shuffle"), bool):
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if playback.get("repeat"):
            features |= MediaPlayerEntityFeature.REPEAT_SET
        if playback.get("can_seek") and self.media_duration:
            features |= MediaPlayerEntityFeature.SEEK
        if volume_view(self._raw)["can_set"]:
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
        return features

    @property
    def source(self) -> str | None:
        if not unit_on(self._raw):
            return None
        wanted = display_activity(self._raw)
        for label, activity_id in source_list(self._offered, self._raw.get("off_activity")):
            if activity_id == wanted:
                return label
        return str(self._raw.get("starting_name") or self._raw.get("activity_name") or "") or None

    @property
    def source_list(self) -> list[str]:
        return [label for label, _ in source_list(self._offered, self._raw.get("off_activity"))]

    @property
    def volume_level(self) -> float | None:
        # ⚠ None when the level is not KNOWN - never 0.5 (gap G2).
        return volume_view(self._raw)["level"]

    @property
    def is_volume_muted(self) -> bool | None:
        # Read from "muted" (API 2.00, G1); None on older Controls or unknown.
        return volume_view(self._raw)["muted"]

    @property
    def media_title(self) -> str | None:
        return media_title(now_playing(self._raw))

    @property
    def media_artist(self) -> str | None:
        np = now_playing(self._raw)
        return (str(np.get("artist") or "") or None) if np else None

    @property
    def media_album_name(self) -> str | None:
        np = now_playing(self._raw)
        return (str(np.get("album") or "") or None) if np else None

    @property
    def media_duration(self) -> int | None:
        np = now_playing(self._raw)
        duration = int(np.get("duration") or 0) if np else 0
        return duration if duration > 0 else None

    @property
    def media_content_type(self) -> str | None:
        np = now_playing(self._raw)
        if not media_title(np):
            return None
        return MediaType.MUSIC if is_music(np) else MediaType.MOVIE

    @property
    def media_position(self) -> int | None:
        return self._position if now_playing(self._raw) else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._position_at if now_playing(self._raw) else None

    @property
    def media_image_url(self) -> str | None:
        # The URL as the Control publishes it - WITHOUT the token. Home
        # Assistant only hashes it here; the picture is fetched below.
        return self._art

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch the cover through our own client.

        ⚠ Home Assistant's default fetch logs the image URL when it fails, and
        a Control art URL is only loadable with token= added. Ours adds the
        token only to this Control's own art route, never follows a redirect
        with it, and says nothing about the URL when it fails.
        """
        if not self._art:
            return None, None
        return await self._client.fetch_art(self._art)

    @property
    def _order(self) -> int:
        """This unit's position among the Control's units (Control order)."""
        keys = list((self.coordinator.data or {}).get("units", {}))
        return keys.index(self._key) if self._key in keys else len(keys)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self._raw
        unit = self._unit or {}
        s = starting(raw)
        activity = self._activity
        volume = volume_view(raw)
        return {
            "unit_kind": self._kind,
            "unit_id": self._item_id,
            "room": unit.get("caption") or (unit.get("name") if self._kind == "room" else ""),
            "activity_id": display_activity(raw) if unit_on(raw) else 0,
            "starting": s > 0,
            "starting_name": str(raw.get("starting_name") or "") if s > 0 else "",
            "on_activity": int(raw.get("on_activity") or 0),
            "off_activity": int(raw.get("off_activity") or 0),
            "volume_display": volume["display"],
            "volume_scale": raw.get("volume_scale") if volume["can_set"] else None,
            # S2 dashboard: the webapp slider's right end, so the tile and the
            # remote turn a position into the device's own number exactly as
            # app/js/controls.js does (level_for_percent + volume_text).
            "volume_max": raw.get("volume_max") if volume["can_set"] else None,
            # S2 dashboard: the unit's own icon (tiles.php app_tile_icon: the
            # running activity's icon while on, the unit's otherwise) and its
            # place in the Control's order (units_from_lists), which the
            # dashboard lists the tiles in.
            "unit_icon": str(raw.get("icon") or ""),
            "unit_order": self._order,
            "buttons": (activity or {}).get("buttons"),
            "play_pause": (activity or {}).get("play_pause"),
            "activities": [
                {"id": int(row["id"]), "name": str(row.get("name") or ""), "icon": str(row.get("icon") or "")}
                for row in self._offered
            ],
            # S3a: the last play from Home Assistant and what the Control said
            # happened to it (sent, attached, accepted ...) - a start is not
            # proof of playback, so this is never "playing".
            "play": self._last_play,
            # The dashboard's Browse Music / Browse Movies: what this room's
            # activities can play (any of them, running or not).
            "plays_music": any(player_of(r)["service"] for r in self._player_rows()),
            "plays_movies": any(player_of(r)["movie_player"] > 0 for r in self._player_rows()),
            # What the Browse shortcut opens: the running activity's kind.
            "browse_kind": _browse_kind(activity if unit_on(raw) else None),
        }

    # ---- commands -----------------------------------------------------------

    async def _send(self, command, confirm: bool = True, still_valid=None):
        return await run_command(self._data, self._key, self._unit_name, command, confirm, still_valid)

    async def _start(self, activity_id: int, still_valid=None) -> None:
        await self._send(lambda: self._client.start_activity(activity_id), still_valid=still_valid)

    async def async_turn_on(self) -> None:
        on_id = int(self._raw.get("on_activity") or 0)
        if on_id <= 0:
            # The webapp's own refusal (app/api/set.php).
            raise HomeAssistantError(f"No On activity for {self._unit_name}")
        await self._start(on_id)

    async def async_turn_off(self) -> None:
        """The unit's Off activity. ALWAYS - whatever runs.

        ⚠ Never /activity stop: that skips the Off activity's own steps (TV and
        amplifier off), so "off" in Home Assistant would mean something other
        than off on the webapp and the remote (Michel 2026-10-25, Q5).
        """
        off_id = int(self._raw.get("off_activity") or 0)
        if off_id <= 0:
            raise HomeAssistantError(f"No Off activity for {self._unit_name}")
        await self._start(off_id)

    async def async_select_source(self, source: str) -> None:
        """Start the activity behind a source label. An unknown name is an
        error, never silence - the UC driver's lesson."""
        for label, activity_id in source_list(self._offered, self._raw.get("off_activity")):
            if label == source:
                def still_valid() -> str | None:
                    # Still one of this unit's offered activities at dispatch.
                    if any(int(r["id"]) == activity_id for r in self._offered):
                        return None
                    return f"{source} is no longer an activity of {self._unit_name}"
                await self._start(activity_id, still_valid)
                return
        raise HomeAssistantError(f"{source} is not an activity of {self._unit_name}")

    async def _key_press(self, key: str, confirm: bool = True) -> None:
        legacy = legacy_id(self._kind, self._item_id)
        await self._send(lambda: self._client.button(legacy, key), confirm)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._key_press(volume_button(volume))

    async def async_volume_up(self) -> None:
        await self._key_press("volume_up", confirm=False)
        # The Activity Service keeps the level from the device's reply before
        # it answers, so one read now shows it (as the webapp's key.php does).
        await self.coordinator.async_refresh()

    async def async_volume_down(self) -> None:
        await self._key_press("volume_down", confirm=False)
        await self.coordinator.async_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        # Discrete Mute and Unmute, never the toggle: a toggle on an unknown
        # state could do the opposite of what was asked.
        await self._key_press("mute" if mute else "unmute")

    async def async_media_play(self) -> None:
        await self._key_press("play_pause" if self._toggle_only("play") else "play")

    async def async_media_pause(self) -> None:
        await self._key_press("play_pause" if self._toggle_only("pause") else "pause")

    async def async_media_play_pause(self) -> None:
        if is_toggle(self._activity):
            await self._key_press("play_pause")
        elif self.state == MediaPlayerState.PLAYING:
            await self._key_press("pause")
        else:
            await self._key_press("play")

    def _toggle_only(self, key: str) -> bool:
        """The source has one combined Play/Pause and no separate key."""
        activity = self._activity
        buttons = (activity or {}).get("buttons")
        return is_toggle(activity) and isinstance(buttons, list) and key not in buttons

    async def async_media_stop(self) -> None:
        await self._key_press("stop")

    async def async_media_next_track(self) -> None:
        await self._key_press("next")

    async def async_media_previous_track(self) -> None:
        await self._key_press("previous")

    # ---- S3a: search, play and browse --------------------------------------

    @property
    def _scope(self) -> dict[str, int]:
        return {self._kind: int(self._item_id)}

    def _player_rows(self) -> list[dict]:
        return player_activities(self._offered)

    def _thumbnail(self, url: str) -> str:
        """Home Assistant's own image proxy URL for a Control picture.

        ⚠ The URL Home Assistant shows and logs carries an opaque id only. The
        Control's picture address stays in this entity; the token is added
        server-side by the client (art_request) when the proxy asks.
        """
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        self._art_ids[key] = url
        self._art_ids.move_to_end(key)
        while len(self._art_ids) > _ART_IDS_MAX:
            self._art_ids.popitem(last=False)
        return self.get_browse_image_url("image", key)

    async def async_get_browse_image(
        self, media_content_type: str, media_content_id: str, media_image_id: str | None = None
    ) -> tuple[bytes | None, str | None]:
        url = self._art_ids.get(str(media_content_id))
        if not url:
            return None, None
        # 0.3.23: made tile-sized once and kept (cache.py shrink_picture) - one
        # store per Control, shared by its rooms
        cache = getattr(self._client, "art_cache", None)
        if cache is None:
            cache = KeptCache(ART_KEEP, ART_KEEP, max_entries=3000, max_bytes=48 * 1024 * 1024)
            try:
                self._client.art_cache = cache
            except AttributeError:
                pass
        now = time.monotonic()
        hit = cache.get(url, now)
        if hit is not None:
            return hit[0]
        body, ctype = await self._client.fetch_art(url)
        if not body:
            return None, None
        small = await self.hass.async_add_executor_job(shrink_picture, body, ctype, BROWSE_ART_PX)
        cache.put(url, small, now, size=len(small[0]))
        return small

    def _running_row(self) -> dict | None:
        """The offered activity a play would land on now without another
        start (media.current_activity), or None."""
        current = current_activity(self._raw)
        return next((r for r in self._offered if int(r["id"]) == current), None)

    def _browse_item(self, result: dict) -> BrowseMedia:
        # ⚠ The TITLE only: Assist speaks it ("Playing Thriller", Michel
        # 2026-10-26). HA 2026.10.4's BrowseMedia has no subtitle; which
        # activity plays it shows in Browse media's folders and in the
        # room's "play" attribute.
        title = result["title"]
        return BrowseMedia(
            media_class=result["media_class"],
            media_content_id=content_id(result["activity"], result["play"]),
            media_content_type=result["content_type"],
            title=title,
            can_play=True,
            can_expand=False,
            thumbnail=self._thumbnail(result["image"]),
        )

    def _check_reads(self) -> None:
        problem = ownership_problem(self._data)
        if problem:
            raise HomeAssistantError(f"{self._unit_name}: {problem}")

    async def _cached(self, key: tuple, fetch):
        """A list read once per inventory round and per running activity
        (Michel 2026-10-26: favourites refreshed "on the inventory cycle and
        when the running activity changes"; "it took long"). Browse, each
        folder of it, the dashboard page and a search then share ONE read.
        A failed read is not kept."""
        stamp = (self.coordinator.inventory_round, current_activity(self._raw))
        hit = self._lists.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        value = await fetch()
        self._lists[key] = (stamp, value)
        return value

    async def _hearts(self, row: dict, query: str | None = None) -> list[dict]:
        """One activity's hearts as results. Error 7 ("plays nothing that has
        favourites") is no hearts, not a failure."""
        aid = int(row["id"])

        async def read():
            try:
                return await self._client.activity_favorites(aid)
            except ArtiControlError as err:
                if err.code == 7 and not isinstance(err, ArtiControlAuthError):
                    return []
                raise
        items = await self._cached(("hearts", aid), read)
        return favorite_results(items, aid, query)

    async def _job(self, job: str, row: dict, idle: bool, query: str, fallback: bool = False) -> list[dict]:
        if not fallback:
            return await self._job_once(job, row, idle, query)
        # Asking IS the feature detection (the Off-room extension): a refusal
        # (7, "plays no music here") means this Control does not have it -
        # remembered for its version - and the hearts are searched instead.
        try:
            found = await self._job_once(job, row, idle, query)
        except ArtiControlError as err:
            if err.code != 7 or isinstance(err, ArtiControlAuthError):
                raise
            self.coordinator.find_when_off = False
            return await self._hearts(row, query)
        self.coordinator.find_when_off = True
        return found

    async def _job_once(self, job: str, row: dict, idle: bool, query: str) -> list[dict]:
        aid = int(row["id"])
        player = player_of(row)
        if job == "favorites":
            return await self._hearts(row, query)
        # ⚠ The webapp's own requests (find-music.js / find-movies.js). While
        # nothing runs the activity is named (idle); while it runs, expect
        # makes the door refuse rather than answer for another player.
        if job == "albums":
            if player["service"] == "roon":
                request = {"action": "albums", "offset": 0, "sort": "name", "dir": "asc",
                           "category": "", "favorites": "", "q": search_word(query)}
            else:
                request = {"action": "library", "what": "albums", "sort": "name", "dir": "asc"}
            if idle:
                request["activity"] = aid
            else:
                request["expect"] = {"activity": aid, "service": player["service"]}
            if player["service"] == "roon":
                # The Control searches its own list (one word): small, not kept.
                data = await self._client.unit_find(self._scope, request)
            else:
                # Spotify's whole saved-album list: read once a round, filtered here.
                data = await self._cached(("library", aid), lambda: self._client.unit_find(self._scope, request))
        else:
            request = {"action": "titles"}
            if idle:
                request["activity"] = aid
            # The film library, as Find Movies reads it: once, then filtered here.
            data = await self._cached(("titles", aid), lambda: self._client.unit_find_movies(self._scope, request))
        if int(data.get("activity") or 0) != aid:
            # The door answered for another activity: not this one's to play.
            return []
        answer = data["answer"]
        if job == "films":
            titles = answer.get("titles")
            if answer.get("ok") is False or not isinstance(titles, list):
                raise ArtiControlError(str(answer.get("error") or "The films could not be read"))
            return film_results(titles, aid, player["movie_player"], query)
        items = answer.get("items")
        if not isinstance(items, list) or (not items and str(answer.get("error") or "")):
            raise ArtiControlError(str(answer.get("error") or "The albums could not be read"))
        # Roon: the server searched one word (search_word); every word is
        # checked here, as for Spotify's list.
        return album_results(items, aid, player["service"], query)

    async def _gather(self, calls) -> tuple[list[dict], list[str]]:
        outcomes = await asyncio.gather(*calls, return_exceptions=True)
        results: list[dict] = []
        failures: list[str] = []
        for outcome in outcomes:
            if isinstance(outcome, ArtiControlAuthError):
                raise HomeAssistantError(
                    f"{self._unit_name}: the Control no longer accepts our sign-in. "
                    "Sign in again under Settings > Devices & services > ArtiCONTROL."
                ) from outcome
            if isinstance(outcome, ArtiControlError):
                failures.append(str(outcome))
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                results.extend(outcome)
        if failures and len(failures) == len(outcomes):
            # Every source failed: say why - never "nothing found".
            raise HomeAssistantError(f"{self._unit_name}: {failures[0]}")
        return results, failures

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search what this unit's activities can play (HassMediaSearchAndPlay
        and the LLM's media tools): the best match first, because Assist
        plays the FIRST result."""
        merged = await self.search_results(str(query.search_query or ""), query.media_filter_classes, query.media_content_id)
        return SearchMedia(result=[self._browse_item(r) for r in merged])

    async def search_results(self, text: str, classes=None, within_id=None) -> list[dict]:
        """The merged search results ({"title", "subtitle" (the artist),
        "image", "activity", "play", "media_class", "content_type"}), for
        search_media and the dashboard's Browse page (find.py)."""
        self._check_reads()
        text = str(text or "").strip()
        rows = self._player_rows()
        within = parse_favorites_id(within_id)
        if within is not None:
            row = next((r for r in rows if int(r["id"]) == within), None)
            if row is None:
                raise HomeAssistantError(f"{self._unit_name}: that is not an activity of {self._unit_name}")
            jobs = [("favorites", row, False, False)]
        else:
            jobs = search_jobs(self._raw, self._offered, self.coordinator.find_when_off)
        results, _ = await self._gather([self._job(job, row, idle, text, fallback) for job, row, idle, fallback in jobs])
        alive = await self._art_alive(results)
        results = [r for r in results if id(r) in alive]
        # ⚠ ACTIVE-ACTIVITY AWARE (Michel 2026-10-26): what the running
        # activity's player can play is played there, without starting
        # another source; the rest by the activity that owns it.
        running = self._running_row()
        results = retarget(results, running, rows)
        return merge(results, text, prefer=int(running["id"]) if running else 0,
                     order=[int(r["id"]) for r in rows], classes=classes)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Start the activity that owns the item, with that item as its play
        (/activity start {"play"}). Refused - nothing sent - when the play is
        not ours, or not this activity's own player's."""
        parsed = parse_content_id(media_id)
        if parsed is None:
            raise HomeAssistantError(
                f"{self._unit_name}: ArtiCONTROL cannot play this - search or browse for it first")
        activity_id, play = parsed
        activity_id, state = await play_on_unit(self._data, self._key, self._unit_name, activity_id, play)
        title = str(play.get("title") or "")
        row = next((r for r in self._offered if int(r["id"]) == activity_id), None)
        self._last_play = {"title": title, "activity_id": activity_id,
                           "activity_name": str((row or {}).get("name") or ""), "state": state}
        self.async_write_ha_state()
        if state in NOT_PLAYED:
            raise HomeAssistantError(
                f"{self._unit_name}: the Control did not play {title or 'it'} ({state})")

    async def _tree(self) -> dict:
        """media.browse_tree over the unit's hearts, read NOW (no cache: a
        heart added or removed shows on the next look), dead covers left out."""
        rows = self._player_rows()
        per_row = await asyncio.gather(*(self._hearts(row) for row in rows), return_exceptions=True)
        for outcome in per_row:
            if isinstance(outcome, ArtiControlAuthError):
                raise HomeAssistantError(
                    f"{self._unit_name}: the Control no longer accepts our sign-in. "
                    "Sign in again under Settings > Devices & services > ArtiCONTROL.") from outcome
            if isinstance(outcome, BaseException) and not isinstance(outcome, ArtiControlError):
                raise outcome
        read = [(row, out) for row, out in zip(rows, per_row) if not isinstance(out, BaseException)]
        if rows and not read:
            raise HomeAssistantError(f"{self._unit_name}: {per_row[0]}")
        alive = await self._art_alive([r for _, res in read for r in res])
        read = [(row, [r for r in res if id(r) in alive]) for row, res in read]
        running = self._running_row()
        if running is not None and not any(int(r["id"]) == int(running["id"]) for r in rows):
            running = None   # it plays nothing a play can name: as when off
        return browse_tree(read, running, rows)

    async def _art_alive(self, results: list[dict]) -> set[int]:
        """The results whose cover can be shown (Michel 2026-10-26, live: "NO
        artwork for Oxygene 3"). A service's own https address can die - a
        Deezer cover a Sonos or HEOS favourite hands out answers 404, or ends on
        Deezer's empty picture (cover_repair.php). Checked here at most
        _ART_CHECKS a look, each remembered a week (an hour when it did not
        answer, which is shown as it is). The Control's own art route is the
        Control's to serve. -> ids of the results to keep."""
        verdicts = self._client.art_verdicts
        now = time.monotonic()
        todo = []
        for r in results:
            url = r["image"]
            if url.startswith("https://") and (url not in verdicts or verdicts[url][1] < now):
                if url not in todo:
                    todo.append(url)
        if todo:
            checks = await asyncio.gather(*(self._client.art_alive(u) for u in todo[:_ART_CHECKS]))
            for url, ok in zip(todo, checks):
                verdicts[url] = (ok, now + (_ART_KEEP if ok is not None else _ART_RETRY))
        return {id(r) for r in results if (verdicts.get(r["image"]) or (None,))[0] is not False}

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """Service aware (Michel 2026-10-26): while an activity runs, the root
        IS its content - its favourites and what else its player plays - with
        the other sources in one "Other sources" folder; while off, the
        unit's playable activities, each opening its favourites. One entry per
        item; artwork on every one. The root searches."""
        self._check_reads()
        tree = await self._tree()
        folder = parse_favorites_id(media_content_id)
        if folder is not None:
            for row, items in tree["folders"]:
                if int(row["id"]) == folder:
                    return self._folder(favorites_id(folder), str(row.get("name") or ""),
                                        [self._browse_item(r) for r in items])
            row = next((r for r in self._player_rows() if int(r["id"]) == folder), None)
            if row is None:
                raise HomeAssistantError(f"{self._unit_name}: that is not an activity of {self._unit_name}")
            # Its items are at the root (the running one, or the only source).
            return self._folder(favorites_id(folder), str(row.get("name") or ""),
                                [self._browse_item(r) for r in tree["root"] if r["activity"] == folder])
        folders = [
            BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=favorites_id(int(row["id"])),
                media_content_type="favorites",
                title=str(row.get("name") or ""),
                can_play=False,
                can_expand=True,
                thumbnail=self._thumbnail(items[0]["image"]),
            )
            for row, items in tree["folders"]
        ]
        if media_content_id == _OTHERS:
            return self._folder(_OTHERS, "Other sources", folders)
        children = [self._browse_item(r) for r in tree["root"]]
        if tree["others"]:
            if folders:
                children.append(BrowseMedia(
                    media_class=MediaClass.DIRECTORY, media_content_id=_OTHERS, media_content_type="favorites",
                    title="Other sources", can_play=False, can_expand=True, thumbnail=folders[0].thumbnail))
        else:
            children.extend(folders)
        root = self._folder("", self._unit_name, children)
        root.can_search = True
        return root

    @staticmethod
    def _folder(content: str, title: str, children: list[BrowseMedia]) -> BrowseMedia:
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=content,
            media_content_type="favorites",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )
