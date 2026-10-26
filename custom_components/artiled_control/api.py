# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Async client for the ArtiLED Control API (HTTP POST, token + json data).

A copy of the Hub integration's client, with three differences:

- NO GLOBAL LOCK. The Hub serialises because a Hub set command blocks reads for
  15-25 s. Control's /activity answers at once, and a /button can take up to
  15 s - a poll must never queue behind one. Ordering of commands is per UNIT,
  in media_player.py; polls run free.
- Timeouts: 10 s for a poll, 15 s for a command.
- Nothing that carries the token is ever logged or put in an exception: not the
  POST body, and not a URL with token= in it. Errors name the endpoint only.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import aiohttp

POLL_TIMEOUT = 10
COMMAND_TIMEOUT = 15
ART_TIMEOUT = 10
ART_MAX_BYTES = 8 * 1024 * 1024
ART_CHECK_TIMEOUT = 4
# Deezer's empty picture: a dead Deezer cover redirects to it (cover_repair.php).
_DEEZER_EMPTY = "/d41d8cd98f00b204e9800998ecf8427e/"

# The art route that takes the API token, as the UC driver uses it
# (artiled_uc_driver.browse_art). The ONLY path the token is ever added to.
ART_ROUTE = "/api/unit_player_art?"

_TOKEN_RE = re.compile(r"(token=)[^&\s'\"]*", re.IGNORECASE)


