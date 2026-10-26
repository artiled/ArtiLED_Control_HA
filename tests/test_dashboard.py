#!/usr/bin/env python3
# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""The ArtiCONTROL dashboard (plan section 3, slice S2): dashboard.py, the
frontend registration, the setup and Configure ticks, and icons.py.

Run:  python3 tests/test_dashboard.py     (exit 0 = all pass)

No Home Assistant on this VM: the integration is loaded AS SHIPPED against the
stand-ins in tests/ha_stubs.py, with lovelace's const module stubbed and its
live dashboards collection duck-typed in HA's exact registration shape (the
Hub's test_dashboard.py fixtures, copied). What is proven is the contract, not
HA's internals - those are pinned by hand (dashboard.py docstring) and are
re-checked on the master in the S2 test step.
"""
from __future__ import annotations

import asyncio
import functools
import importlib
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs as S  # noqa: E402

check = S.Checker()

S.install()
LOVELACE_KEY = "lovelace-key"
S._module("homeassistant.components.lovelace")
S._module("homeassistant.components.lovelace.const", LOVELACE_DATA=LOVELACE_KEY)
pkg = S.load_package()
dash = importlib.import_module("artiled_control.dashboard")
const = importlib.import_module("artiled_control.const")
icons_mod = importlib.import_module("artiled_control.icons")
cf = importlib.import_module("artiled_control.config_flow")
PKG = S.PKG_DIR


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- duck-typed lovelace (the Hub's fixtures) --------------------------------


class Store:
    def __init__(self, fail_save=False, config=None):
        self.saved = None
        self.save_calls = 0
        self.config = config
        self.fail_save = fail_save

    async def async_save(self, config):
        self.save_calls += 1
        if self.fail_save:
            raise OSError("disk full")
        self.saved = config
        self.config = config


class Collection:
    """lovelace's DashboardsCollection: creating registers the store under the
    url path, as lovelace's own listener does; deleting removes it."""

    def __init__(self, dashboards, fail=False, fail_save=False, fail_delete=False, no_register=False):
        self.dashboards = dashboards
        self.created = []
        self.deleted = []
        self.data = {}
        self.fail = fail
        self.fail_save = fail_save
        self.fail_delete = fail_delete
        self.no_register = no_register

    def preload(self, url_path="artiled-control", title="ArtiCONTROL", icon="articontrol:logo", config=None):
        item_id = url_path.replace("-", "_")
        self.data[item_id] = {"id": item_id, "url_path": url_path, "title": title, "icon": icon,
                              "show_in_sidebar": True, "require_admin": False}
        store = Store(config=config)
        self.dashboards[url_path] = store
        return store

    async def async_create_item(self, item):
        if self.fail:
            raise RuntimeError("Url path needs to contain a hyphen (-)")
        self.created.append(item)
        item_id = item["url_path"].replace("-", "_")
        stored = {"id": item_id, **item}
        self.data[item_id] = stored
        if not self.no_register:
            self.dashboards[item["url_path"]] = Store(fail_save=self.fail_save)
        return stored

    async def async_delete_item(self, item_id):
        if self.fail_delete:
            raise RuntimeError("storage write failed")
        item = self.data.pop(item_id)
        self.deleted.append(item_id)
        self.dashboards.pop(item["url_path"], None)


class WsOwner:
    def __init__(self, coll):
        self.storage_collection = coll

    async def ws_create_item(self, hass, connection, msg):
        pass


def ha_async_response(func):
    @functools.wraps(func)
    def schedule_handler(hass, connection, msg):
        return None
    return schedule_handler


def ha_require_admin(func):
    @functools.wraps(func)
    def with_admin(hass, connection, msg):
        return func(hass, connection, msg)
    return with_admin


def ha_registered_handler(owner):
    return ha_require_admin(ha_async_response(owner.ws_create_item))


class Entries:
    def __init__(self):
        self.updates = []

    def async_update_entry(self, entry, data=None):
        self.updates.append(data)
        entry.data = data


class Entry:
    def __init__(self, data):
        self.data = dict(data)


def make_hass(dashboards, with_collection=True, fail_create=False, **kw):
    coll = Collection(dashboards, fail=fail_create, **kw)
    data = {LOVELACE_KEY: types.SimpleNamespace(dashboards=dashboards)}
    if with_collection:
        handler = ha_registered_handler(WsOwner(coll))
        data["websocket_api"] = {dash.WS_CREATE_COMMAND: (handler, None)}
    return types.SimpleNamespace(data=data, config_entries=Entries()), coll


