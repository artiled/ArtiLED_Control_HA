# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Config flow for ArtiCONTROL."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArtiControlAuthError, ArtiControlClient, ArtiControlError
from .const import CONF_ASSIST_ALL, CONF_CREATE_DASHBOARD, CONF_TOKEN, DOMAIN
from .dashboard import DATA_DASHBOARD_CREATED, DATA_DASHBOARD_PENDING, async_create_dashboard
from .identity import ident_verdict, qualified
from .units import too_old

# ⚠ LOGIN, NOT A TOKEN - as on the Hub. The customer already has a Control
# username and password (the webapp's), and /api/login turns that into the API
# token. What gets STORED is the token; the password is used once and never
# persisted.
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        # Michel 2026-10-25 (Q1): nothing reaches voice until someone asks,
        # with this one exception, off by default. Applied once, at setup.
        vol.Optional(CONF_ASSIST_ALL, default=False): bool,
        # S2, as the Hub (#525): create the ArtiCONTROL dashboard on setup.
        # Explicit and skippable; Configure offers it again later.
        vol.Optional(CONF_CREATE_DASHBOARD, default=True): bool,
    }
)

# Re-auth asks for the login only: the credentials stopped working, not the
# address, and re-typing an IP that was right is a chance to get it wrong.
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class _Refused(Exception):
    """A setup check failed; the argument is the form error key."""


async def probe(hass, host: str, username: str | None, password: str | None, token: str | None = None) -> dict:
    """Everything setup must prove about a Control, in order.

    Reachability first, so an unreachable Control reports as such rather than as
    a rejected password. Then a QUALIFIED identity, the same on two /alive
    calls. Then the login (or the saved token), proven on /version. Then the
    fields the unit model rests on.

    Returns {"alive", "token", "version"}; raises _Refused(error key).
    """
    session = async_get_clientsession(hass)
    host = host.strip()
    try:
        alive = await ArtiControlClient(session, host, "").alive()
        if not qualified(alive.get("ident")):
            # ⚠ Never the host as a stand-in identity - see identity.py.
            raise _Refused("no_identity")
        if username is not None:
            token = await ArtiControlClient.login(session, host, username, password or "")
        client = ArtiControlClient(session, host, token or "")
        version = await client.version()
        rooms = await client.rooms()
        zones = await client.zones()
        again = await client.alive()
    except ArtiControlAuthError:
        raise _Refused("invalid_auth") from None
    except ArtiControlError:
        raise _Refused("cannot_connect") from None
    if ident_verdict(alive.get("ident"), again.get("ident")) != "same":
        raise _Refused("no_identity")
    if too_old(rooms, zones):
        raise _Refused("too_old")
    return {"alive": alive, "token": token, "version": version}


class ArtiControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Host + Control login, as the Hub integration does it."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._reauth_host: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                found = await probe(self.hass, host, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except _Refused as refused:
                errors["base"] = str(refused)
            else:
                await self.async_set_unique_id(str(found["alive"]["ident"]).strip())
                # ⚠ The same Control at a new address, signed in successfully:
                # that entry's host and token are updated - never a second entry.
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_TOKEN: found["token"]})
                # ⚠ Only host + token (and the one-shot Assist tick) are
                # persisted. The password did its one job.
                return self.async_create_entry(
                    title=str(found["alive"].get("name") or "ArtiCONTROL"),
                    data={
                        CONF_HOST: host,
                        CONF_TOKEN: found["token"],
                        CONF_ASSIST_ALL: bool(user_input.get(CONF_ASSIST_ALL, False)),
                        CONF_CREATE_DASHBOARD: bool(user_input.get(CONF_CREATE_DASHBOARD, True)),
                    },
                )
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """The Control rejected our token - ask for the login again.

        ⚠ Without this step the prompt Home Assistant shows is a dead end, and
        the customer's only way out is delete and re-add: every entity id, area,
        alias and voice choice gone. The Hub learned that one first.

        Address follow opens this too, for a Control that moved and refuses the
        saved token at its new address: then `follow_host` is the new address.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._reauth_host = entry_data.get("follow_host") if entry_data else None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:  # the entry vanished mid-flow
            return self.async_abort(reason="reauth_failed")
        host = self._reauth_host or entry.data[CONF_HOST]

        if user_input is not None:
            try:
                found = await probe(self.hass, host, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except _Refused as refused:
                errors["base"] = str(refused)
            else:
                # ⚠ Must still be the SAME Control. Every unique_id is built
                # from its ident; signing in to another box would strand them.
                if ident_verdict(entry.unique_id, found["alive"].get("ident")) != "same":
                    return self.async_abort(reason="wrong_control")
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_HOST: host, CONF_TOKEN: found["token"]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"host": host},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """A new address for the SAME Control (Michel 2026-10-25, N4).

        The path where Home Assistant and the Control sit on different network
        segments, so the Control's broadcast never arrives. The login is
        optional: without it the saved token is tried at the new address.
        ⚠ The ident must match, and unique_id never changes - so every entity
        id, name, area and voice choice stays.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_failed")
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            username = (user_input.get(CONF_USERNAME) or "").strip() or None
            try:
                found = await probe(
                    self.hass, host, username, user_input.get(CONF_PASSWORD),
                    token=None if username else entry.data[CONF_TOKEN],
                )
            except _Refused as refused:
                errors["base"] = "token_refused" if str(refused) == "invalid_auth" and not username else str(refused)
            else:
                if ident_verdict(entry.unique_id, found["alive"].get("ident")) != "same":
                    errors["base"] = "wrong_control"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, CONF_HOST: host, CONF_TOKEN: found["token"]},
                        reason="reconfigure_successful",
                    )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Optional(CONF_USERNAME): str,
                vol.Optional(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Configure: let Assist control all rooms, now; create the dashboard again."""
        return ArtiControlOptionsFlow(config_entry)


class ArtiControlOptionsFlow(config_entries.OptionsFlow):
    """Two explicit actions, each a tick, each done once, now.

    - Expose every offered room to Assist: the same tick as in setup, for
      someone who skipped it. An ACTION, not a setting: it is not re-applied on
      reload, so a room someone un-exposes afterwards stays un-exposed.
    - Create the ArtiCONTROL dashboard again (the Hub's #525 path back):
      creation on setup is once-only by design, so a dashboard the customer
      deleted stays deleted; this creates it now (or adopts one at our url
      path) and records it again. It also clears a PENDING marker.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            if user_input.get("assist_all_now"):
                from . import async_expose_all_units  # noqa: PLC0415 - avoids an import cycle

                async_expose_all_units(self.hass, self._entry)
            # A SETTING, not an action (Michel 2026-10-26, off by default): the
            # same switch as the Voice page's Favourites tab.
            wanted = bool(user_input.get("all_favorites"))
            if wanted != bool(self._entry.data.get("all_favorites")):
                from .favorites import set_all  # noqa: PLC0415 - avoids an import cycle

                data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
                if data is not None:
                    await set_all(self.hass, data, wanted)
            if user_input.get("create_dashboard_again"):
                data = {k: v for k, v in self._entry.data.items()
                        if k not in (DATA_DASHBOARD_CREATED, DATA_DASHBOARD_PENDING)}
                data[CONF_CREATE_DASHBOARD] = True
                self.hass.config_entries.async_update_entry(self._entry, data=data)
                await async_create_dashboard(self.hass, self._entry)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("assist_all_now", default=False): bool,
                vol.Optional("create_dashboard_again", default=False): bool,
                vol.Optional("all_favorites", default=bool(self._entry.data.get("all_favorites"))): bool,
            }),
        )
