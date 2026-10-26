# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Which Control this is, and following it when its address changes.

IDENTITY IS THE CONTROL'S IDENT, QUALIFIED, AND NEVER THE ADDRESS.

/alive answers the ident from /opt/artiled/data/ident, or "" when that file is
missing; the Control's discovery service announces "000000000000000" in the
same case. Neither identifies anything, so setup refuses both.

⚠ The Hub integration falls back to the HOST when the Hub has no ident. That
is deliberately NOT copied: on a Control it would build address-derived ids,
and a Control that moved would come back as a second set of entities.

Address follow (Michel 2026-10-25, N4, feedback_auto_follow_everything):
Home Assistant listens PASSIVELY to the Control's own discovery broadcast on
UDP 50081 - no scan, no probing of other hosts. A packet is only a CANDIDATE;
discovery is not authentication. The host changes only after the candidate
answers our ident on /alive AND accepts the saved token. Broadcasts reach Home
Assistant only on the same network segment; elsewhere (Michel's own house) the
Reconfigure flow is the path.

Pure rules plus a small asyncio listener. No Home Assistant import, so
tests/test_identity.py loads this file by path.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

_LOGGER = logging.getLogger(__name__)

MAGIC = "fna349fn"


def qualified(ident) -> bool:
    """A usable identity: non-empty and not the all-zeros stand-in."""
    text = str(ident or "").strip()
    return bool(text) and set(text) != {"0"}


def ident_verdict(stored, answered) -> str:
    """"same" | "different" | "unqualified" for an /alive answer."""
    if not qualified(answered):
        return "unqualified"
    return "same" if str(answered).strip() == str(stored or "").strip() else "different"


def parse_announcement(data: bytes) -> dict | None:
    """"fna349fn|<ident>|<type>|<ip>|<name>" -> its fields; anything else None.

    The name may itself contain "|", so it is the rest of the line.
    """
    try:
        text = data.decode("utf-8", errors="strict").strip()
    except (UnicodeDecodeError, AttributeError):
        return None
    parts = text.split("|", 4)
    if len(parts) < 4 or parts[0] != MAGIC or not qualified(parts[1]):
        return None
    return {
        "ident": parts[1].strip(),
        "type": parts[2].strip(),
        "ip": parts[3].strip(),
        "name": parts[4].strip() if len(parts) > 4 else "",
    }


def is_ip_literal(host) -> bool:
    """Only an address is ever followed. A HOSTNAME follows itself through DNS,
    and replacing it with an address would quietly undo what the installer
    chose."""
    try:
        ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return False
    return True


class FollowTracker:
    """Which addresses have claimed which ident, and when.

    ⚠ TWO DIFFERENT ADDRESSES CLAIMING ONE IDENT within the window is refused,
    never "the first Control found": a cloned SD card announces the same ident
    from two boxes, and following either one would hand a customer's rooms to
    the wrong building.
    """

    def __init__(self, window: float) -> None:
        self._window = window
        self._claims: dict[str, dict[str, float]] = {}

    def heard(self, ident: str, address: str, now: float) -> None:
        claims = self._claims.setdefault(ident, {})
        claims[address] = now
        for addr in [a for a, at in claims.items() if now - at > self._window]:
            del claims[addr]

    def verdict(self, ident: str, stored_host: str, now: float) -> tuple[str, str | None]:
        """("none" | "candidate" | "ambiguous", address).

        "none" when nothing but the stored host claims this ident lately.
        """
        recent = {a for a, at in (self._claims.get(ident) or {}).items() if now - at <= self._window}
        others = recent - {str(stored_host)}
        if not others:
            return "none", None
        if len(others) > 1 or str(stored_host) in recent:
            # Two new addresses, or the old address still announcing next to a
            # new one: two boxes with one ident, not a move.
            return "ambiguous", None
        return "candidate", next(iter(others))


async def verify_candidate(address: str, stored_ident: str, alive, token_works) -> str:
    """Prove a candidate before following it.

    alive(address) -> /alive's data at that address (raises when unreachable).
    token_works(address) -> True when the SAVED token is accepted there,
    False when it is refused (raises when unreachable).

    Returns "follow" | "different" | "unreachable" | "reauth".
    """
    try:
        answered = (await alive(address) or {}).get("ident")
    except Exception:  # noqa: BLE001 - any failure is "not proven"
        return "unreachable"
    if ident_verdict(stored_ident, answered) != "same":
        return "different"
    try:
        ok = await token_works(address)
    except Exception:  # noqa: BLE001
        return "unreachable"
    return "follow" if ok else "reauth"


class Fence:
    """A generation number per config entry: host + token snapshot.

    ⚠ Bumped on every setup, which follows every reconfigure, re-auth and
    address follow. A client remembers the generation it was made in; a
    response that arrives after the bump belongs to the old host or the old
    token and is dropped, never applied over the new state.
    """

    def __init__(self) -> None:
        self.current = 0

    def bump(self) -> int:
        self.current += 1
        return self.current

    def is_current(self, generation: int) -> bool:
        return generation == self.current


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, callback) -> None:
        self._callback = callback

    def datagram_received(self, data: bytes, addr) -> None:
        packet = parse_announcement(data)
        if packet is None:
            return
        try:
            self._callback(packet, addr[0])
        except Exception as err:  # noqa: BLE001 - a listener must never die on one packet
            _LOGGER.debug("Discovery packet not handled: %s", err)


async def async_listen(loop: asyncio.AbstractEventLoop, port: int, callback):
    """Listen for discovery broadcasts. Returns the transport, or None when the
    port cannot be bound (then Reconfigure is the only path, which is fine).

    ⚠ Shared, not exclusive: SO_REUSEADDR (and SO_REUSEPORT where it exists),
    so another listener on the same host - another integration, a tool - keeps
    hearing the same broadcasts.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", port))
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(callback), sock=sock)
    except OSError as err:
        sock.close()
        _LOGGER.info("Not listening for Control announcements on UDP %s: %s", port, err)
        return None
    return transport