def wire_lovelace(hass, dashboards, **kw):
    """Give an inert FakeHass a live lovelace, as Home Assistant has one."""
    coll = Collection(dashboards, **kw)
    hass.data[LOVELACE_KEY] = types.SimpleNamespace(dashboards=dashboards)
    hass.data["websocket_api"] = {dash.WS_CREATE_COMMAND: (ha_registered_handler(WsOwner(coll)), None)}
    return coll


# ---- the constants: ArtiCONTROL's own, never the Hub's ----------------------
print("dashboard.py: the Hub's copy with ArtiCONTROL's constants (plan 3.1)")
check("url path artiled-control, with the hyphen HA requires", dash.DASHBOARD_URL_PATH == "artiled-control")
check("the item: title ArtiCONTROL, icon articontrol:logo, in the sidebar, for everyone",
      dash.DASHBOARD_ITEM == {"url_path": "artiled-control", "title": "ArtiCONTROL", "icon": "articontrol:logo",
                              "show_in_sidebar": True, "require_admin": False})
check("config is the three-line strategy custom:artiled-control",
      dash.DASHBOARD_CONFIG == {"strategy": {"type": "custom:artiled-control"}, "views": []})
check("never the Hub's url path or strategy", dash.DASHBOARD_URL_PATH != "artiled-app"
      and dash.DASHBOARD_CONFIG["strategy"]["type"] != "custom:artiled-hub")
check("the setup tick's key is the one the config flow writes", dash.CONF_CREATE_DASHBOARD == const.CONF_CREATE_DASHBOARD)
js = (PKG / "frontend" / "articontrol.js").read_text()
check("the strategy element the frontend defines matches the config's type",
      '"ll-strategy-dashboard-artiled-control"' in js)
check("the icon the item names is the iconset the frontend registers",
      "window.customIconsets.articontrol = " in js)

# ---- the pure planner (the Hub's cases) -------------------------------------
print("\nplan_dashboard")
check("unticked wins", dash.plan_dashboard(False, False, False) == "skip:disabled")
check("created once stays deleted", dash.plan_dashboard(True, True, False) == "skip:created")
check("existing is adopted", dash.plan_dashboard(True, False, True) == "adopt")
check("fresh install creates", dash.plan_dashboard(True, False, False) == "create")
check("pending -> skip, whether or not something exists",
      dash.plan_dashboard(True, False, True, pending=True) == "skip:pending"
      and dash.plan_dashboard(True, False, False, pending=True) == "skip:pending")
check("created beats pending", dash.plan_dashboard(True, True, True, pending=True) == "skip:created")
check("unticked beats pending", dash.plan_dashboard(False, False, True, pending=True) == "skip:disabled")

# ---- async_create_dashboard (the Hub's cases) -------------------------------
print("\nasync_create_dashboard")
dashboards = {}
hass, coll = make_hass(dashboards)
entry = Entry({"host": "192.0.2.10", "token": "t", "create_dashboard": True})
out = run(dash.async_create_dashboard(hass, entry))
check("fresh: created with the pinned item and the strategy config",
      out == "create" and coll.created == [dash.DASHBOARD_ITEM] and dashboards["artiled-control"].saved == dash.DASHBOARD_CONFIG)
check("fresh: marker recorded, entry data kept",
      bool(entry.data.get(dash.DATA_DASHBOARD_CREATED)) and entry.data["host"] == "192.0.2.10")
dashboards.clear()
check("restart after the customer deleted it: stays deleted",
      run(dash.async_create_dashboard(hass, entry)) == "skip:created" and dashboards == {})

d2 = {}
hass2, coll2 = make_hass(d2)
coll2.preload()
entry2 = Entry({"host": "192.0.2.11", "token": "u", "create_dashboard": True})
check("a second Control: adopts, never a second dashboard, config untouched",
      run(dash.async_create_dashboard(hass2, entry2)) == "adopt" and coll2.created == [] and d2["artiled-control"].saved is None
      and bool(entry2.data.get(dash.DATA_DASHBOARD_CREATED)))

d_hub = {}
hass_hub, coll_hub = make_hass(d_hub)
hub_store = coll_hub.preload(url_path="artiled-app", title="ArtiLED", icon="artiled:logo",
                             config={"strategy": {"type": "custom:artiled-hub"}, "views": []})