class ArtiControlError(Exception):
    """The Control returned an error or is unreachable. `code` is the API's
    error number (0 when it never answered)."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class ArtiControlAuthError(ArtiControlError):
    """The token (or the login) was rejected."""


def redact(text) -> str:
    """Any token=... in a text, replaced. For log lines and diagnostics."""
    return _TOKEN_RE.sub(r"\1REDACTED", str(text))


def api_version(value) -> tuple[int, int] | None:
    """/version "api" ("2.00") -> (2, 0). None when absent or unreadable: an
    older Control, served the old way. Compared as two integers, as the API
    document says."""
    major, _, minor = str(value or "").strip().partition(".")
    if not major.isdigit() or not minor.isdigit():
        return None
    return int(major), int(minor)


def api_at_least(value, wanted: tuple[int, int]) -> bool:
    parsed = value if isinstance(value, tuple) else api_version(value)
    return parsed is not None and parsed >= wanted


# The Activity Service answers a key it could not place in WORDS, not with an
# error. Port of app_remote_key_failed (API app/includes/remote.php): the
# failure forms are the service's own (transport_delivered() in
# artiled_activity.py) plus the webapp's. Anything else a device says, and
# silence, counts as delivered - as it does for the Remote 3.
_FAILED_EXACT = ("0", "error", "auth failed", "no command", "no result", "no routing", "no volume command",
                 "unknown command", "device not found", "device disabled", "device type not found",
                 "no ip address", "no mac address", "no protocol", "unknown protocol", "invalid command")
_FAILED_PREFIX = ("unknown command", "no smartthings", "telnet error", "http error", "rs232 error",
                  "binary tcp error", "binary rs232 error", "websocket error", "barco error", "sony error",
                  "sony discover", "lg error", "ir error")
_FAILED_WORD = re.compile(r"\berror:", re.IGNORECASE)


def key_failed(reply) -> bool:
    """True when a /button reply means the key reached nothing."""
    if not isinstance(reply, dict) or not reply.get("success"):
        return True
    result = (reply.get("data") or {}).get("result") if isinstance(reply.get("data"), dict) else None
    text = result.strip().lower() if isinstance(result, str) else ""
    if text in _FAILED_EXACT:
        return True
    if any(text.startswith(prefix) for prefix in _FAILED_PREFIX):
        return True
    return bool(_FAILED_WORD.search(text))


def start_accepted(data, activity_id) -> bool:
    """The documented acceptance of an id start: {"activity": <that id>,
    "action": "start", ...}. The service's own word ("start") is read when
    present but not required - a Control before API 2.00 does not send it."""
    if not isinstance(data, dict) or data.get("action") != "start":
        return False
    try:
        return int(data.get("activity")) == int(activity_id)
    except (TypeError, ValueError):
        return False


def art_request(host: str, token: str, url) -> tuple[str, bool] | None:
    """(address to fetch, whether it carries the token) for an image_url.

    ⚠ CREDENTIALS GO ONLY TO THIS CONTROL'S OWN ART ROUTE. The token is added
    to a relative /api/unit_player_art?... path and nothing else: never to an
    outside URL (a Roon bridge, an https poster), never to another relative
    path. Anything else is fetched exactly as given, without the token.
    """
    url = str(url or "")
    if url.startswith(ART_ROUTE):
        return f"http://{host}{url}&token={token}", True
    if url.startswith("/") and not url.startswith("//"):
        return f"http://{host}{url}", False
    if url.startswith(("http://", "https://")):
        return url, False
    return None


class ArtiControlClient:
    """Minimal async Control API client. The Control stays authoritative -
    this client never caches state; the coordinator owns polling."""

    def __init__(self, session: aiohttp.ClientSession, host: str, token: str, generation: int = 0) -> None:
        self._session = session
        self.host = host.strip()
        self._base = f"http://{self.host}/api"
        self._token = token
        # Which host + token snapshot this client belongs to (identity.Fence).
        self.generation = generation
        # (major, minor) from /version "api", None for an older Control.
        self.api: tuple[int, int] | None = None
        # Outside cover verdicts: url -> (alive True / False / None, until).
        self.art_verdicts: dict[str, tuple] = {}

    async def _call(self, endpoint: str, data: dict[str, Any] | None = None,
                    timeout: int = POLL_TIMEOUT) -> Any:
        # ⚠ _post names the endpoint and the error's TYPE only. aiohttp's own
        # text can carry the request, and the request carries the token.
        payload = {"token": self._token, "data": json.dumps(data or {})}
        body = await self._post(endpoint, payload, timeout)
        return body.get("data") if body.get("data") not in (None, "") else {}

    async def _collection(self, endpoint: str, name: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """A list answer, read STRICTLY (Mycroft 8662, HA-S3).

        ⚠ A success with no data, null data, or data without this list is NOT
        an empty list. Read as one, a malformed reply would certify that the
        Control has no rooms or no activities - and after the grace hour delete
        the customer's entities. Only an explicit list counts; an explicit []
        is a real, valid empty answer.
        """
        payload = {"token": self._token, "data": json.dumps(data or {})}
        body = await self._post(endpoint, payload, POLL_TIMEOUT)
        answer = body.get("data")
        if not isinstance(answer, dict) or not isinstance(answer.get(name), list):
            raise ArtiControlError(f"Malformed {endpoint} answer")
        return answer[name]

    async def _post(self, endpoint: str, payload: dict, timeout: int) -> dict:
        """POST and check the envelope; returns the whole body."""
        try:
            resp = await self._session.post(
                f"{self._base}/{endpoint}", data=payload, timeout=aiohttp.ClientTimeout(total=timeout)
            )
            body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ArtiControlError(f"Control unreachable ({endpoint}: {type(err).__name__})") from None
        if not isinstance(body, dict) or int(body.get("success", 0) or 0) != 1:
            error = int(body.get("error", 0) or 0) if isinstance(body, dict) else 0
            if error == 2:
                raise ArtiControlAuthError("Token rejected by the Control", 2)
            text = str(body.get("error_text") or "Control error") if isinstance(body, dict) else "Control error"
            raise ArtiControlError(redact(text), error)
        return body

    @staticmethod
    async def login(session: aiohttp.ClientSession, host: str, username: str, password: str) -> str:
        """Exchange the customer's Control login for the Control's API token.

        ⚠ THE TOKEN IS STORED, THE PASSWORD IS NOT. /api/login answers the
        box-wide stable token (get_token()), as on the Hub, so nothing ever
        needs the password again and a token that stops working is a re-auth.
        """
        try:
            resp = await session.post(
                f"http://{host.strip()}/api/login",
                data={"data": json.dumps({"username": username, "password": password})},
                timeout=aiohttp.ClientTimeout(total=20),
            )
            body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ArtiControlError(f"Control unreachable (login: {type(err).__name__})") from None
        if not isinstance(body, dict) or int(body.get("success", 0) or 0) != 1:
            raise ArtiControlAuthError("Login failed", 1)
        token = (body.get("data") or {}).get("token") if isinstance(body.get("data"), dict) else None
        if not token:
            raise ArtiControlError("The Control accepted the login but returned no token")
        return str(token)

    async def alive(self) -> dict[str, Any]:
        """Unauthenticated reachability probe - returns name + ident."""
        try:
            resp = await self._session.get(f"{self._base}/alive", timeout=aiohttp.ClientTimeout(total=POLL_TIMEOUT))
            body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ArtiControlError(f"Control unreachable (alive: {type(err).__name__})") from None
        if not isinstance(body, dict) or int(body.get("success", 0) or 0) != 1:
            raise ArtiControlError("Unexpected alive response")
        return body.get("data") or {}

    async def version(self) -> dict[str, str]:
        """{"version": product release, "api": API definition version or ""}."""
        data = await self._call("version")
        return {"version": str(data.get("version", "")), "api": str(data.get("api", "") or "")}

    async def rooms(self) -> list[dict[str, Any]]:
        return await self._collection("rooms", "rooms")

    async def zones(self) -> list[dict[str, Any]]:
        return await self._collection("zones", "zones")

    async def activities(self, scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """{} = every unit's (API 2.00), {"zone": id} or {"room": id} = one unit's."""
        return await self._collection("activities", "activities", scope or {})

    async def commands(self, device_id: int) -> list[dict[str, Any]]:
        """/commands: a device's commands ({"id","name"}), read only."""
        return await self._collection("commands", "commands", {"device": int(device_id)})

    async def command(self, device_id: int, command: str) -> None:
        """/command: one of the device's /commands ids."""
        await self._call("command", {"device": int(device_id), "command": str(command)}, timeout=COMMAND_TIMEOUT)

    async def devices(self) -> list[dict[str, Any]]:
        """/devices {}: every device, with the Control's own down / deep_standby."""
        return await self._collection("devices", "devices", {})

    async def start_activity(self, activity_id: int) -> dict[str, Any]:
        """/activity start. ⚠ A start is not proof it ran: the answer is the
        Activity Service's word (or "accepted"), so the caller looks again."""
        data = await self._call("activity", {"action": "start", "activity": int(activity_id)}, timeout=COMMAND_TIMEOUT)
        if not start_accepted(data, activity_id):
            # ⚠ Success with an answer that is not the documented start
            # acceptance (Mycroft 8662, HA-S6): the outcome is UNKNOWN. Said so,
            # never reported as started - and never sent again.
            raise ArtiControlError("The Control's answer to the start could not be read - "
                                   "the activity may or may not have started", 7)
        return data

    async def start_with_play(self, activity_id: int, play: dict) -> dict[str, Any]:
        """/activity start with {"play": ...} (#107): this item, for this start
        only. ⚠ The answer's play state is what the player's own path said
        (sent, attached, accepted ...) - never proof that it plays. A success
        without the documented answer is an UNKNOWN outcome, said so, and never
        sent again."""
        data = await self._call("activity", {"action": "start", "activity": int(activity_id), "play": play},
                                timeout=COMMAND_TIMEOUT)
        if not start_accepted(data, activity_id) or not isinstance(data.get("play"), dict):
            raise ArtiControlError("The Control's answer to the play could not be read - "
                                   "it may or may not have started", 7)
        return data

    async def unit_find(self, scope: dict[str, int], request: dict[str, Any]) -> dict[str, Any]:
        """/unit_find: the webapp's own Find Music request for one unit."""
        return await self._door("unit_find", scope, request)

    async def unit_find_movies(self, scope: dict[str, int], request: dict[str, Any]) -> dict[str, Any]:
        """/unit_find_movies: the webapp's own Find Movies request for one unit."""
        return await self._door("unit_find_movies", scope, request)

    async def _door(self, endpoint: str, scope: dict[str, int], request: dict[str, Any]) -> dict[str, Any]:
        data = await self._call(endpoint, {**request, **scope})
        if not isinstance(data, dict) or not isinstance(data.get("answer"), dict):
            raise ArtiControlError(f"Malformed {endpoint} answer")
        return data

    async def unit_player(self, scope: dict[str, int], request: dict[str, Any]) -> dict[str, Any]:
        """/unit_player: the running activity's music player - its state, or
        a seek, shuffle or repeat (the webapp remote's playback.php door)."""
        data = await self._call("unit_player", {**request, **scope}, timeout=COMMAND_TIMEOUT)
        if not isinstance(data, dict):
            raise ArtiControlError("Malformed unit_player answer")
        return data

    async def activity_favorites(self, activity_id: int) -> list[dict[str, Any]]:
        """/activity_favorites: the hearts of one activity's own player, each
        with the exact play /activity accepts. Read only, also while it is not
        running. Read strictly: an explicit [] is "no hearts", a malformed
        answer is an error - never an empty list."""
        return await self._collection("activity_favorites", "items", {"activity": int(activity_id)})

    async def button(self, legacy: int, key: str) -> str:
        """One key to a unit's routing. Raises with the Control's own words when
        the reply means the key reached nothing (key_failed).

        `legacy` is the unit's one number (units.legacy_id): the zone id, or
        minus the room id. Sent as "zone" because every Control takes that -
        {"room": id} only arrived with card #684 - and it is exactly what the
        webapp slider and the UC driver send.
        """
        payload = {"token": self._token, "data": json.dumps({"zone": int(legacy), "button": key})}
        data = (await self._post("button", payload, COMMAND_TIMEOUT)).get("data")
        if not isinstance(data, dict) or not isinstance(data.get("result"), str):
            # ⚠ A success without a result STRING is not the service's empty
            # answer - it is an answer we cannot read (Mycroft 8662, HA-S6).
            # An explicit "" stays a valid, delivered result.
            raise ArtiControlError("The Control's answer to the key could not be read - "
                                   "it may or may not have reached the device", 7)
        reply = {"success": 1, "data": data}
        result = data["result"]
        if key_failed(reply):
            raise ArtiControlError(redact(result or "The key reached nothing"), 7)
        return result

    async def art_alive(self, url: str) -> bool | None:
        """Does an outside https cover load? False: 404 / 410, or it ends on
        Deezer's empty picture (the Control's cover_repair rule); True: 200;
        None: no answer - not known. Never with the token (outside address)."""
        if not str(url).startswith("https://"):
            return None
        if _DEEZER_EMPTY in url:
            return False
        try:
            # HEAD, as the Control's own check: the picture itself is not fetched.
            resp = await self._session.head(url, timeout=aiohttp.ClientTimeout(total=ART_CHECK_TIMEOUT),
                                            allow_redirects=True)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None
        if resp.status in (404, 410):
            return False
        if _DEEZER_EMPTY in str(getattr(resp, "url", "") or ""):
            return False
        return True if resp.status == 200 else None

    async def fetch_art(self, url) -> tuple[bytes | None, str | None]:
        """A Now Playing picture, fetched server-side.

        ⚠ Home Assistant's own fetch logs the image URL when it fails, and ours
        can carry token=. So the fetch is done here, it never follows a redirect
        while it carries the token, and a failure says nothing about the URL.
        """
        request = art_request(self.host, self._token, url)
        if request is None:
            return None, None
        address, with_token = request
        try:
            resp = await self._session.get(
                address,
                timeout=aiohttp.ClientTimeout(total=ART_TIMEOUT),
                allow_redirects=not with_token,
            )
            if resp.status != 200:
                return None, None
            body = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None, None
        if not body or len(body) > ART_MAX_BYTES:
            return None, None
        return body, resp.headers.get("Content-Type", "image/jpeg")
