#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Tests for kept-then-fresh (0.3.23, cache.py): the read cache, its use by
the Browse websocket commands, and the tile-sized cover cache.

Run:  python3 tests/test_cache.py     (exit 0 = all pass)
"""
from __future__ import annotations

import asyncio
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()
cache_mod = S.load_pure("cache")


def pure() -> None:
    print("the store (cache.py KeptCache)")
    c = cache_mod.KeptCache(fresh=60, keep=600, max_entries=3, max_bytes=10_000)
    check("an unknown key is a miss", c.get("a", 0) is None and c.misses == 1)
    c.put("a", {"x": 1}, 100)
    check("within `fresh`: the value, age 10, fresh", c.get("a", 110) == ({"x": 1}, 10) and c.is_fresh(10))
    got = c.get("a", 400)
    check("after `fresh`, within `keep`: still served, with its age (kept)", got == ({"x": 1}, 300) and not c.is_fresh(300))
    check("after `keep`: gone (asked as if never read)", c.get("a", 701) is None and len(c) == 0)
    for k in "bcde":
        c.put(k, k, 0)
    check("at most max_entries: the least recently used goes first", len(c) == 3 and c.get("b", 1) is None and c.get("e", 1) == ("e", 1))
    c.clear()
    c.put("big", "x" * 20_000, 0)
    check("a value larger than the whole store is not kept", c.get("big", 1) is None and c.bytes == 0)
    c.put("p", "y" * 6000, 0)
    c.put("q", "z" * 6000, 0)
    check("bounded in bytes: the older one goes", c.get("p", 1) is None and c.get("q", 1) is not None and c.bytes <= 10_000)
    c.put(("u1", "recent"), 1, 0)
    c.put(("u2", "recent"), 2, 0)
    check("invalidate drops exactly what matches", c.invalidate(lambda k: k[0] == "u1") == 1 and c.get(("u2", "recent"), 1) == (2, 1))
    rk = cache_mod.request_key
    check("request_key: the door's routing fields are not part of it",
          rk({"action": "recent", "expect": {"activity": 1}}) == rk({"activity": 9, "action": "recent"}))
    check("request_key: different requests, different keys", rk({"action": "browse", "offset": 0}) != rk({"action": "browse", "offset": 100}))

    print("\ncovers made tile-sized (shrink_picture)")
    from PIL import Image
    big = io.BytesIO()
    Image.new("RGB", (1200, 1200), (180, 40, 40)).save(big, format="PNG")
    body = big.getvalue()
    small, ctype = cache_mod.shrink_picture(body, "image/png", 320)
    with Image.open(io.BytesIO(small)) as img:
        size = img.size
    check("a 1200 px cover is served at 320 px as a JPEG, smaller", size == (320, 320) and ctype == "image/jpeg" and len(small) < len(body))
    tiny = io.BytesIO()
    Image.new("RGB", (100, 100)).save(tiny, format="PNG")
    check("a small one as it came", cache_mod.shrink_picture(tiny.getvalue(), "image/png", 320) == (tiny.getvalue(), "image/png"))
    check("not a picture: as it came", cache_mod.shrink_picture(b"\xff\xd8JPEG", "image/jpeg", 320) == (b"\xff\xd8JPEG", "image/jpeg"))


async def through_ha() -> None:
    control = S.house(S.FakeControl())
    rows = {a["id"]: a for a in control.activities}
    rows[120].update(music_service="roon", music_where="Secondary", movie_player=0)
    for a in control.activities:
        a.setdefault("music_service", "")
        a.setdefault("music_where", "")
        a.setdefault("movie_player", 0)
    control.find_answers[("roon", "recent")] = {"sections": [{"title": "Recently played", "items": [{"title": "Thriller"}]}], "error": ""}
    control.find_answers[("roon", "albums")] = {"items": [{"title": "Bad"}], "count": 1, "next": 1}
    hass, entry, control = await S.setup_integration(control)
    find = sys.modules["artiled_control.find"]
    clock = {"now": 1000.0}
    find._now = lambda: clock["now"]

    async def ask(request, fresh=False, entity="media_player.secondary"):
        conn = S.FakeConnection()
        msg = {"id": 1, "type": "artiled_control/find", "entity_id": entity, "request": request}
        if fresh:
            msg["fresh"] = True
        await hass.ws["artiled_control/find"](hass, conn, msg)
        return conn.results.get(1), conn.errors.get(1)

    def sent(action=None):
        return [d for e, d in control.requests if e == "unit_find" and (action is None or d.get("action") == action)]

    print("\nBrowse reads through Home Assistant (artiled_control/find)")
    control.requests.clear()
    r1, _ = await ask({"action": "recent"})
    r2, _ = await ask({"action": "recent"})
    check("the same read twice within a minute: ONE /unit_find, the same answer, not marked kept",
          len(sent("recent")) == 1 and r1["answer"] == r2["answer"] and r2["kept"] == 0, str(sent()))
    await ask({"action": "albums", "offset": 0})
    await ask({"action": "albums", "offset": 100})
    check("another request (another offset) is its own read", len(sent("albums")) == 2)
    clock["now"] += 300
    control.requests.clear()
    r3, _ = await ask({"action": "recent"})
    check("after a minute: served AT ONCE from what was kept, with its age (kept, then fresh)",
          not sent() and r3["kept"] == 300 and r3["answer"] == r1["answer"])
    control.find_answers[("roon", "recent")]["sections"][0]["items"][0]["title"] = "Bad"
    r4, _ = await ask({"action": "recent"}, fresh=True)
    check("the page's fresh ask goes to the Control and answers what is there now",
          len(sent("recent")) == 1 and r4["kept"] == 0 and r4["answer"]["sections"][0]["items"][0]["title"] == "Bad")
    control.requests.clear()
    r5, _ = await ask({"action": "recent"})
    check("...and that fresh answer is what is kept from then on", not sent() and r5["answer"]["sections"][0]["items"][0]["title"] == "Bad")
    clock["now"] += find.FIND_KEEP + 1
    await ask({"action": "recent"})
    check("after six hours nothing is kept: asked again", len(sent("recent")) == 1)

    control.requests.clear()
    await ask({"action": "accounts"})
    await ask({"action": "accounts", "fresh": "1"})
    check("accounts fresh=1 (the webapp's 'ask the player now') is never served from the store",
          len(sent("accounts")) == 2)
    control.requests.clear()
    await ask({"action": "accounts"})
    check("...and refreshes the kept accounts", not sent())

    print("\nnever kept: a play, a failed read; a play drops Recent")
    control.zones[1]["active_activity"] = 120
    await S.poll(hass)
    await ask({"action": "recent"})
    await ask({"action": "albums", "offset": 0})
    control.requests.clear()
    item = json.dumps({"path": [{"root": "artists"}, {"title": "MJ"}, {"title": "Bad"}]})
    await ask({"action": "play_item", "item": item, "do": "Play Now"})
    await ask({"action": "play_item", "item": item, "do": "Play Now"})
    check("a play is sent every time (never answered from the store)", len(sent("play_item")) == 2)
    control.requests.clear()
    await ask({"action": "recent"})
    await ask({"action": "albums", "offset": 0})
    check("after a play: Recent is read again, the album list is not", len(sent("recent")) == 1 and not sent("albums"))
    control.requests.clear()
    control.find_answers[("roon", "browse")] = {"items": []}
    control.fail.add("unit_find")
    _, err = await ask({"action": "browse", "path": "[]", "offset": 0})
    control.fail.discard("unit_find")
    await ask({"action": "browse", "path": "[]", "offset": 0})
    check("a failed read is not kept: the next ask goes to the Control", err is not None and len(sent("browse")) == 1, str(sent()))
    control.zones[1]["active_activity"] = 0
    await S.poll(hass)

    print("\ncovers for tiles: made small once, kept per Control")
    from PIL import Image
    big = io.BytesIO()
    Image.new("RGB", (900, 900), (20, 90, 160)).save(big, format="PNG")
    url = "https://covers.example.test/a.png"
    players = list(hass.data["artiled_control"][entry.entry_id]["players"].values())
    player = players[0]
    sess = player._client._session
    sess.outside[url] = big.getvalue()
    key = player._thumbnail(url).split("/image/")[1].split("?")[0]
    before = len([1 for m, u in sess.log if u == url])
    one = await player.async_get_browse_image("image", key)
    two = await player.async_get_browse_image("image", key)
    fetched = len([1 for m, u in sess.log if u == url]) - before
    with Image.open(io.BytesIO(one[0])) as img:
        size = img.size
    check("the tile cover is 320 px (the Control's is 900)", size == (320, 320) and one[1] == "image/jpeg")
    check("asked twice, fetched once", fetched == 1 and one == two, str(fetched))
    other = players[1]
    key2 = other._thumbnail(url).split("/image/")[1].split("?")[0]
    await other.async_get_browse_image("image", key2)
    check("another room of the same Control uses the same kept cover",
          len([1 for m, u in sess.log if u == url]) - before == 1)
    await S.stop(hass)


async def main() -> None:
    pure()
    await through_ha()
    check.done("cache")


if __name__ == "__main__":
    asyncio.run(main())