entry_hub = Entry({"host": "h", "token": "t"})
out_hub = run(dash.async_create_dashboard(hass_hub, entry_hub))
check("next to the ArtiLED (Hub) dashboard: ours is created beside it", out_hub == "create" and "artiled-control" in d_hub)
check("the Hub's dashboard is never adopted, written or deleted",
      hub_store.save_calls == 0 and hub_store.config["strategy"]["type"] == "custom:artiled-hub"
      and coll_hub.deleted == [] and "artiled-app" in d_hub)

entry_old = Entry({"host": "h", "token": "t"})   # an S1 entry: no tick stored -> default on
hass_old, coll_old = make_hass({})
check("an S1 entry without the tick: default on, created", run(dash.async_create_dashboard(hass_old, entry_old)) == "create")
hass4, coll4 = make_hass({})
entry4 = Entry({"host": "h", "token": "t", "create_dashboard": False})
check("unticked: nothing created, no marker",
      run(dash.async_create_dashboard(hass4, entry4)) == "skip:disabled" and coll4.created == []
      and not entry4.data.get(dash.DATA_DASHBOARD_CREATED))
hass5, _ = make_hass({}, with_collection=False)
entry5 = Entry({"host": "h", "token": "t"})
check("no live collection: logged and skipped, never raises, no marker",
      run(dash.async_create_dashboard(hass5, entry5)) == "error:no-collection" and not entry5.data.get(dash.DATA_DASHBOARD_CREATED))
check("no lovelace data: logged and skipped",
      run(dash.async_create_dashboard(types.SimpleNamespace(data={}, config_entries=Entries()), Entry({}))) == "error:no-lovelace-data")
hass7, _ = make_hass({}, fail_create=True)
entry7 = Entry({"host": "h"})
check("create raises inside HA: swallowed, no marker",
      run(dash.async_create_dashboard(hass7, entry7)) == "error:RuntimeError" and not entry7.data.get(dash.DATA_DASHBOARD_CREATED))
real = ha_registered_handler(WsOwner(coll))
check("finder resolves HA's require_admin(async_response(bound))",
      dash._find_live_collection(types.SimpleNamespace(data={"websocket_api": {dash.WS_CREATE_COMMAND: (real, None)}})) is coll)
check("finder: an undecorated function without __self__ -> None",
      dash._find_live_collection(types.SimpleNamespace(data={"websocket_api": {dash.WS_CREATE_COMMAND: (lambda *a: None, None)}})) is None)

d8 = {}
hass8, coll8 = make_hass(d8, fail_save=True)
entry8 = Entry({"host": "h"})
check("save fails after create: the empty item is rolled back, nothing recorded",
      run(dash.async_create_dashboard(hass8, entry8)) == "error:rolled-back" and coll8.deleted == ["artiled_control"]
      and d8 == {} and not entry8.data.get(dash.DATA_DASHBOARD_CREATED))
coll8.fail_save = False
check("after a rollback the next setup creates afresh",
      run(dash.async_create_dashboard(hass8, entry8)) == "create" and d8["artiled-control"].saved == dash.DASHBOARD_CONFIG)
d10 = {}
hass10, coll10 = make_hass(d10, fail_save=True, fail_delete=True)
entry10 = Entry({"host": "h"})
out10 = run(dash.async_create_dashboard(hass10, entry10))
pend = entry10.data.get(dash.DATA_DASHBOARD_PENDING) or {}
check("rollback fails too: PENDING with our id, never 'created'",
      out10 == "error:pending" and pend.get("id") == "artiled_control" and pend.get("url_path") == "artiled-control"
      and not entry10.data.get(dash.DATA_DASHBOARD_CREATED))
d10["artiled-control"].fail_save = False
saves = d10["artiled-control"].save_calls
check("pending: the next setup touches nothing",
      run(dash.async_create_dashboard(hass10, entry10)) == "skip:pending" and d10["artiled-control"].save_calls == saves
      and coll10.deleted == [] and len(coll10.created) == 1)
d12 = {}
hass12, coll12 = make_hass(d12)
mine = coll12.preload(config={"views": [{"title": "Mine"}]})
entry12 = Entry({"host": "h", dash.DATA_DASHBOARD_PENDING: {"id": "artiled_control", "url_path": "artiled-control"}})
check("pending + a customer's same-id replacement: zero writes",
      run(dash.async_create_dashboard(hass12, entry12)) == "skip:pending" and mine.save_calls == 0
      and mine.config["views"][0]["title"] == "Mine")


