# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Font Awesome icon paths, fetched from the customer's own Control at runtime.

A COPY of the ArtiLED Hub integration's icons.py (plan 3.2, "Icons"); only the
names differ. The Control stores its room, zone and activity icons as Font
Awesome **Pro** classes ("fa-solid fa-tv") and Home Assistant ships MDI, so the
SVG paths have to come from somewhere.

⚠ NEVER A BUNDLED ICON FILE. Font Awesome's Pro licence forbids making
standalone copies of Pro icons available to non-licensees, and a published
integration would be exactly that - the Hub learned this when its repository
went public and had to be rebuilt. Instead the paths are read from the Control
the integration is already talking to: every Control carries the licensed Font
Awesome Pro tree in its own web root (/fontawesome/), so the icons never leave
the licensed context.

⚠ The BROWSER cannot do this itself: the box serves its web root without CORS
headers (measured on the Hub, 2026-08-17), so it has to be read server-side.

⚠ No token goes with these requests: they are the web root's static files,
never the API.

Only the icons this Control actually uses (plus the few glyphs the dashboard's
own keys draw, const.CONTROL_GLYPHS) are fetched - a few dozen, not 3,241.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import aiohttp

from .const import ICON_CACHE_DIR, ICON_CACHE_FILE

# One icon, e.g. /fontawesome/svgs/solid/couch.svg — about 650 bytes.
FA_SOLID = "/fontawesome/svgs/solid/{name}.svg"
# The light style (the webapp remote's and the Touch's shortcut glyphs:
# "fa-light fa-folder-open"), cached as "light-<name>".
FA_STYLE = "/fontawesome/svgs/{style}/{name}.svg"
LIGHT = "light-"
# The alias map. 1.3 MB, against 31 MB for icons.json which carries every path
# as well; we only ever need the names, so read the smaller file.
FA_ALIASES = "/fontawesome/metadata/icons.yml"

_VIEWBOX = re.compile(r'viewBox="([^"]+)"')
_PATH_D = re.compile(r'\sd="([^"]+)"')
# icons.yml is regular enough to scan by line, which avoids parsing 1.3 MB of
# YAML to read two fields:
#     bed-front:
#       aliases:
#         names:
#           - bed-alt
_CANONICAL = re.compile(r"^([a-z0-9][a-z0-9-]*):$")
_ALIAS_NAME = re.compile(r"^\s+- ([a-z0-9][a-z0-9-]*)$")


def fa_name(icon: str) -> str:
    """"fas fa-couch" / "fa-solid fa-couch" / "couch" -> "couch".

    Mirrors fa_name() in the frontend: the boxes have stored several generations of
    class string, so take the LAST fa- token rather than assuming a position.
    """
    parts = [p for p in str(icon or "").strip().split() if p]
    token = next((p for p in reversed(parts) if p.startswith("fa-")), None)
    if token is None:
        token = parts[-1] if parts else ""
    return token[3:] if token.startswith("fa-") else token


def icon_key(icon: str) -> str:
    """The cache key of a class: its name ("couch"), or "light-<name>" for the
    light style. Mirrors fa_key() in the frontend."""
    name = fa_name(icon)
    parts = str(icon or "").split()
    return f"{LIGHT}{name}" if name and ("fa-light" in parts or "fal" in parts) else name


def _parse_svg(body: str) -> list[str] | None:
    """An FA solid SVG -> [path d, viewBox]. None if it is not one."""
    box = _VIEWBOX.search(body)
    d = _PATH_D.search(body)
    if not box or not d:
        return None
    return [d.group(1), box.group(1)]


class ControlIcons:
    """Reads Font Awesome paths from a Control and caches them on disk.

    The cache lives in the CONFIG directory, not in the integration directory:
    a HACS update replaces the integration wholesale, and a cache that vanished
    on every upgrade would refetch for nothing.
    """

    def __init__(self, hass, session: aiohttp.ClientSession, origin: str) -> None:
        self._hass = hass
        self._session = session
        self._origin = origin.rstrip("/")
        self._aliases: dict[str, str] | None = None
        self._lock = asyncio.Lock()

    @property
    def dir(self) -> Path:
        return Path(self._hass.config.path(ICON_CACHE_DIR))

    @property
    def file(self) -> Path:
        return self.dir / ICON_CACHE_FILE

    async def _get(self, path: str, timeout: int = 30) -> str | None:
        try:
            resp = await self._session.get(
                f"{self._origin}{path}", timeout=aiohttp.ClientTimeout(total=timeout)
            )
            if resp.status != 200:
                return None
            return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def _alias_map(self) -> dict[str, str]:
        """alias -> canonical, read once per Control.

        "bed-alt" was a live zone icon on the dev Hub and has no SVG of its own;
        it resolves only through icons.yml's aliases.names. Getting this wrong
        silently drops icons that ARE in use, which is worse than dropping ones
        that are not.
        """
        if self._aliases is not None:
            return self._aliases
        body = await self._get(FA_ALIASES, timeout=60)
        aliases: dict[str, str] = {}
        if body:
            canonical = ""
            in_names = False
            for line in body.splitlines():
                top = _CANONICAL.match(line)
                if top:
                    canonical, in_names = top.group(1), False
                    continue
                stripped = line.strip()
                if stripped == "names:":
                    in_names = True
                    continue
                if in_names:
                    alias = _ALIAS_NAME.match(line)
                    if alias:
                        aliases.setdefault(alias.group(1), canonical)
                        continue
                    in_names = False
        self._aliases = aliases
        return aliases

    async def _one(self, name: str) -> list[str] | None:
        if name.startswith(LIGHT):
            body = await self._get(FA_STYLE.format(style="light", name=name[len(LIGHT):]))
            return _parse_svg(body) if body is not None else None
        body = await self._get(FA_SOLID.format(name=name))
        if body is None:
            canonical = (await self._alias_map()).get(name)
            if not canonical or canonical == name:
                return None
            body = await self._get(FA_SOLID.format(name=canonical))
            if body is None:
                return None
        return _parse_svg(body)

    async def async_refresh(self, icons: set[str]) -> dict[str, list[str]]:
        """Fetch any icon we do not already hold, and rewrite the cache file.

        Never raises: an icon is cosmetic, and a Control that cannot serve one must
        not take the dashboard down with it.
        """
        async with self._lock:
            wanted = {icon_key(i) for i in icons if icon_key(i)}
            have = await self._hass.async_add_executor_job(self._read)
            missing = sorted(wanted - set(have))
            if not missing:
                return have
            results = await asyncio.gather(
                *(self._one(name) for name in missing), return_exceptions=True
            )
            for name, entry in zip(missing, results):
                if isinstance(entry, list):
                    have[name] = entry
            await self._hass.async_add_executor_job(self._write, have)
            return have

    def _read(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            tmp.replace(self.file)
        except OSError:
            pass
