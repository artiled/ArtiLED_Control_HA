# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Kept, then fresh (0.3.23) - the webapp's own pattern for its reads.

Michel 2026-10-26: "a lot of things take long to load in HA. Can we do some
caching?" MEASURED on the dev Control (on the box, read actions only): a
Browse list answers in 70-400 ms (Recent and Albums ~400 ms, 47-96 KB), a
search 3-7 s (Roon 3.3-5.8 s, Sonos 6.4-7.5 s), a cover 20 ms but 76 KB at
full size; and every tab, every Back and every page view asked again.

So a READ answer is kept (per Control, unit, activity and exact request):
  - younger than `fresh` seconds: served as it is, nothing asked;
  - older, up to `keep` seconds: served AT ONCE with its age ("kept"), and
    the page asks again with fresh=true in the background (find-music.js
    svcAccounts: "the kept answer first, then fresh");
  - older than `keep`: gone, asked as if never read.
A write is never kept and DROPS what it can change (invalidate). A failed
read is never kept. The store is bounded in entries and in bytes (least
recently used out first), and lives in memory only - a restart starts
empty, as the webapp's page does.

Pure: no Home Assistant import; the clock is passed in (tests).
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Callable


def size_of(value: Any) -> int:
    """Roughly what a value costs to keep (its JSON length; bytes as is)."""
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    try:
        return len(json.dumps(value, separators=(",", ":"), default=str))
    except (TypeError, ValueError):
        return 1024


class KeptCache:
    """A bounded LRU of (stored_at, value, size) with kept-then-fresh ages."""

    def __init__(self, fresh: float, keep: float, max_entries: int = 400, max_bytes: int = 12 * 1024 * 1024) -> None:
        self.fresh = float(fresh)
        self.keep = float(keep)
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._items: OrderedDict[Any, tuple[float, Any, int]] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.stale_hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def bytes(self) -> int:
        return self._bytes

    def get(self, key, now: float) -> tuple[Any, float] | None:
        """-> (value, age in seconds) while kept, else None (and it is dropped)."""
        item = self._items.get(key)
        if item is None:
            self.misses += 1
            return None
        stored, value, _ = item
        age = max(0.0, now - stored)
        if age > self.keep:
            self._drop(key)
            self.misses += 1
            return None
        self._items.move_to_end(key)
        if age > self.fresh:
            self.stale_hits += 1
        else:
            self.hits += 1
        return value, age

    def is_fresh(self, age: float) -> bool:
        return age <= self.fresh

    def put(self, key, value, now: float, size: int | None = None) -> None:
        size = size_of(value) if size is None else int(size)
        if size > self.max_bytes:
            self._drop(key)
            return
        self._drop(key)
        self._items[key] = (now, value, size)
        self._bytes += size
        while self._items and (len(self._items) > self.max_entries or self._bytes > self.max_bytes):
            old = next(iter(self._items))
            self._drop(old)

    def invalidate(self, match: Callable[[Any], bool]) -> int:
        """Drops every entry whose key matches; -> how many."""
        gone = [k for k in self._items if match(k)]
        for k in gone:
            self._drop(k)
        return len(gone)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def _drop(self, key) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self._bytes -= item[2]


def request_key(request: dict) -> str:
    """The request as a stable key: its own fields, sorted; the door's
    routing fields (expect / activity, set by the integration) left out -
    the activity is a separate part of the cache key."""
    own = {k: v for k, v in (request or {}).items() if k not in ("expect", "activity", "fresh_read")}
    return json.dumps(own, sort_keys=True, separators=(",", ":"), default=str)


# Reads whose answer follows what plays: a play through the door drops them.
AFTER_PLAY = frozenset({"recent", "song_list"})


# A Browse tile is 104-160 px wide (x2 on a retina screen); the Control serves
# its covers at full size (MEASURED 76 KB each, 100 to a page = 7.6 MB through
# Home Assistant). So a cover for a tile or a row is made 320 px once, kept,
# and served from memory: ~15 KB, no second fetch from the Control.
BROWSE_ART_PX = 320
ART_KEEP = 24 * 3600


def shrink_picture(body: bytes, content_type: str | None, max_px: int = BROWSE_ART_PX) -> tuple[bytes, str]:
    """The picture at most max_px on its longest side, as a JPEG; the original
    when it is small enough already, is not a picture Pillow reads, or Pillow
    is not there (run in an executor: it decodes)."""
    ctype = content_type or "image/jpeg"
    try:
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return body, ctype
    try:
        with Image.open(io.BytesIO(body)) as img:
            if max(img.size) <= max_px:
                return body, ctype
            img.thumbnail((max_px, max_px))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=82, optimize=True)
            small = out.getvalue()
    except Exception:  # noqa: BLE001 - any picture Pillow cannot read is served as it came
        return body, ctype
    return (small, "image/jpeg") if len(small) < len(body) else (body, ctype)


# 0.3.24: the stores live per CONTROL (its ident) for the life of Home
# Assistant, not per config entry: adding a voice favourite reloads the entry,
# and a cache that went with the reload made the very next look slow again
# (Michel 2026-10-26: "When I click one everything disappears ... and comes
# back but takes long").
_STORES: dict = {}


def store(ident, name: str, factory) -> KeptCache:
    """The named KeptCache of one Control, made once."""
    key = (str(ident), name)
    cache = _STORES.get(key)
    if cache is None:
        cache = _STORES[key] = factory()
    return cache