# ---- the integration: frontend registration and the dashboard at setup ------
async def integration():
    print("\nsetup: the frontend module, served under ArtiCONTROL's own paths")
    hass = S.FakeHass()
    hass.session = S.FakeSession()
    dashboards = {}
    coll = wire_lovelace(hass, dashboards)
    hass, entry, control = await S.setup_integration(hass=hass)
    version = json.loads((PKG / "manifest.json").read_text())["version"]
    paths = {c.url_path: c.path for c in hass.http.static}
    check("two static paths, both ArtiCONTROL's", set(paths) == {"/artiled_control_frontend", "/artiled_control_icons"},
          str(paths))
    check("never the Hub's paths", not any(p.startswith("/artiled_hub") for p in paths))
    check("the frontend path serves the package's frontend directory",
          pathlib.Path(paths["/artiled_control_frontend"]) == PKG / "frontend")
    check("the icon path is the CONFIG directory's cache, not the package",
          pathlib.Path(paths["/artiled_control_icons"]) == pathlib.Path(hass.config.path(const.ICON_CACHE_DIR))
          and PKG not in pathlib.Path(paths["/artiled_control_icons"]).parents)
    check("the module is added once, versioned from manifest.json",
          hass.extra_js == [f"/artiled_control_frontend/articontrol.js?v={version}"], str(hass.extra_js))
    served = sorted(p.name for p in (PKG / "frontend").iterdir())
    check("the served directory holds only the module (no Font Awesome file shipped)", served == ["articontrol.js"], str(served))
    check("the dashboard was created at setup", coll.created == [dash.DASHBOARD_ITEM]
          and dashboards["artiled-control"].saved == dash.DASHBOARD_CONFIG and bool(entry.data.get(dash.DATA_DASHBOARD_CREATED)))
    check("the icon fetch and the favourites warm-up run in the background, never delaying setup",
          entry.background == ["artiled_control_icons", "artiled_control_warm_hearts"], str(entry.background))

    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("a reload registers nothing twice and creates nothing again",
          len(hass.http.static) == 2 and len(hass.extra_js) == 1 and len(coll.created) == 1)

    second = S.house(S.FakeControl(ident="c0ffee000002", name="Annex"))
    hass.session.boxes["192.0.2.20"] = second
    entry2 = S.ConfigEntry("e2", second.ident, {"host": "192.0.2.20", "token": second.token}, title="Annex")
    hass.config_entries.entries["e2"] = entry2
    await hass.config_entries.setup(entry2)
    check("a second Control: loaded, no second registration, one dashboard (adopted)",
          entry2.state == S.ConfigEntryState.LOADED and len(hass.http.static) == 2 and len(hass.extra_js) == 1
          and len(coll.created) == 1 and bool(entry2.data.get(dash.DATA_DASHBOARD_CREATED)))

    print("\npresentation never blocks control")
    h2 = S.FakeHass()
    h2.session = S.FakeSession()
    h2.http = object()   # no static-path API at all
    h2, e3, _ = await S.setup_integration(hass=h2)
    check("no http static paths: the integration still loads, entities exist",
          e3.state == S.ConfigEntryState.LOADED and any(e.entity_id.startswith("media_player.") for e in h2.entities))
    check("no lovelace: still loads, nothing recorded", not e3.data.get(dash.DATA_DASHBOARD_CREATED))

    print("\nthe icons the dashboard asks the Control for")
    names = pkg.icon_names(hass.data["artiled_control"]["e1"]["coordinator"].data)
    check("the rooms' and zones' own icons", "fa-couch" in names)
    check("every activity's icon", "fa-tv" in names)
    check("the player's key glyphs", set(const.CONTROL_GLYPHS) <= names)

    print("\nthe attributes the tile needs (S2 additions)")
    player = hass.entity("media_player.kitchen")
    attrs = player.extra_state_attributes
    check("unit_icon: the unit's own icon", attrs["unit_icon"] == "fa-couch")
    keys = list(hass.data["artiled_control"]["e1"]["coordinator"].data["units"])
    check("unit_order: its place in the Control's order", attrs["unit_order"] == keys.index("room_2"))
    orders = sorted(e.extra_state_attributes["unit_order"] for e in hass.entities
                    if e.entity_id.startswith("media_player.") and e._entry_id == "e1")
    check("unit_order differs per unit", len(set(orders)) == len(orders))
    check("volume_max: None while the unit is off", attrs["volume_max"] is None)
    loud = S.house(S.FakeControl())
    loud.rooms[1].update(active_activity=20, volume=40, volume_display="-40.0 dB", volume_max=98, volume_scale="db")
    h3, _, _ = await S.setup_integration(control=loud)
    a3 = h3.entity("media_player.kitchen").extra_state_attributes
    check("volume_max: the device's maximum while on, beside its scale and display",
          a3["volume_max"] == 98 and a3["volume_scale"] == "db" and a3["volume_display"] == "-40.0 dB")


async def flows():
    print("\nconfig flow: the dashboard tick, and Configure 'create it again'")

    async def run_user(data):
        hass = S.FakeHass()
        hass.session = S.FakeSession()
        hass.session.boxes["192.0.2.30"] = S.house(S.FakeControl())
        flow = cf.ArtiControlConfigFlow()
        flow.hass = hass
        return await flow.async_step_user(data)

    base = {"host": "192.0.2.30", "username": "installer", "password": "secret-password"}
    r = await run_user(dict(base))
    check("ticked by default", r["type"] == "create_entry" and r["data"]["create_dashboard"] is True)
    r = await run_user({**base, "create_dashboard": False})
    check("unticked is stored as such", r["data"]["create_dashboard"] is False)
    check("the password is never stored", "secret-password" not in json.dumps(r["data"]))
    keys = [str(getattr(k, "key", k)) for k in cf.DATA_SCHEMA.schema]
    check("the form offers the tick", "create_dashboard" in keys)

    hass = S.FakeHass()
    hass.session = S.FakeSession()
    dashboards = {}
    coll = wire_lovelace(hass, dashboards)
    hass, entry, _ = await S.setup_integration(hass=hass)
    dashboards.clear()   # the customer deletes the dashboard
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("deleted stays deleted across a reload", "artiled-control" not in dashboards and len(coll.created) == 1)
    flow = cf.ArtiControlOptionsFlow(entry)
    flow.hass = hass
    form = await flow.async_step_init(None)
    check("Configure shows its form", form["type"] == "form")
    entry.data = {**entry.data, dash.DATA_DASHBOARD_PENDING: {"id": "x", "url_path": "artiled-control"}}
    r = await flow.async_step_init({"create_dashboard_again": True})
    check("'create it again' clears the marker and the pending note, then creates",
          r["type"] == "create_entry" and len(coll.created) == 2 and "artiled-control" in dashboards
          and not entry.data.get(dash.DATA_DASHBOARD_PENDING) and bool(entry.data.get(dash.DATA_DASHBOARD_CREATED)))
    exposed_before = len(hass.expose_calls)
    await flow.async_step_init({"create_dashboard_again": False, "assist_all_now": True})
    check("the Assist tick still works beside it", len(hass.expose_calls) > exposed_before and len(coll.created) == 2)


async def icons():
    print("\nicons.py: Font Awesome from the Control's own web root, never bundled")

    class Resp:
        def __init__(self, status, body=""):
            self.status = status
            self._body = body

        async def text(self):
            return self._body

    class IconSession:
        def __init__(self, files):
            self.files = files
            self.urls = []

        async def get(self, url, timeout=None):
            self.urls.append(url)
            path = url.split("192.0.2.10", 1)[1]
            if path in self.files:
                return Resp(200, self.files[path])
            return Resp(404)

    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 512"><path d="M64 64 TV"/></svg>'
    files = {
        "/fontawesome/svgs/solid/tv.svg": svg,
        "/fontawesome/svgs/solid/bed-front.svg": svg.replace("TV", "BED"),
        "/fontawesome/metadata/icons.yml": "bed-front:\n  aliases:\n    names:\n      - bed-alt\n  label: Bed\n",
    }
    hass = S.FakeHass()
    session = IconSession(files)
    icons = icons_mod.ControlIcons(hass, session, "http://192.0.2.10")
    got = await icons.async_refresh({"fa-solid fa-tv", "fas fa-bed-alt", "fa-solid fa-nothing"})
    check("a solid icon is read and parsed to [path, viewBox]", got.get("tv") == ["M64 64 TV", "0 0 640 512"])
    check("an alias resolves through icons.yml", got.get("bed-alt") == ["M64 64 BED", "0 0 640 512"])
    check("an icon the Control cannot serve is simply absent", "nothing" not in got)
    check("only the web root's static files are read - no API, no token",
          all("/fontawesome/" in u and "token" not in u and "/api/" not in u for u in session.urls), str(session.urls))
    cache = pathlib.Path(hass.config.path(const.ICON_CACHE_DIR, const.ICON_CACHE_FILE))
    check("the cache is written in the CONFIG directory", json.loads(cache.read_text()).get("tv") == ["M64 64 TV", "0 0 640 512"])
    session.urls.clear()
    await icons.async_refresh({"fa-solid fa-tv"})
    check("an icon already cached is not fetched again", session.urls == [])

    class DeadSession:
        async def get(self, url, timeout=None):
            import aiohttp
            raise aiohttp.ClientError("down")
    quiet = icons_mod.ControlIcons(S.FakeHass(), DeadSession(), "http://192.0.2.99")
    try:
        await quiet.async_refresh({"fa-solid fa-radio"})
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    check("a Control that serves nothing never raises (an icon is cosmetic)", ok)
    check("the shortcut glyphs are the light style, kept apart (light-<name>), as the frontend's fa_key",
          icons_mod.icon_key("fa-light fa-folder-open") == "light-folder-open" and icons_mod.icon_key("fa-solid fa-tv") == "tv"
          and icons_mod.FA_STYLE.format(style="light", name="folder-open") == "/fontawesome/svgs/light/folder-open.svg")
    check("fa_name agrees with the frontend's", icons_mod.fa_name("fa-solid fa-tv") == "tv"
          and icons_mod.fa_name("fas fa-bed-alt") == "bed-alt" and icons_mod.fa_name("") == "")


async def voice_scenes():
    """Live on the master, HA 2026.10.4 (Michel 2026-10-26): a voice activity
    read "Kitchen Listen to Roon in Kitchen" (scene.kitchen_kitchen_listen_to_
    roon_in_kitchen), no assistant heard it, and a removed one came back under
    its old id. The stub registry now names entities as HA 2026.10 does."""
    print("\nvoice activities from the Voice page: the spoken name, heard, cleanly removed")
    ASSIST = ("conversation", "cloud.alexa", "cloud.google_assistant")
    hass, entry, _ = await S.setup_integration()
    # Home Assistant Cloud with Alexa on and Google off (cloud prefs).
    hass.data["cloud"] = S.cloud(alexa=True, google=False)
    # ⚠ The room itself is exposed to NOTHING: since Michel 2026-10-26 a
    # voice activity no longer copies its room's exposure.
    reloads = hass.config_entries.reloads
    await hass.services.call("artiled_control", "add_voice_activity",
                             {"entity_id": "media_player.kitchen", "activity": 20, "name": "Listen to Sonos in Kitchen"})
    await hass.settle()
    check("a voice activity is added IN PLACE - no reload of the entry (Michel 2026-10-26)", hass.config_entries.reloads == reloads)
    scene = hass.entity("scene.listen_to_sonos_in_kitchen")
    check("the entity_id is the spoken name's slug - no area, no device prefix", scene is not None,
          str(sorted(e for e in hass.entity_registry.entities if e.startswith("scene."))))
    state = hass.states.get("scene.listen_to_sonos_in_kitchen")
    check("the friendly name is EXACTLY the spoken name",
          state is not None and state.attributes.get("friendly_name") == "Listen to Sonos in Kitchen",
          str(state and state.attributes.get("friendly_name")))
    reg = hass.entity_registry.async_get("scene.listen_to_sonos_in_kitchen")
    check("the scene is on no device (a device would prefix its name in HA 2026.10)", reg is not None and reg.device_id is None)
    check("it carries which unit + activity it starts", state is not None
          and state.attributes.get("artiled_unit") == "room_2" and state.attributes.get("artiled_activity") == 20)
    check("ALWAYS exposed to Assist and to the cloud assistant that is on (Alexa) - adding it IS the asking, "
          "whatever the room's own exposure (Michel 2026-10-26)",
          hass.exposure.get(("conversation", "scene.listen_to_sonos_in_kitchen")) is True
          and hass.exposure.get(("cloud.alexa", "scene.listen_to_sonos_in_kitchen")) is True)
    check("...and not to a cloud assistant that is off in Home Assistant (Google)",
          hass.exposure.get(("cloud.google_assistant", "scene.listen_to_sonos_in_kitchen")) is False)
    check("...the room's own exposure untouched",
          hass.exposure.get(("conversation", "media_player.kitchen")) is False)

    hass.exposure[("cloud.alexa", "scene.listen_to_sonos_in_kitchen")] = False   # the user changes it
    hass.exposure[("cloud.google_assistant", "media_player.kitchen")] = True
    hass.data["cloud"] = S.cloud(alexa=True, google=True)   # Google switched on later: not re-applied
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    check("applied ONCE: a later user change stands across a reload",
          hass.exposure.get(("cloud.alexa", "scene.listen_to_sonos_in_kitchen")) is False
          and hass.exposure.get(("cloud.google_assistant", "scene.listen_to_sonos_in_kitchen")) is False)

    await hass.services.call("artiled_control", "add_voice_activity",
                             {"entity_id": "media_player.secondary", "activity": 120})
    await hass.settle()
    check("a room exposed to nothing: its new voice activity is still heard (Assist, and Alexa and Google now both on)",
          all(hass.exposure.get((a, "scene.roon_in_the_secondary")) is True for a in ASSIST),
          str([(a, hass.exposure.get((a, "scene.roon_in_the_secondary"))) for a in ASSIST]))
    hass.data.pop("cloud")
    await hass.services.call("artiled_control", "add_voice_activity",
                             {"entity_id": "media_player.living_room_main", "activity": 112})
    await hass.settle()
    main_scene = next((e for e in hass.entity_registry.entities if e.startswith("scene.") and "kaleidescape" in e), "")
    check("no Home Assistant Cloud: Assist only",
          hass.exposure.get(("conversation", main_scene)) is True
          and hass.exposure.get(("cloud.alexa", main_scene)) is False
          and hass.exposure.get(("cloud.google_assistant", main_scene)) is False, main_scene)
    hass.data["cloud"] = S.cloud(alexa=True, google=True)

    if "scene.listen_to_sonos_in_kitchen" in hass.entity_registry.entities:
        hass.entity_registry.async_update_entity("scene.listen_to_sonos_in_kitchen", new_entity_id="scene.my_sonos")
    await hass.config_entries.async_reload("e1")
    await hass.settle()
    await hass.services.call("artiled_control", "remove_voice_activity",
                             {"entity_id": "media_player.kitchen", "activity": 20})
    await hass.settle()
    uid = f"{S.IDENT}_voice_activity_room_2_20"
    check("removed: no registry entry", "scene.my_sonos" not in hass.entity_registry.entities)
    check("removed: no deleted-entity leftover HA would restore the old id from",
          not any(k[2] == uid for k in hass.entity_registry.deleted_entities), str(list(hass.entity_registry.deleted_entities)))
    flags = hass.storage.get("artiled_control_e1_flags") or {}
    check("removed: its once-only voice default is forgotten, so a re-add is new",
          uid not in (flags.get("voice_defaults") or []))
    await hass.services.call("artiled_control", "add_voice_activity",
                             {"entity_id": "media_player.kitchen", "activity": 20, "name": "Listen to Sonos in Kitchen"})
    await hass.settle()
    check("re-added: the clean entity_id again, not the old one",
          "scene.listen_to_sonos_in_kitchen" in hass.entity_registry.entities and "scene.my_sonos" not in hass.entity_registry.entities)
    check("re-added: exposed again to every assistant that is on",
          hass.exposure.get(("cloud.alexa", "scene.listen_to_sonos_in_kitchen")) is True
          and hass.exposure.get(("cloud.google_assistant", "scene.listen_to_sonos_in_kitchen")) is True)
    await S.stop(hass)


async def voice_aliases():
    """Michel 2026-10-26 (0.2.3): the suggested name drops the verb ("Sonos in
    the Kitchen"); the long form ("Listen to Sonos in the Kitchen") becomes an
    alias, and a clash keeps the verb."""
    print("\nvoice activities: the long form as an alias, the verb kept on a clash")
    control = S.house(S.FakeControl())
    control.activities.append(S.activity(22, "Play Sonos", room_id=2))
    hass, entry, _ = await S.setup_integration(control=control)
    reg = hass.entity_registry
    await hass.services.call("artiled_control", "add_voice_activity", {"entity_id": "media_player.kitchen", "activity": 20})
    await hass.settle()
    e = reg.async_get("scene.sonos_in_the_kitchen")
    check("no name given: the short form", e is not None, str(sorted(x for x in reg.entities if x.startswith("scene."))))
    check("the long form is added as an alias, after HA's own computed name",
          e is not None and e.aliases == [S.COMPUTED_NAME, "Listen to Sonos in the Kitchen"], str(e and e.aliases))
    await hass.services.call("artiled_control", "add_voice_activity", {"entity_id": "media_player.kitchen", "activity": 22})
    await hass.settle()
    check("a second activity whose short form is taken keeps its verb",
          reg.async_get("scene.play_sonos_in_the_kitchen") is not None,
          str(sorted(x for x in reg.entities if x.startswith("scene."))))
    p = reg.async_get("scene.play_sonos_in_the_kitchen")
    check("...and needs no alias (its name IS the long form)", p is not None and p.aliases == [S.COMPUTED_NAME], str(p and p.aliases))

    reg.async_update_entity("scene.sonos_in_the_kitchen", aliases=[S.COMPUTED_NAME, "Music please"])
    await hass.services.call("artiled_control", "add_voice_activity",
                             {"entity_id": "media_player.kitchen", "activity": 20, "name": "Kitchen tunes"})
    await hass.settle()
    k = reg.async_get("scene.sonos_in_the_kitchen")
    check("a rename keeps the user's own aliases and adds the long form once",
          k is not None and "Music please" in k.aliases and k.aliases.count("Listen to Sonos in the Kitchen") == 1
          and S.COMPUTED_NAME in k.aliases, str(k and k.aliases))

    control2 = S.house(S.FakeControl())
    h2, _, _ = await S.setup_integration(control=control2)
    h2.states.set("light.kitchen_music", types.SimpleNamespace(
        state="on", attributes={"friendly_name": "Listen to Sonos in the Kitchen"}))
    await h2.services.call("artiled_control", "add_voice_activity", {"entity_id": "media_player.kitchen", "activity": 20})
    await h2.settle()
    e2 = h2.entity_registry.async_get("scene.sonos_in_the_kitchen")
    check("no alias when another entity already answers to the long form",
          e2 is not None and e2.aliases == [S.COMPUTED_NAME], str(e2 and e2.aliases))
    await S.stop(hass)
    await S.stop(h2)

    print("\nvoice activities made by 0.2.2 get the alias once (backfill)")
    control3 = S.house(S.FakeControl())
    h3, e3, _ = await S.setup_integration(control=control3)
    await h3.services.call("artiled_control", "add_voice_activity", {"entity_id": "media_player.kitchen", "activity": 20})
    await h3.services.call("artiled_control", "add_voice_activity", {"entity_id": "media_player.secondary", "activity": 120})
    await h3.settle()
    r3 = h3.entity_registry
    # as 0.2.2 left them: no alias of their own, nothing recorded
    r3.async_update_entity("scene.sonos_in_the_kitchen", aliases=[S.COMPUTED_NAME])
    r3.async_update_entity("scene.roon_in_the_secondary", aliases=[S.COMPUTED_NAME, "Roon please"])
    flags = h3.storage.get("artiled_control_e1_flags") or {}
    flags.pop("alias_backfill", None)
    h3.storage["artiled_control_e1_flags"] = flags
    await h3.config_entries.async_reload("e1")
    await h3.settle()
    check("backfill: a scene without aliases gets the long form",
          r3.async_get("scene.sonos_in_the_kitchen").aliases == [S.COMPUTED_NAME, "Listen to Sonos in the Kitchen"],
          str(r3.async_get("scene.sonos_in_the_kitchen").aliases))
    check("backfill: a scene with the user's own aliases is left alone",
          r3.async_get("scene.roon_in_the_secondary").aliases == [S.COMPUTED_NAME, "Roon please"])
    r3.async_update_entity("scene.sonos_in_the_kitchen", aliases=[S.COMPUTED_NAME])   # the user removes ours
    await h3.config_entries.async_reload("e1")
    await h3.settle()
    check("backfill: once - an alias the user removed is not put back",
          r3.async_get("scene.sonos_in_the_kitchen").aliases == [S.COMPUTED_NAME])
    check("backfill: recorded per scene in the flags store",
          f"{S.IDENT}_voice_activity_room_2_20" in (h3.storage["artiled_control_e1_flags"].get("alias_backfill") or []))
    await S.stop(h3)


async def main():
    await integration()
    await flows()
    await icons()
    await voice_scenes()
    await voice_aliases()


asyncio.run(main())
check.done("dashboard")
