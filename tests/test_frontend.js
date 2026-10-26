#!/usr/bin/env node
// Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
/* Tests for the ArtiCONTROL frontend module (plan section 3, slice S2).
 *
 * Run:  JSDOM_PATH=~/tools/jsdom/node_modules/jsdom node tests/test_frontend.js
 *       (exit 0 = all pass). HUB_HA_DIR points at the ArtiLED Hub integration's
 *       checkout for the side-by-side checks (default
 *       /opt/artiled/software/ArtiLED_Hub_HA); without it those checks FAIL,
 *       they are never skipped.
 *
 * The module is loaded AS SHIPPED - nothing here is a copy of it:
 *   1. in a vm with the few browser globals it touches, an epilogue handing
 *      the pure rules back (the Hub's test_frontend.js approach);
 *   2. next to the Hub's own artiled-hub.js, in BOTH load orders, sharing one
 *      window and one element registry - the S2 "done when";
 *   3. in jsdom, where the cards are rendered and clicked, and the service
 *      calls they make are recorded. No network: fetch is a stub.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { spawnSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const SRC = path.join(ROOT, "custom_components", "artiled_control", "frontend", "articontrol.js");
const HUB_DIR = process.env.HUB_HA_DIR || "/opt/artiled/software/ArtiLED_Hub_HA";
const HUB_SRC = path.join(HUB_DIR, "custom_components", "artiled_hub", "frontend", "artiled-hub.js");
const JSDOM_PATH = (process.env.JSDOM_PATH || path.join(os.homedir(), "tools/jsdom/node_modules/jsdom"))
  .replace(/^~(?=\/)/, os.homedir());

const SOURCE = fs.readFileSync(SRC, "utf8");
/* The code without its block comments - the namespace checks are about what
   RUNS; the comments name the Hub's paths on purpose, to warn about them. */
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "");
const FAILURES = [];
let checks = 0;

function check(label, ok, hint) {
  checks += 1;
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${label}`);
  if (!ok) {
    FAILURES.push(label);
    if (hint) console.log(`        ${hint}`);
  }
}

const EXPORTS = [
  "classify", "unit_on", "tile_status", "tile_icon", "tile_art", "tile_wash", "volume_view",
  "level_for_percent", "volume_text", "slider_value_text", "key_shown", "transport_keys",
  "mute_shown", "np_words", "np_position", "play_modes", "seek_to", "clock", "activity_rows", "off_row", "voice_suggest",
  "voice_suggest_long", "suggest_name", "aliases_shown", "aliases_to_save",
  "unit_path", "fa_name", "icon_name", "collect", "build_views", "no_units_view", "error_text",
  "ArtiControlDashboardStrategy", "find_accounts", "heos_account", "layout_word", "layout_columns", "LAYOUTS", "LAYOUT_DEFAULT",
];

/* A registry like window.customElements: records definitions, refuses a
   second define() of a tag as the real one does. */
class Registry {
  constructor(label) { this.label = label; this.map = new Map(); }
  define(tag, ctor) {
    if (this.map.has(tag)) throw new Error(`${tag} already defined`);
    this.map.set(tag, ctor);
  }
  get(tag) { return this.map.get(tag); }
  whenDefined() { return Promise.resolve(); }
}

class FakeElement {
  attachShadow() { return { innerHTML: "", querySelector: () => null, querySelectorAll: () => [] }; }
}

/* Icon caches as the two integrations would serve them. */
const HUB_ICONS = { couch: ["M1 1 HUB-COUCH", "0 0 640 512"] };
const CONTROL_ICONS = {
  tv: ["M2 2 CONTROL-TV", "0 0 640 512"],
  "power-off": ["M3 3 POWER", "0 0 512 512"],
  play: ["M4 4 PLAY", "0 0 384 512"],
};
function fake_fetch(url) {
  const u = String(url);
  if (u.startsWith("/artiled_hub_icons/")) return Promise.resolve({ ok: true, json: () => Promise.resolve(HUB_ICONS) });
  if (u.startsWith("/artiled_control_icons/")) return Promise.resolve({ ok: true, json: () => Promise.resolve(CONTROL_ICONS) });
  return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
}

function make_sandbox(shared) {
  const box = {
    HTMLElement: FakeElement,
    customElements: shared.registry,
    window: shared.window,
    document: { activeElement: null, querySelector: () => null },
    location: { href: "http://ha.local/", pathname: "/artiled-control/home" },
    history: { pushState: () => {} },
    URL,
    fetch: fake_fetch,
    console,
    setTimeout,
    Promise,
    Date,
    JSON,
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    setInterval: (fn) => { shared.intervals.push(fn); return shared.intervals.length; },
    clearInterval: () => {},
  };
  box.globalThis = box;
  shared.window.location = box.location;
  return vm.createContext(box);
}

function load(file, shared, epilogue) {
  const ctx = make_sandbox(shared);
  vm.runInContext(fs.readFileSync(file, "utf8") + (epilogue || ""), ctx, { filename: file });
  return ctx;
}

async function main() {
  /* ---------------------------------------------------------------- */
  console.log("\nthe shipped file is text a browser will actually execute");
  const raw = fs.readFileSync(SRC);
  check("no NUL byte in the module", !raw.includes(0),
        "a NUL makes the file binary; the browser loads it but the elements never register");
  check("the module is clean UTF-8", Buffer.compare(Buffer.from(raw.toString("utf8"), "utf8"), raw) === 0);
  check("node --check accepts it", spawnSync(process.execPath, ["--check", SRC]).status === 0);
  check("no import.meta (the test harness and a classic load would choke on it)", !/import\.meta/.test(SOURCE));

  const solo = { registry: new Registry("native"), window: {}, intervals: [] };
  const ctx = load(SRC, solo, `\n;globalThis.__t = {${EXPORTS.join(", ")}};\n`);
  const t = ctx.__t;

  check("registration targets BOTH registries (the Hub's scoped-registry fix)",
        /define_all\(NATIVE_REGISTRY\)/.test(SOURCE) && /define_all\(customElements\)/.test(SOURCE));
  check("the swap is watched, and the watch is bounded",
        /customElements !== NATIVE_REGISTRY/.test(SOURCE) && /clearInterval\(watcher\)/.test(SOURCE) &&
        /REGISTRY_WATCH_MS/.test(SOURCE));
  check("define_once swallows an already-defined error",
        /try \{\n    if \(!registry\.get\(tag\)\) registry\.define\(tag, ctor\);/.test(SOURCE));

  /* ---------------------------------------------------------------- */
  console.log("\nArtiCONTROL's own names, never the Hub's (plan 3.2)");
  const tags = Array.from(solo.registry.map.keys()).sort();
  check("exactly our seven elements are defined",
        JSON.stringify(tags) === JSON.stringify([
          "artiled-control-browse-card", "artiled-control-favorites-card", "artiled-control-home-card", "artiled-control-player-card", "artiled-control-unit-card", "artiled-control-voice-card",
          "ll-strategy-dashboard-artiled-control"]), JSON.stringify(tags));
  check("the strategy tag matches custom:artiled-control (dashboard.py)",
        solo.registry.get("ll-strategy-dashboard-artiled-control") === t.ArtiControlDashboardStrategy);
  const HUB_TAGS = ["artiled-tile-card", "artiled-entity-card", "artiled-colors-card", "artiled-voice-card",
                    "ll-strategy-dashboard-artiled-hub"];
  check("none of the Hub's tags appears in the source",
        HUB_TAGS.every((tag) => !CODE.includes(`"${tag}"`)));
  check("the Hub's static paths never appear in the code", !/\/artiled_hub_/.test(CODE));
  check("the Hub's artiled-home-card tag is not ours", !CODE.includes('"artiled-home-card"'));
  check("the layout key is the Control's own (articontrol_layout), the Hub's artiled_layout never named in code",
        CODE.includes('"articontrol_layout"') && !CODE.includes('"artiled_layout"'));
  check("the `artiled` iconset is never read or written",
        !/customIconsets\.artiled\b/.test(CODE) && !/customIconsets\[["']artiled["']\]/.test(CODE));
  check("own iconset `articontrol` registered", typeof solo.window.customIconsets.articontrol === "function");
  check("icon cache path is the integration's ICON_URL",
        SOURCE.includes('"/artiled_control_icons/fa-icons.json"') &&
        fs.readFileSync(path.join(ROOT, "custom_components/artiled_control/const.py"), "utf8")
          .includes('ICON_BASE = "/artiled_control_icons"'));
  check("customCards carry only artiled-control-* entries",
        solo.window.customCards.every((c) => c.type.startsWith("artiled-control-")));
  const logo = await solo.window.customIconsets.articontrol("logo");
  check("articontrol:logo is the measured mark", /A9\.61,9\.61/.test(logo.path) && logo.viewBox === "0 0 24 24");
  const tv = await solo.window.customIconsets.articontrol("fa-tv");
  check("articontrol:fa-tv resolves from the CONTROL's cache", tv.path === CONTROL_ICONS.tv[0]);
  const unknown = await solo.window.customIconsets.articontrol("fa-nothing-like-it");
  check("an unknown icon falls back to the mark, not an empty box", unknown.path === logo.path);

  /* ---------------------------------------------------------------- */
  console.log("\nnext to the ArtiLED (Hub) dashboard, in BOTH load orders");
  const hub_present = fs.existsSync(HUB_SRC);
  check(`the Hub module is available for the side-by-side check (${HUB_SRC})`, hub_present,
        "set HUB_HA_DIR to the ArtiLED_Hub_HA checkout");
  if (hub_present) {
    for (const order of [["hub", "control"], ["control", "hub"]]) {
      const shared = { registry: new Registry("native"), window: {}, intervals: [] };
      const ctxs = {};
      for (const which of order) {
        ctxs[which] = load(which === "hub" ? HUB_SRC : SRC, shared);
      }
      const label = order.join(" then ");
      const all = [...HUB_TAGS, "artiled-control-unit-card", "artiled-control-player-card",
                   "artiled-control-voice-card", "ll-strategy-dashboard-artiled-control"];
      check(`${label}: all nine elements defined, none lost`, all.every((tag) => shared.registry.get(tag)));
      check(`${label}: each tag belongs to its own module`,
            shared.registry.get("ll-strategy-dashboard-artiled-hub").name === "ArtiLedDashboardStrategy" &&
            shared.registry.get("ll-strategy-dashboard-artiled-control").name === "ArtiControlDashboardStrategy" &&
            shared.registry.get("artiled-voice-card").name === "ArtiLedVoiceCard" &&
            shared.registry.get("artiled-control-voice-card").name === "ArtiControlVoiceCard");
      const couch = await shared.window.customIconsets.artiled("fa-couch");
      check(`${label}: the Hub's room icon (artiled:fa-couch) still renders from the HUB's cache`,
            couch.path === HUB_ICONS.couch[0], JSON.stringify(couch));
      const hub_tv = await shared.window.customIconsets.artiled("fa-tv");
      check(`${label}: the Hub's iconset does not read the Control's cache`, hub_tv.path !== CONTROL_ICONS.tv[0]);
      const ctl_tv = await shared.window.customIconsets.articontrol("fa-tv");
      check(`${label}: articontrol:fa-tv resolves from the Control's cache`, ctl_tv.path === CONTROL_ICONS.tv[0]);
      const ctl_couch = await shared.window.customIconsets.articontrol("fa-couch");
      check(`${label}: the Control's iconset does not read the Hub's cache`, ctl_couch.path !== HUB_ICONS.couch[0]);
      const types = shared.window.customCards.map((c) => c.type);
      check(`${label}: both modules' picker entries present`,
            types.includes("artiled-tile-card") && types.includes("artiled-control-unit-card"));
      /* Home Assistant swaps window.customElements for its scoped registry
         after both modules loaded: each module's watcher re-defines on it. */
      const live = new Registry("scoped");
      Object.values(ctxs).forEach((c) => { c.customElements = live; });
      shared.intervals.forEach((fn) => fn());
      check(`${label}: after the registry swap both modules define on the live registry`,
            all.every((tag) => live.get(tag)));
    }
  }

  /* ---------------------------------------------------------------- */
  console.log("\nthe tile's rules, ported from tiles.php / paint.js / units.php");
  const st = (state, a) => ({ state, attributes: a || {} });
  const acts = [{ id: 11, name: "Netflix", icon: "fa-solid fa-tv" }, { id: 12, name: "Radio", icon: "fa-solid fa-radio" }];
  check("classify: a zone device", JSON.stringify(t.classify({ identifiers: [["artiled_control", "c0ffee000001_zone_3"]] })) ===
        JSON.stringify({ kind: "zone", id: 3, ident: "c0ffee000001" }));
  check("classify: a room device", t.classify({ identifiers: [["artiled_control", "c0ffee000001_room_7"]] }).kind === "room");
  check("classify: a Control device's remote is a device, never a second Control",
        JSON.stringify(t.classify({ identifiers: [["artiled_control", "c0ffee000001_device_7"]] })) ===
        JSON.stringify({ kind: "device", id: 7, ident: "c0ffee000001" }));
  check("classify: the Control device", JSON.stringify(t.classify({ identifiers: [["artiled_control", "c0ffee000001"]] })) ===
        JSON.stringify({ kind: "control", id: 0, ident: "c0ffee000001" }));
  check("classify: a Hub device is not ours", t.classify({ identifiers: [["artiled_hub", "x_room_1"]] }) === null);
  check("classify: an ident with underscores keeps them", t.classify({ identifiers: [["artiled_control", "a_b_zone_9"]] }).ident === "a_b");

  check("stable activity: status is its name", t.tile_status(st("playing", { source: "Netflix" })).text === "Netflix");
  check("on without a name: On", t.tile_status(st("on", {})).text === "On");
  check("off: Off", t.tile_status(st("off", {})).text === "Off");
  const sB = t.tile_status(st("on", { starting: true, starting_name: "Radio", source: "Radio" }));
  check("starting B: '<B> · Starting' with moving dots", sB.text === "Radio · Starting" && sB.starting === true);
  const sOff = t.tile_status(st("off", { starting: true, starting_name: "Off" }));
  check("starting Off: 'Off · Starting' (the off look)", sOff.text === "Off · Starting" && sOff.starting);
  check("unavailable: Unavailable", t.tile_status(st("unavailable", {})).text === "Unavailable" &&
        t.tile_status(undefined).text === "Unavailable");

  const running = { activity_id: 11, activities: acts, unit_icon: "fa-solid fa-couch" };
  check("icon: the running activity's while on", t.tile_icon(st("playing", running)) === "fa-solid fa-tv");
  check("icon: the unit's own when off", t.tile_icon(st("off", running)) === "fa-solid fa-couch");
  check("icon: the unit's own while starting (units.php blanks activity_icon)",
        t.tile_icon(st("on", { ...running, starting: true })) === "fa-solid fa-couch");
  check("art: the HA proxy picture while on", t.tile_art(st("playing", { entity_picture: "/api/media_player_proxy/x" })) === "/api/media_player_proxy/x");
  check("art: none while starting", t.tile_art(st("on", { entity_picture: "/p", starting: true })) === "");
  check("art: none when off", t.tile_art(st("off", { entity_picture: "/p" })) === "");
  check("wash: art / on / nothing", t.tile_wash(st("playing", { entity_picture: "/p" })) === "art" &&
        t.tile_wash(st("on", {})) === "on" && t.tile_wash(st("off", {})) === "");

  console.log("\nvolume: never a guessed level (G2), the device's own number");
  const vk = t.volume_view(st("on", { supported_features: 4, volume_level: 0.42, volume_display: "-38.0 dB", volume_max: 98, volume_scale: "db" }));
  check("known level: shown at its fraction", vk.shown && vk.known && vk.p === 0.42 && vk.display === "-38.0 dB");
  const vu = t.volume_view(st("on", { supported_features: 4 }));
  check("unknown level: shown greyed, knob in the middle, no number", vu.shown && !vu.known && vu.p === 0.5 && vu.display === "");
  check("off: no slider", !t.volume_view(st("off", { supported_features: 4, volume_level: 0.4 })).shown);
  check("nothing routes volume: no slider", !t.volume_view(st("on", { supported_features: 0 })).shown);
  check("level_for_percent rounds half to even, as artiled_steps.py",
        t.level_for_percent(50, 9) === 4 && t.level_for_percent(50, 11) === 6 && t.level_for_percent(70, 98) === 69);
  check("volume_text: dB scale", t.volume_text(70, "db") === "-10.0 dB" && t.volume_text(85, "db") === "+5.0 dB");
  check("volume_text: absolute", t.volume_text(42, "absolute") === "42");
  check("no maximum known: no number at all (never a % presented as the volume)", t.slider_value_text(70, 0, "db") === "");
  check("with a maximum: the device's own number", t.slider_value_text(50, 160, "db") === "0.0 dB" &&
        t.slider_value_text(70, 98, "absolute") === "69");

  console.log("\nthe remote's keys: only those the activity uses (#570)");
  const toggle = { buttons: ["play_pause", "next", "previous"], play_pause: "toggle" };
  const separate = { buttons: ["play", "pause", "stop"], play_pause: "separate" };
  check("null entry: every key, never the toggle", t.key_shown("play", null) && t.key_shown("stop", null) && !t.key_shown("play_pause", null));
  check("toggle activity: one Play/Pause instead of Play and Pause",
        t.key_shown("play_pause", toggle) && !t.key_shown("play", toggle) && !t.key_shown("pause", toggle));
  check("separate activity: only its keys", t.key_shown("play", separate) && t.key_shown("stop", separate) && !t.key_shown("next", separate));
  check("power always shows", t.key_shown("power", separate));
  const all_features = 1 | 16 | 32 | 4096 | 16384 | 8 | 4;
  check("transport follows buttons", JSON.stringify(t.transport_keys(st("playing", { buttons: toggle.buttons, play_pause: "toggle", supported_features: all_features }))) ===
        JSON.stringify(["previous", "play_pause", "next"]));
  check("transport: no buttons field -> every separate key",
        JSON.stringify(t.transport_keys(st("playing", { supported_features: all_features }))) ===
        JSON.stringify(["previous", "stop", "play", "pause", "next"]));
  check("transport: a key the entity does not support is not drawn",
        !t.transport_keys(st("playing", { supported_features: 16384 })).includes("next"));
  check("transport: none while off or starting",
        t.transport_keys(st("off", { supported_features: all_features })).length === 0 &&
        t.transport_keys(st("on", { supported_features: all_features, starting: true })).length === 0);
  check("mute: offered with VOLUME_MUTE and mute_toggle", t.mute_shown(st("on", { supported_features: 8 })) &&
        !t.mute_shown(st("on", { supported_features: 8, buttons: ["play"] })) && !t.mute_shown(st("on", { supported_features: 0 })));

  console.log("\nNow Playing");
  const w = t.np_words(st("playing", { media_title: "Song", media_artist: "Artist", media_album_name: "Album" }));
  check("music: title over 'artist · album'", w.title === "Song" && w.meta === "Artist · Album");
  check("film: title alone", t.np_words(st("paused", { media_title: "Movie" })).meta === "");
  check("nothing while starting", t.np_words(st("on", { media_title: "Old", starting: true })).title === "");
  const now = Date.parse("2026-10-26T12:00:10Z");
  const pos = t.np_position(st("playing", { media_duration: 200, media_position: 30, media_position_updated_at: "2026-10-26T12:00:00Z" }), now);
  check("position moves on while playing", pos && Math.round(pos.position) === 40 && pos.duration === 200);
  const pp = t.np_position(st("paused", { media_duration: 200, media_position: 30, media_position_updated_at: "2026-10-26T12:00:00Z" }), now);
  check("position stands while paused", pp && pp.position === 30);
  check("no duration: no bar", t.np_position(st("playing", { media_position: 3 }), now) === null);
  check("clock", t.clock(65) === "1:05" && t.clock(3725) === "1:02:05" && t.clock(0) === "0:00");

  console.log("\nshuffle, repeat, seek (S3c, playback.js)");
  const pm = t.play_modes(st("playing", { activity_id: 11, supported_features: 2 | 32768 | 262144, shuffle: true, repeat: "one", media_duration: 200 }));
  check("what the player said, while the entity offers it", pm.shuffle === true && pm.repeat === "one" && pm.can_seek === true);
  const pn = t.play_modes(st("playing", { activity_id: 11, supported_features: 0, shuffle: true, repeat: "one", media_duration: 200 }));
  check("not offered: unknown, never shown as off", pn.shuffle === null && pn.repeat === null && pn.can_seek === false);
  check("off: nothing", t.play_modes(st("off", { supported_features: 2 | 32768 | 262144, shuffle: false, repeat: "off" })).repeat === null);
  check("seek_to: whole seconds within the track (seekSeconds)",
        t.seek_to(0.5, 200) === 100 && t.seek_to(1.5, 200) === 200 && t.seek_to(-1, 200) === 0 && t.seek_to(0.333, 61) === 20);

  console.log("\nactivities: start-only, the running one lit, a starting one 'Starting'");
  const rows = t.activity_rows(st("playing", running));
  check("rows mirror the offered activities", rows.length === 2 && rows[0].name === "Netflix" && rows[0].running && !rows[1].running);
  const rs = t.activity_rows(st("on", { activity_id: 12, activities: acts, starting: true }));
  check("starting row: running look + starting", rs[1].running && rs[1].starting && !rs[0].running);
  check("off: no row running", t.activity_rows(st("off", running)).every((r) => !r.running));
  check("Off row only when the unit has an Off activity", t.off_row(st("off", { off_activity: 0 })) === null &&
        t.off_row(st("off", { off_activity: 5 })).running === true);
  check("Off row: starting while the Off activity starts", t.off_row(st("off", { off_activity: 5, starting: true })).starting === true);
  check("Off row: not lit while on", t.off_row(st("playing", { off_activity: 5 })).running === false);
  check("Off row: not lit while unavailable", t.off_row(st("unavailable", { off_activity: 5 })).running === false);

  console.log("\nvoice names agree with voice.py");
  const VOICE_CASES = [["Netflix", "Living Room"], [" Radio ", " Kitchen "], ["Listen to Sonos", "Kitchen"],
                       ["Watch Apple TV", "Cinema"], ["Netflix", "The Den"], ["Play PS5", "Game Room"]];
  const py = spawnSync("python3", ["-c", [
    "import importlib.util, json, sys",
    `spec = importlib.util.spec_from_file_location('v', ${JSON.stringify(path.join(ROOT, "custom_components/artiled_control/voice.py"))})`,
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)",
    "print(json.dumps([m.default_spoken_name(a, u) for a, u in json.loads(sys.argv[1])]))",
  ].join("\n"), JSON.stringify(VOICE_CASES)]);
  const want = py.status === 0 ? JSON.parse(String(py.stdout)) : null;
  check("voice_suggest == voice.default_spoken_name (same cases, two languages)",
        want && VOICE_CASES.every(([a, u], i) => want[i] === t.voice_suggest(a, u)), String(py.stderr));
  const pyl = spawnSync("python3", ["-c", [
    "import importlib.util, json, sys",
    `spec = importlib.util.spec_from_file_location('v', ${JSON.stringify(path.join(ROOT, "custom_components/artiled_control/voice.py"))})`,
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)",
    "print(json.dumps([m.long_spoken_name(a, u) for a, u in json.loads(sys.argv[1])]))",
  ].join("\n"), JSON.stringify(VOICE_CASES)]);
  const want_long = pyl.status === 0 ? JSON.parse(String(pyl.stdout)) : null;
  check("voice_suggest_long == voice.long_spoken_name (same cases, two languages)",
        want_long && VOICE_CASES.every(([a, u], i) => want_long[i] === t.voice_suggest_long(a, u)), String(pyl.stderr));
  check("suggest_name: the short form while free, the verb kept when it is taken",
        t.suggest_name("Listen to Zidoo", "Kitchen", () => false) === "Zidoo in the Kitchen" &&
        t.suggest_name("Watch Zidoo", "Kitchen", (n) => n.toLowerCase() === "zidoo in the kitchen") === "Watch Zidoo in the Kitchen");
  check("aliases: HA's computed-name null is not shown",
        t.aliases_shown([null, "Telly room", ""]) === "Telly room");
  check("aliases: a save keeps the computed-name null (else the spoken name stops working)",
        JSON.stringify(t.aliases_to_save("Telly room, Den", [null, "Telly room"])) === JSON.stringify([null, "Telly room", "Den"]));
  check("aliases: an entry that had no null is saved as typed",
        JSON.stringify(t.aliases_to_save("Den", ["Old"])) === JSON.stringify(["Den"]));
  check("aliases: never read -> the null is kept (HA's default for an entry)",
        JSON.stringify(t.aliases_to_save("Den", undefined)) === JSON.stringify([null, "Den"]));
  check("voice_suggest: the activity's verb dropped, 'the' before the room (Michel 2026-10-26)",
        t.voice_suggest("Listen to Sonos", "Kitchen") === "Sonos in the Kitchen" &&
        t.voice_suggest("Watch Apple TV", "Cinema") === "Apple TV in the Cinema" &&
        t.voice_suggest("Netflix", "The Den") === "Netflix in The Den" &&
        t.voice_suggest("Watch", "Kitchen") === "Watch in the Kitchen");

  check("unit_path: one Control", t.unit_path({ kind: "zone", id: 3, ident: "c0ffee000001" }, false) === "zone-3");
  check("unit_path: several Controls carry the ident's tail",
        t.unit_path({ kind: "zone", id: 3, ident: "c0ffee000001" }, true) === "zone-3-000001");
  [["fas fa-couch", "couch"], ["fa-solid fa-tv", "tv"], ["tv", "tv"], ["", ""], [undefined, ""]].forEach(([i, o]) =>
    check(`fa_name(${JSON.stringify(i)}) -> ${JSON.stringify(o)}`, t.fa_name(i) === o));
  check("icon_name: served -> articontrol:fa-*, else the fallback",
        t.icon_name("fa-solid fa-tv", CONTROL_ICONS, "mdi:x") === "articontrol:fa-tv" &&
        t.icon_name("fa-solid fa-nope", CONTROL_ICONS, "mdi:x") === "mdi:x");
  check("error_text: the integration's words pass through", t.error_text(new Error("No Off activity for Kitchen")) === "No Off activity for Kitchen");
  check("error_text: no words -> a remedy", /Check that it is on/.test(t.error_text({})));

  /* ---------------------------------------------------------------- */
  console.log("\nthe strategy: units by device identifier, Control order, offered only");
  const IDENT = "c0ffee000001";
  const IDENT2 = "beef00000002";
  const devices = [
    { id: "dC", identifiers: [["artiled_control", IDENT]], name: "Control" },
    { id: "dK", identifiers: [["artiled_control", `${IDENT}_room_7`]], name: "Kitchen" },
    { id: "dW", identifiers: [["artiled_control", `${IDENT}_zone_3`]], name: "Cinewall" },
    { id: "dE", identifiers: [["artiled_control", `${IDENT}_room_9`]], name: "Empty" },
    { id: "dH", identifiers: [["artiled_hub", "hub_room_1"]], name: "Hub room" },
  ];
  const entities = [
    { entity_id: "media_player.kitchen", platform: "artiled_control", device_id: "dK", unique_id: `${IDENT}_room_7` },
    { entity_id: "media_player.cinewall", platform: "artiled_control", device_id: "dW", unique_id: `${IDENT}_zone_3` },
    /* ⚠ ON NO DEVICE (HA 2026.10 would prefix the device name): found by
       its unique_id, never by device or by its user-named entity_id. */
    { entity_id: "scene.netflix_on_the_wall", platform: "artiled_control", device_id: null, unique_id: `${IDENT}_voice_activity_zone_3_11` },
    { entity_id: "scene.someone_elses", platform: "other", device_id: null, unique_id: `${IDENT}_voice_activity_zone_3_12` },
    { entity_id: "sensor.control_version", platform: "artiled_control", device_id: "dC", unique_id: `${IDENT}_version` },
    { entity_id: "light.hub_room", platform: "artiled_hub", device_id: "dH", unique_id: "hub_room_1" },
  ];
  const states = {
    "media_player.kitchen": st("off", { unit_kind: "room", unit_id: 7, unit_order: 4, activities: [{ id: 21, name: "Radio", icon: "" }], friendly_name: "Kitchen" }),
    "media_player.cinewall": st("playing", { unit_kind: "zone", unit_id: 3, unit_order: 1, unit_icon: "fa-solid fa-tv", activities: acts, friendly_name: "Cinewall",
      plays_music: true, plays_movies: true }),
    "scene.netflix_on_the_wall": st("2026-10-26T10:00:00Z", { artiled_unit: "zone_3", artiled_activity: 11, friendly_name: "Netflix on the wall" }),
  };
  const controls = t.collect(entities, devices, states);
  check("one Control, named from its device", controls.length === 1 && controls[0].name === "Control");
  check("units in Control order (unit_order), not registry or name order",
        controls[0].units.map((u) => u.entity).join(",") === "media_player.cinewall,media_player.kitchen");
  check("a unit device without a media_player (not offered) is not listed",
        !controls[0].units.some((u) => u.device_id === "dE"));
  check("Hub devices are ignored", controls[0].units.every((u) => u.kind !== "hub"));
  check("a chosen voice activity (on no device) is found by its unique_id",
        JSON.stringify(controls[0].units[0].chosen) ===
        JSON.stringify([{ activity: 11, name: "Netflix on the wall", entity: "scene.netflix_on_the_wall" }]));

  const views = t.build_views(controls, { dashboard_path: "artiled-control", admin: true, have: CONTROL_ICONS });
  const home = views.find((v) => v.path === "home");
  const home_cards = home.sections[0].cards;
  /* 0.3.19 (Michel 2026-10-26): "first page only Rooms/Zones" - the activities
     only once a room is opened; and the webapp's Grid view */
  check("Home: ONE home card holding the list (the Hub's artiled-home-card), full width",
        home.sections.length === 1 && home_cards.length === 1 && home_cards[0].type === "custom:artiled-control-home-card" &&
        home_cards[0].grid_options.columns === "full");
  const hgroups = home_cards[0].groups;
  check("Home: one list, no heading with one Control (the webapp's Home has none)",
        hgroups.length === 1 && !hgroups[0].heading);
  check("Home: a tile per offered unit, in order, linking to its page",
        hgroups[0].tiles.map((c) => `${c.entity}|${c.navigate}`).join(";") ===
        "media_player.cinewall|/artiled-control/zone-3;media_player.kitchen|/artiled-control/room-7");
  check("Home: the tiles carry NO activity buttons (only on the room's own page)",
        hgroups[0].tiles.every((c) => c.activities === false));
  check("Home: no layout fixed unless the strategy says so", !("layout" in home_cards[0]));
  const fixed_views = t.build_views(controls, { dashboard_path: "artiled-control", admin: false, have: {}, layout: "list" });
  check("strategy `layout: list` fixes it for the dashboard",
        fixed_views.find((v) => v.path === "home").sections[0].cards[0].layout === "list");
  const odd_views = t.build_views(controls, { dashboard_path: "artiled-control", admin: false, have: {}, layout: "mosaic" });
  check("an unknown layout word is ignored (the switch decides)",
        !("layout" in odd_views.find((v) => v.path === "home").sections[0].cards[0]));
  const sub = views.find((v) => v.path === "zone-3");
  check("unit page: a subview back to Home", sub && sub.subview === true && sub.back_path === "/artiled-control/home");
  check("unit page: the unit's card without activity buttons, then the player",
        sub.sections[0].cards[0].type === "custom:artiled-control-unit-card" && sub.sections[0].cards[0].activities === false &&
        sub.sections[0].cards[1].type === "custom:artiled-control-player-card" && sub.sections[0].cards[1].entity === "media_player.cinewall");
  check("unit page (0.3.25): the house's favourites, played in THIS room, under the player",
        sub.sections[0].cards[2] && sub.sections[0].cards[2].type === "custom:artiled-control-favorites-card" &&
        sub.sections[0].cards[2].entity === "media_player.cinewall");
  check("unit page: a room that plays nothing has no favourites card",
        views.find((v) => v.path === "room-7").sections[0].cards.length === 2);
  check("unit page: the player knows its Browse pages", JSON.stringify(sub.sections[0].cards[1].browse) ===
        JSON.stringify({ music: "/artiled-control/zone-3-music", movies: "/artiled-control/zone-3-movies" }));
  const bmus = views.find((v) => v.path === "zone-3-music");
  const bmov = views.find((v) => v.path === "zone-3-movies");
  check("Browse Music / Browse Movies: pages of their own under the unit's page, with the back arrow (Michel 2026-10-26)",
        bmus && bmov && bmus.subview === true && bmus.back_path === "/artiled-control/zone-3" && bmus.type === "panel" &&
        bmus.cards[0].type === "custom:artiled-control-browse-card" && bmus.cards[0].kind === "music" && bmov.cards[0].kind === "movies");
  check("a room that plays nothing gets no Browse pages", !views.some((v) => v.path.startsWith("room-7-")) &&
        JSON.stringify(views.find((v) => v.path === "room-7").sections[0].cards[1].browse) === "{}");
  const voice = views.find((v) => v.path === "voice");
  const vcard = voice && voice.sections[0].cards[0];
  check("Voice page for an admin, with the Control's voice card", vcard && vcard.type === "custom:artiled-control-voice-card");
  check("Voice: rooms and zones apart, as the Hub's", vcard.rooms.map((r) => r.entity).join() === "media_player.kitchen" &&
        vcard.zones.map((r) => r.entity).join() === "media_player.cinewall");
  check("Voice: each unit's activities and its chosen ones",
        vcard.units[0].activities.length === 2 && vcard.units[0].chosen.length === 1 && vcard.units[0].icon === "articontrol:fa-tv");
  check("Voice: a unit icon the Control did not serve falls back to the kind's",
        vcard.units[1].icon === "mdi:sofa");
  const plain = t.build_views(controls, { dashboard_path: "artiled-control", admin: false, have: {} });
  check("no Voice page for a household member (its commands are admin-only)", !plain.some((v) => v.path === "voice"));

  const two_dev = devices.concat([
    { id: "dC2", identifiers: [["artiled_control", IDENT2]], name: "Annex" },
    { id: "dZ2", identifiers: [["artiled_control", `${IDENT2}_zone_3`]], name: "Bar" },
  ]);
  const two_ent = entities.concat([{ entity_id: "media_player.bar", platform: "artiled_control", device_id: "dZ2" }]);
  const two_states = { ...states, "media_player.bar": st("off", { unit_kind: "zone", unit_order: 0, activities: acts }) };
  const two = t.collect(two_ent, two_dev, two_states);
  const two_views = t.build_views(two, { dashboard_path: "artiled-control", admin: true, have: {} });
  const two_home = two_views.find((v) => v.path === "home");
  const two_groups = two_home.sections[0].cards[0].groups;
  check("two Controls: one list each, headed by the Control's name",
        two_home.sections.length === 1 && two_groups.length === 2 &&
        two_groups.map((g) => g.heading).join() === "Annex,Control");
  const zone3 = two_views.filter((v) => /^zone-3/.test(v.path || "") && !/-(music|movies)$/.test(v.path)).map((v) => v.path);
  check("two Controls: two zone 3s get two different pages", zone3.length === 2 && zone3[0] !== zone3[1]);
  check("two Controls: voice rows say which Control", two_views.find((v) => v.path === "voice").sections[0].cards[0]
    .zones.some((z) => z.name === "Bar (Annex)"));

  const fake_hass = (ents, devs, sts, admin) => ({
    callWS: ({ type }) => Promise.resolve(type === "config/entity_registry/list" ? ents : devs),
    states: sts, user: { is_admin: admin },
  });
  const gen = await t.ArtiControlDashboardStrategy.generate({}, fake_hass(entities, devices, states, true));
  check("generate(): Home first, then the unit pages, Voice last",
        gen.views.map((v) => v.path).join() === "home,zone-3,zone-3-music,zone-3-movies,room-7,voice");
  const none = await t.ArtiControlDashboardStrategy.generate({}, fake_hass([], [], {}, true));
  check("generate(): no integration -> how to add it", /Add integration/.test(none.views[0].cards[0].content));
  const empty = await t.ArtiControlDashboardStrategy.generate({},
    fake_hass([entities.find((e) => e.entity_id === "sensor.control_version")], [devices[0]], {}, true));
  check("generate(): a Control with no offered unit -> says how a room appears",
        /appears here by itself/.test(empty.views[0].cards[0].content));
  const pinned = await t.ArtiControlDashboardStrategy.generate({ dashboard_path: "my-av" }, fake_hass(entities, devices, states, false));
  check("generate(): dashboard_path overrides the URL's", pinned.views[0].sections[0].cards[0].groups[0].tiles[0].navigate === "/my-av/zone-3");
  const gen_fixed = await t.ArtiControlDashboardStrategy.generate({ layout: "square" }, fake_hass(entities, devices, states, false));
  check("generate(): the strategy's `layout:` reaches the home card", gen_fixed.views[0].sections[0].cards[0].layout === "square");

  /* ---------------------------------------------------------------- */
  console.log("\nthe /unit_find answers in the Control's REAL shapes (music_answer.php, music_heos.php)");
  const live_acc = t.find_accounts({ accounts: { tidal: true, apple: false, sonoslib: true, sonos: { spotify: true },
    roon: { tidal: true, qobuz: true }, heos: { sources: [{ sid: 10 }], favorites: false } }, spotify: true, kept: true });
  check("accounts: the flags read from answer.accounts (Michel: no TIDAL / Qobuz tabs)",
        live_acc.accounts.roon.qobuz === true && live_acc.accounts.sonoslib === true && live_acc.spotify === true && live_acc.known);
  check("accounts: a flat answer still reads", t.find_accounts({ roon: { tidal: true } }).accounts.roon.tidal === true);
  check("accounts: nothing kept yet = not known (the fresh read decides)", !t.find_accounts({ accounts: {}, spotify: false, kept: false }).known);
  check("heos_account: answer.account", t.heos_account({ account: { favorites: true, sources: [] }, error: "" }).favorites === true &&
        t.heos_account({ favorites: true }).favorites === true);

  /* ---------------------------------------------------------------- */
  console.log("\ntile layouts - the webapp's Settings > Layout (app/js/layout.js, app.css, tablet.css)");
  check("the webapp's four layouts, in its order; Grid the default",
        JSON.stringify(t.LAYOUTS) === JSON.stringify(["rows", "grid", "square", "list"]) && t.LAYOUT_DEFAULT === "grid");
  check("layout_word: an unknown or missing word is Grid; 'compact' reads as Square",
        t.layout_word("mosaic") === "grid" && t.layout_word(null) === "grid" && t.layout_word("compact") === "square" &&
        t.layout_word("list") === "list");
  const cols = (l, w, land) => t.layout_columns(l, w, land).cols;
  check("phone: Rows 1, Grid 2, Square 2, List 1 (app.css)",
        [cols("rows", 360), cols("grid", 360), cols("square", 360), cols("list", 360)].join() === "1,2,2,1");
  check("tablet portrait: Rows 2, Grid 4, Square 4, List 2 (tablet.css)",
        [cols("rows", 720), cols("grid", 720), cols("square", 720), cols("list", 720)].join() === "2,4,4,2");
  check("tablet landscape: Grid 6, Square 6, Rows/List 2 - the CONTROL's tablet.css (no Hub 5-across step)",
        [cols("grid", 944, true), cols("grid", 1300, true), cols("square", 944, true), cols("rows", 944, true), cols("list", 944, true)].join() === "6,6,6,2,2");
  check("a wide list in portrait keeps the portrait counts", cols("grid", 1200, false) === 4);

  /* ---------------------------------------------------------------- */
  await dom_tests(states);

  console.log(FAILURES.length ? `\n${FAILURES.length} FAILED of ${checks}: ${FAILURES.join(", ")}`
                              : `\nall frontend rules pass (${checks} checks)`);
  process.exit(FAILURES.length ? 1 : 0);
}

/* ------------------------------------------------------------------ *
 * The cards, rendered in jsdom.
 * ------------------------------------------------------------------ */
async function dom_tests() {
  console.log("\nthe cards in a DOM (jsdom)");
  let JSDOM;
  try {
    ({ JSDOM } = require(JSDOM_PATH));
  } catch (e) {
    check(`jsdom is available at ${JSDOM_PATH}`, false, "set JSDOM_PATH");
    return;
  }
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { runScripts: "outside-only", url: "http://ha.local/artiled-control/home" });
  const win = dom.window;
  const intervals = [];
  win.setInterval = (fn) => { intervals.push(fn); return intervals.length; };
  win.clearInterval = () => {};
  const scrolled = [];
  win.scrollTo = (x, y) => { scrolled.push(y); };
  win.fetch = fake_fetch;
  win.eval(SOURCE + "\n;window.__t = {ArtiControlUnitCard, ArtiControlPlayerCard, ArtiControlVoiceCard};\n");
  const settle = () => new Promise((r) => setTimeout(r, 20));

  const calls = [];
  let expose_list = null;
  const notes = [];
  const make_hass = (states, fail) => ({
    states,
    user: { is_admin: true },
    callService: (domain, service, data) => {
      calls.push([domain, service, data]);
      return fail ? Promise.reject(new Error(fail)) : Promise.resolve();
    },
    callWS: (msg) => {
      calls.push(["ws", msg.type, msg]);
      if (msg.type === "config/entity_registry/get") {
        return Promise.resolve({ options: { conversation: { should_expose: true } }, aliases: [null, "Telly room"] });
      }
      if (msg.type === "homeassistant/expose_entity/list" && expose_list) return Promise.resolve(expose_list);
      return Promise.resolve({});
    },
  });
  const acts = [{ id: 11, name: "Netflix", icon: "fa-solid fa-tv" }, { id: 12, name: "Radio", icon: "fa-solid fa-radio" }];
  const playing = {
    state: "playing",
    attributes: {
      friendly_name: "Cinewall", unit_kind: "zone", room: "Cinema", unit_icon: "fa-solid fa-couch",
      activity_id: 11, starting: false, off_activity: 5, on_activity: 11, activities: acts, source: "Netflix",
      supported_features: 1 | 4 | 8 | 16 | 32 | 4096 | 16384 | 128 | 256 | 2048 | 1024,
      volume_level: 0.5, volume_display: "-30.0 dB", volume_max: 100, volume_scale: "db",
      buttons: ["play_pause", "next", "previous", "mute_toggle"], play_pause: "toggle",
      media_title: "A Film", entity_picture: "/api/media_player_proxy/media_player.cinewall?token=abc",
      is_volume_muted: true,
    },
  };

  /* --- the unit card --------------------------------------------- */
  const card = win.document.createElement("artiled-control-unit-card");
  card.setConfig({ entity: "media_player.cinewall", navigate: "/artiled-control/zone-3" });
  win.document.body.appendChild(card);
  card.addEventListener("hass-notification", (e) => notes.push(e.detail.message));
  card.hass = make_hass({ "media_player.cinewall": playing });
  await settle();
  card.hass = make_hass({ "media_player.cinewall": playing });
  const root = card.shadowRoot;
  check("tile: name with the zone's room as caption", root.querySelector(".n").textContent === "Cinewall" &&
        root.querySelector(".caption").textContent === "Cinema");
  check("tile: status is the running activity", root.querySelector(".st").textContent === "Netflix");
  check("tile: the activity's icon from the Control's cache", root.querySelector(".icon ha-icon").getAttribute("icon") === "articontrol:fa-tv");
  check("tile: switch on", root.querySelector(".sw").classList.contains("on"));
  const wash = root.querySelector(".wash");
  check("tile: artwork wash through Home Assistant's proxy", wash && wash.classList.contains("art") &&
        /media_player_proxy/.test(wash.style.backgroundImage));
  check("tile: volume slider shown at the level", !root.querySelector(".slider").hidden &&
        root.querySelector(".knob").style.getPropertyValue("--p") === "0.5");
  const buttons = Array.from(root.querySelectorAll(".act"));
  check("tile: the activities directly as buttons, plus Off", buttons.length === 3 &&
        buttons[0].textContent.includes("Netflix") && buttons[2].dataset.off === "1");
  check("tile: the running activity's button lit, Off not", buttons[0].classList.contains("running") && !buttons[2].classList.contains("running"));

  calls.length = 0;
  buttons[1].click();
  await settle();
  check("tile: an activity button STARTS that activity by id, through the unit's entity",
        JSON.stringify(calls) === JSON.stringify([["artiled_control", "start_activity", { entity_id: "media_player.cinewall", activity: 12 }]]));
  calls.length = 0;
  root.querySelectorAll(".act")[2].click();
  await settle();
  check("tile: Off = the unit's turn_off (its Off activity)",
        JSON.stringify(calls) === JSON.stringify([["media_player", "turn_off", { entity_id: "media_player.cinewall" }]]));
  calls.length = 0;
  root.querySelector(".sw").click();
  await settle();
  check("tile: the switch turns an on unit off", JSON.stringify(calls[0]) === JSON.stringify(["media_player", "turn_off", { entity_id: "media_player.cinewall" }]));
  check("tile: no toast when it worked (toasts only on errors)", notes.length === 0);

  let navigated = null;
  win.addEventListener("location-changed", () => { navigated = win.location.pathname; });
  root.querySelector(".tile").click();
  check("tile: a tap opens the unit page", navigated === "/artiled-control/zone-3");

  const off_state = { state: "off", attributes: { ...playing.attributes, activity_id: 0, source: null, entity_picture: null } };
  card.hass = make_hass({ "media_player.cinewall": off_state }, "No On activity for Cinewall");
  check("tile off: Off, the unit's icon (its kind's when the Control did not serve it), no wash, no slider",
        root.querySelector(".st").textContent === "Off" &&
        root.querySelector(".icon ha-icon").getAttribute("icon") === "mdi:vector-rectangle" &&
        !root.querySelector(".wash") && root.querySelector(".slider").hidden);
  check("tile off: the Off button lit", root.querySelectorAll(".act")[2].classList.contains("running"));
  root.querySelector(".sw").click();
  await settle();
  check("tile: a refused command toasts the integration's words", notes[notes.length - 1] === "No On activity for Cinewall");
  check("tile: and marks the switch failed", root.querySelector(".sw").classList.contains("failed"));

  const starting = { state: "on", attributes: { ...playing.attributes, activity_id: 12, starting: true, starting_name: "Radio", entity_picture: null } };
  card.hass = make_hass({ "media_player.cinewall": starting });
  check("tile starting: '<B> · Starting' with the moving dots", root.querySelector(".st").textContent === "Radio · Starting" &&
        !root.querySelector(".status .dots").hidden);
  check("tile starting: the starting activity's button has the active look",
        root.querySelectorAll(".act")[1].classList.contains("running") && root.querySelectorAll(".act")[1].querySelector(".dots"));

  const unknown_vol = { state: "on", attributes: { ...playing.attributes, volume_level: undefined, volume_display: null, entity_picture: null } };
  card.hass = make_hass({ "media_player.cinewall": unknown_vol });
  check("tile: an unknown level greys the slider with the knob in the middle",
        root.querySelector(".slider").classList.contains("unknown") && root.querySelector(".knob").style.getPropertyValue("--p") === "0.5");

  card.hass = make_hass({ "media_player.cinewall": { state: "unavailable", attributes: playing.attributes } });
  check("tile unavailable: dimmed, buttons disabled", root.querySelector(".tile").classList.contains("unavail") &&
        Array.from(root.querySelectorAll(".act")).every((b) => b.disabled));

  const bare = win.document.createElement("artiled-control-unit-card");
  bare.setConfig({ entity: "media_player.cinewall", activities: false });
  win.document.body.appendChild(bare);
  bare.hass = make_hass({ "media_player.cinewall": playing });
  check("unit-page tile: no activity buttons", !bare.shadowRoot.querySelector(".acts"));

  /* --- the home card (0.3.19: rooms/zones only, in the webapp's layouts) --- */
  const kitchen = { state: "off", attributes: { friendly_name: "Kitchen", unit_kind: "room", activities: acts, off_activity: 5, activity_id: 0 } };
  const home_states = { "media_player.cinewall": playing, "media_player.kitchen": kitchen };
  const home_cfg = (extra) => ({
    groups: [{ heading: "", tiles: [
      { entity: "media_player.cinewall", navigate: "/artiled-control/zone-3", activities: false },
      { entity: "media_player.kitchen", navigate: "/artiled-control/room-7", activities: false },
    ] }],
    ...(extra || {}),
  });
  try { win.localStorage.removeItem("articontrol_layout"); } catch (e) { /* none */ }
  /* the Hub dashboard's own choice in the same browser: never read, never written */
  win.localStorage.setItem("artiled_layout", "list");
  const hc = win.document.createElement("artiled-control-home-card");
  hc.setConfig(home_cfg());
  win.document.body.appendChild(hc);
  hc.hass = make_hass(home_states);
  await settle();
  const hr = hc.shadowRoot;
  const htiles = () => Array.from(hr.querySelectorAll(".app-tiles > artiled-control-unit-card"));
  check("home: a tile per room/zone, in order", htiles().map((c) => c._config.entity).join() === "media_player.cinewall,media_player.kitchen");
  check("home: no activity buttons on Home (Michel: only when you click a room)",
        htiles().every((c) => !c.shadowRoot.querySelector(".acts")));
  check("home: the tile still shows the room's state - the running activity and its cover",
        htiles()[0].shadowRoot.querySelector(".st").textContent === "Netflix" && !!htiles()[0].shadowRoot.querySelector(".wash.art"));
  check("home: Grid when its own key holds nothing - although the Hub's artiled_layout says List (nothing read or migrated)", htiles().every((c) => c.getAttribute("layout") === "grid") &&
        hr.querySelector(".app-tiles").dataset.layout === "grid");
  check("home: no section heading with one Control", !hr.querySelector(".app-section"));
  const lbtns = Array.from(hr.querySelectorAll(".layouts .tt-tile"));
  check("home: the layout switch, the webapp's four with their artwork, Grid chosen",
        lbtns.map((b) => b.dataset.layout).join() === "rows,grid,square,list" &&
        lbtns.every((b) => b.querySelector("svg")) && lbtns[1].classList.contains("is-running") &&
        lbtns[1].getAttribute("aria-checked") === "true");
  let hnav = null;
  const on_nav = () => { hnav = win.location.pathname; };
  win.addEventListener("location-changed", on_nav);
  htiles()[1].shadowRoot.querySelector(".tile").click();
  win.removeEventListener("location-changed", on_nav);
  check("home: a tap on a tile opens the room's page", hnav === "/artiled-control/room-7");
  calls.length = 0;
  htiles()[1].shadowRoot.querySelector(".sw").click();
  await settle();
  check("home: the tile's switch still works", JSON.stringify(calls[0]) === JSON.stringify(["media_player", "turn_on", { entity_id: "media_player.kitchen" }]));
  hr.querySelector('.tt-tile[data-layout="square"]').click();
  check("home: a switch press stores the choice under its OWN key (localStorage articontrol_layout)",
        win.localStorage.getItem("articontrol_layout") === "square");
  check("home: the Hub's artiled_layout is left exactly as it was", win.localStorage.getItem("artiled_layout") === "list");
  check("home: and the tiles follow at once", htiles().every((c) => c.getAttribute("layout") === "square") &&
        hr.querySelector('.tt-tile[data-layout="square"]').classList.contains("is-running") &&
        !hr.querySelector('.tt-tile[data-layout="grid"]').classList.contains("is-running"));
  const hc2 = win.document.createElement("artiled-control-home-card");
  hc2.setConfig(home_cfg());
  win.document.body.appendChild(hc2);
  hc2.hass = make_hass(home_states);
  check("home: a second home card opens in the stored layout", hc2.shadowRoot.querySelector(".app-tiles").dataset.layout === "square");
  hr.querySelector('.tt-tile[data-layout="list"]').click();
  check("home: a switch on one card moves every card on the page", hc2.shadowRoot.querySelector(".app-tiles").dataset.layout === "list");
  const hbox = hr.querySelector(".app-tiles");
  Object.defineProperty(hbox, "clientWidth", { configurable: true, get: () => 720 });
  hr.querySelector('.tt-tile[data-layout="grid"]').click();
  check("home: the columns follow the list's width (720 = tablet portrait, Grid 4)",
        hbox.style.getPropertyValue("--cols") === "4" && hbox.dataset.tier === "portrait");
  hc2.remove();
  hc.remove();
  const hf = win.document.createElement("artiled-control-home-card");
  hf.setConfig(home_cfg({ layout: "rows" }));
  win.document.body.appendChild(hf);
  hf.hass = make_hass(home_states);
  check("home: a strategy-fixed layout wins over the stored one, and no switch is drawn",
        !hf.shadowRoot.querySelector(".layouts") &&
        Array.from(hf.shadowRoot.querySelectorAll("artiled-control-unit-card")).every((c) => c.getAttribute("layout") === "rows"));
  hf.remove();
  const hm = win.document.createElement("artiled-control-home-card");
  hm.setConfig({ groups: [{ heading: "Annex", tiles: [{ entity: "media_player.kitchen", activities: false }] },
                          { heading: "Control", tiles: [{ entity: "media_player.cinewall", activities: false }] }] });
  win.document.body.appendChild(hm);
  hm.hass = make_hass(home_states);
  check("home: two Controls - a heading and a list each",
        Array.from(hm.shadowRoot.querySelectorAll(".app-section")).map((h) => h.textContent).join() === "Annex,Control" &&
        hm.shadowRoot.querySelectorAll(".app-tiles").length === 2);
  hm.remove();
  const real_ls = Object.getOwnPropertyDescriptor(win, "localStorage");
  Object.defineProperty(win, "localStorage", { configurable: true, get: () => { throw new Error("SecurityError"); } });
  const hx = win.document.createElement("artiled-control-home-card");
  hx.setConfig(home_cfg());
  win.document.body.appendChild(hx);
  let hx_ok = true;
  try { hx.hass = make_hass(home_states); } catch (e) { hx_ok = false; }
  check("home: storage refused -> still renders (in the choice made on this page, else Grid)",
        hx_ok && !!hx.shadowRoot.querySelector(".app-tiles"));
  hx.shadowRoot.querySelector('.tt-tile[data-layout="square"]').click();
  check("home: storage refused -> a switch press still holds on the page",
        hx.shadowRoot.querySelector(".app-tiles").dataset.layout === "square");
  hx.remove();
  if (real_ls) Object.defineProperty(win, "localStorage", real_ls);
  try { win.localStorage.removeItem("articontrol_layout"); win.localStorage.removeItem("artiled_layout"); } catch (e) { /* none */ }
  const tile_css = (htiles().length ? "" : "") + (() => {
    const u = win.document.createElement("artiled-control-unit-card");
    u.setConfig({ entity: "media_player.cinewall" });
    u.hass = make_hass(home_states);
    return u.shadowRoot.querySelector("style").textContent;
  })();
  check("tile CSS: Grid/Square hide the status line and the slider (app.css .layout-grid)",
        /:host\(\[layout="grid"\]\) \.status,[\s\S]*?\{\s*display: none;/.test(tile_css));
  check("tile CSS: the Control's Grid icon cap (calc(100% - 42px)) keeps the switch on the icon's line",
        /:host\(\[layout="grid"\]\) \.icon[\s\S]*?max-width: calc\(100% - 42px\)/.test(tile_css));
  check("tile CSS: Square is a square (aspect-ratio 1); List has no card, no wash",
        /:host\(\[layout="square"\]\) \.tile \{[^}]*aspect-ratio: 1/.test(tile_css) &&
        /:host\(\[layout="list"\]\) \.wash/.test(tile_css));
  check("tile CSS: List words in the theme's text colour (no dark card behind them)",
        /:host\(\[layout="list"\]\) \.name[\s\S]*?var\(--primary-text-color/.test(tile_css));

  /* --- the player card -------------------------------------------- */
  const player = win.document.createElement("artiled-control-player-card");
  player.setConfig({ entity: "media_player.cinewall" });
  win.document.body.appendChild(player);
  player.addEventListener("hass-notification", (e) => notes.push(e.detail.message));
  player.hass = make_hass({ "media_player.cinewall": playing });
  await settle();
  player.hass = make_hass({ "media_player.cinewall": playing });
  const pr = player.shadowRoot;
  check("player: Now Playing title", pr.querySelector(".now-title").textContent === "A Film" && !pr.querySelector(".now").hidden);
  check("player: the artwork behind it (the remote's .35 art)", !pr.querySelector(".art").hidden);
  const arows = Array.from(pr.querySelectorAll(".arow"));
  check("player: activity rows + the Off row", arows.length === 3 && arows[2].dataset.off === "1");
  check("player: the running row lit and 'On', the others 'Off'", arows[0].classList.contains("running") &&
        arows[0].querySelector(".asub").textContent === "On" && arows[1].querySelector(".asub").textContent === "Off");
  const keys = Array.from(pr.querySelectorAll(".transport .key")).map((k) => k.dataset.k);
  check("player: only the activity's keys, Play/Pause as one", keys.join() === "previous,play_pause,next");
  calls.length = 0;
  pr.querySelector('.transport .key[data-k="next"]').click();
  await settle();
  check("player: Next -> media_next_track", JSON.stringify(calls[0]) === JSON.stringify(["media_player", "media_next_track", { entity_id: "media_player.cinewall" }]));
  calls.length = 0;
  check("player: mute sits on the volume row, no line of its own (Michel 2026-10-26)",
        !!pr.querySelector(".vol-row .mutekey") && !pr.querySelector(".row.mute") && pr.querySelector(".vol-row").classList.contains("with-mute"));
  pr.querySelector(".vol-row .mutekey").click();
  await settle();
  check("player: mute when muted -> unmute (discrete, never a blind toggle)",
        JSON.stringify(calls[0]) === JSON.stringify(["media_player", "volume_mute", { entity_id: "media_player.cinewall", is_volume_muted: false }]));
  calls.length = 0;
  pr.querySelector(".step.up").click();
  await settle();
  check("player: the speaker steps the volume", JSON.stringify(calls[0]) === JSON.stringify(["media_player", "volume_up", { entity_id: "media_player.cinewall" }]));
  const order = Array.from(pr.querySelector(".remote").children).map((n) => n.className.split(" ").filter((c) => c !== "row" && c !== "with-mute")[0]);
  check("player: Michel's order (2026-10-26) - the whole cover with Now Playing and progress, then the keys, the volume with mute, then the rows",
        order.join() === "art,shortcuts,cover,now,progress,transport,vol-row,rows", order.join());
  /* Michel 2026-10-26: the WHOLE cover above the keys, not only the see-through background */
  const cover = pr.querySelector("img.cover");
  check("player: the whole album cover drawn above the keys, as the webapp's Now Playing picture",
        cover && !cover.hidden && cover.getAttribute("src") === playing.attributes.entity_picture &&
        /url\("?\/api\/media_player_proxy\/media_player\.cinewall\?token=abc"?\)/.test(pr.querySelector(".art").style.backgroundImage));
  check("player: uncropped - object-fit contain, at most 340 wide and 200 high (.app-remote__info-art)",
        /\.cover \{[^}]*object-fit: contain[^}]*\}/.test(pr.querySelector("style").textContent) &&
        /\.cover \{[^}]*max-height: 200px/.test(pr.querySelector("style").textContent));
  cover.dispatchEvent(new win.Event("error"));
  check("player: a cover that does not load hides (the background stays)", cover.hidden);
  const unknown_level = { state: "on", attributes: { ...playing.attributes, volume_level: undefined, volume_display: null } };
  player.hass = make_hass({ "media_player.cinewall": unknown_level });
  calls.length = 0;
  pr.querySelector(".step.up").click();
  pr.querySelector(".step.down").click();
  await settle();
  check("player: unknown level - greyed, and the speakers do nothing (as the webapp)",
        calls.length === 0 && pr.querySelector(".vol-row").classList.contains("unknown"));
  player.hass = make_hass({ "media_player.cinewall": playing });
  calls.length = 0;
  pr.querySelectorAll(".arow")[1].click();
  await settle();
  check("player: a row starts its activity by id", JSON.stringify(calls[0]) ===
        JSON.stringify(["artiled_control", "start_activity", { entity_id: "media_player.cinewall", activity: 12 }]));
  player.hass = make_hass({ "media_player.cinewall": off_state });
  check("player off: no transport, no volume, no Now Playing; Off lit",
        pr.querySelector(".transport").hidden && pr.querySelector(".vol-row").hidden && pr.querySelector(".now").hidden &&
        pr.querySelectorAll(".arow")[2].classList.contains("running"));
  player.hass = make_hass({ "media_player.cinewall": starting });
  check("player starting: the row says Starting with the moving dots",
        /Starting/.test(pr.querySelectorAll(".arow")[1].querySelector(".asub").textContent) &&
        pr.querySelectorAll(".arow")[1].querySelector(".dots"));
  /* S3c: shuffle, repeat and a seekable bar, as the webapp remote */
  const music = { state: "playing", attributes: { ...playing.attributes, supported_features: playing.attributes.supported_features | 2 | 32768 | 262144,
    shuffle: false, repeat: "all", media_duration: 200, media_position: 50, media_position_updated_at: new Date().toISOString() } };
  player.hass = make_hass({ "media_player.cinewall": music });
  check("player: shuffle and repeat between the times, off dim / on bright, repeat-one's 1 hidden",
        !pr.querySelector(".modes").hidden && !pr.querySelector('.mode[data-mode="shuffle"]').classList.contains("on") &&
        pr.querySelector('.mode[data-mode="repeat"]').classList.contains("on") && pr.querySelector(".mode-one").hidden &&
        pr.querySelector(".times").children[1].classList.contains("modes"));
  check("player: the bar is seekable", pr.querySelector(".progress").classList.contains("seekable"));
  calls.length = 0;
  pr.querySelector('.mode[data-mode="shuffle"]').click();
  pr.querySelector('.mode[data-mode="repeat"]').click();
  await settle();
  check("player: shuffle tap -> the other one; repeat all -> one",
        JSON.stringify(calls) === JSON.stringify([["media_player", "shuffle_set", { entity_id: "media_player.cinewall", shuffle: true }],
                                                  ["media_player", "repeat_set", { entity_id: "media_player.cinewall", repeat: "one" }]]));
  const ptrack = pr.querySelector(".ptrack");
  ptrack.getBoundingClientRect = () => ({ left: 0, width: 200 });
  calls.length = 0;
  ptrack.dispatchEvent(new win.MouseEvent("pointerdown", { clientX: 150 }));
  ptrack.dispatchEvent(new win.MouseEvent("pointermove", { clientX: 100 }));
  check("player: the bar follows the finger", parseFloat(pr.querySelector(".bar").style.width) === 50);
  ptrack.dispatchEvent(new win.MouseEvent("pointerup", { clientX: 100 }));
  await settle();
  check("player: release -> media_seek to that second",
        JSON.stringify(calls) === JSON.stringify([["media_player", "media_seek", { entity_id: "media_player.cinewall", seek_position: 100 }]]));
  const one = { state: "playing", attributes: { ...music.attributes, repeat: "one", shuffle: true } };
  player.hass = make_hass({ "media_player.cinewall": one });
  check("player: repeat one shows its 1, shuffle on bright",
        !pr.querySelector(".mode-one").hidden && pr.querySelector('.mode[data-mode="shuffle"]').classList.contains("on"));
  player.hass = make_hass({ "media_player.cinewall": { state: "playing", attributes: { ...music.attributes, supported_features: playing.attributes.supported_features } } });
  calls.length = 0;
  ptrack.dispatchEvent(new win.MouseEvent("pointerdown", { clientX: 10 }));
  ptrack.dispatchEvent(new win.MouseEvent("pointerup", { clientX: 10 }));
  await settle();
  check("player: not offered - no modes, the bar a picture, a press seeks nothing",
        pr.querySelector(".modes").hidden && !pr.querySelector(".progress").classList.contains("seekable") && calls.length === 0);
  /* The shortcut row (the webapp remote's APP_REMOTE_SHORTCUTS, the Touch's):
     Browse and Search open the Browse page of what runs (Michel 2026-10-26) */
  const pid = (a, play) => `play:${a}:${JSON.stringify(play)}`;
  const THUMB = (n) => `/api/media_player_proxy/media_player.cinewall/browse_media/image/${n}?token=ha`;
  const ALBUM = { kind: "music", source: "music_album", key: "5", title: "Thriller" };
  const FILM = { kind: "movie", title_id: 9, title: "Alien" };
  player.setConfig({ entity: "media_player.cinewall", browse: { music: "/artiled-control/zone-3-music", movies: "/artiled-control/zone-3-movies" } });
  player.hass = make_hass({ "media_player.cinewall": { state: "playing", attributes: { ...playing.attributes, plays_music: true, plays_movies: true, browse_kind: "movies" } } });
  await settle();
  const pr2 = player.shadowRoot;
  const sb = pr2.querySelector('.shortcut[data-goto="browse"]');
  const ss = pr2.querySelector('.shortcut[data-goto="search"]');
  check("player: Browse and Search as the remote's round icons (folder-open, magnifying-glass), no text buttons",
        sb && ss && !sb.hidden && !ss.hidden && sb.getAttribute("aria-label") === "Browse" && ss.getAttribute("aria-label") === "Search" &&
        !/Browse Music|Browse Movies|Find/.test(pr2.querySelector(".remote").textContent));
  const pcss = pr2.querySelector("style").textContent;
  check("player: the shortcut keys as big as the transport keys (60 round, 22 glyph) - Michel: 'quite small'",
        /\.shortcut \{[^}]*width: 60px; height: 60px/.test(pcss) && /\.shortcut ha-icon \{ --mdc-icon-size: 22px; \}/.test(pcss) &&
        /\.key \{[^}]*width: 60px; height: 60px/.test(pcss) && /\.key ha-icon \{ --mdc-icon-size: 22px; \}/.test(pcss));
  check("player: the light Font Awesome glyphs (the Touch's), MDI only while the Control's are not there",
        ["mdi:folder-open-outline", "articontrol:fa-light-folder-open"].includes(sb.querySelector("ha-icon").getAttribute("icon")));
  let went = null;
  win.addEventListener("location-changed", () => { went = win.location.pathname + win.location.search; });
  sb.click();
  check("player: Browse while a film activity runs opens Browse Movies", went === "/artiled-control/zone-3-movies", went);
  ss.click();
  check("player: Search opens the same page with its search field ready", went === "/artiled-control/zone-3-movies?search=1", went);
  player.hass = make_hass({ "media_player.cinewall": { state: "playing", attributes: { ...playing.attributes, plays_music: true, plays_movies: true, browse_kind: "music" } } });
  sb.click();
  check("player: a music activity's Browse opens Browse Music", went === "/artiled-control/zone-3-music", went);
  player.hass = make_hass({ "media_player.cinewall": { state: "playing", attributes: { ...playing.attributes } } });
  check("player: a room that plays nothing: no shortcuts", pr2.querySelector(".shortcuts").hidden);
  /* 0.3.23: Browse Music's first reads are made while the room's page shows */
  const pf_calls = () => calls.filter((c) => c[0] === "ws" && c[1] === "artiled_control/find" && c[2].entity_id === "media_player.prefetch");
  const pf = win.document.createElement("artiled-control-player-card");
  pf.setConfig({ entity: "media_player.prefetch", browse: { music: "/artiled-control/x-music" } });
  win.document.body.appendChild(pf);
  const pf_state = { "media_player.prefetch": { state: "on", attributes: { ...playing.attributes, plays_music: true, browse_kind: "music" } } };
  pf.hass = make_hass(pf_state);
  await settle();
  check("prefetch: nothing asked at once (the page draws first)", pf_calls().length === 0);
  await new Promise((r) => setTimeout(r, 1600));
  check("prefetch: 1.5 s later Browse's accounts, then Recent, are read (kept by the integration)",
        pf_calls().map((c) => c[2].request.action).join() === "accounts,recent" && pf_calls().every((c) => c[2].entity_id === "media_player.prefetch"),
        JSON.stringify(calls.slice(-5)));
  pf.hass = make_hass(pf_state);
  await new Promise((r) => setTimeout(r, 1600));
  check("prefetch: once per room per 5 minutes, not on every repaint", pf_calls().length === 2);
  pf.remove();
  win.history.pushState(null, "", "/artiled-control/home");
  player.setConfig({ entity: "media_player.cinewall" });
  player.hass = make_hass({ "media_player.cinewall": playing });
  await settle();

  /* The Browse page = the webapp's Find Music / Find Movies (0.3.16): its tabs, its requests, its pages */
  const browse_ws = [];
  const ALB = (n) => ({ id: n, title: `Album ${n}`, subtitle: "Someone", image: THUMB(`a${n}`),
                        content_id: pid(12, { ...ALBUM, key: String(n), title: `Album ${n}` }), content_type: "music", path: [{ x: n }] });
  const find_answers = (req) => {
    /* the REAL /unit_find shape (read live 2026-10-26, room 2): the flags one level down */
    if (req.action === "accounts") return req.fresh === "1"
      ? { accounts: { tidal: true, apple: false, sonoslib: true, sonos: { tidal: true }, roon: { tidal: true, qobuz: true } }, spotify: false, kept: true }
      : { accounts: { tidal: true, apple: false, sonoslib: true, sonos: { tidal: true }, roon: { tidal: true, qobuz: false },
                      heos: { sources: [], favorites: false } }, spotify: false, kept: true };
    if (req.action === "recent") return { sections: [
      { title: "Recently played", items: [{ title: "Thriller", subtitle: "MJ", image: THUMB("t"), content_id: pid(12, ALBUM), content_type: "music" }] },
      { title: "Recently added", items: [{ title: "Bad", subtitle: "MJ", image: THUMB("b"), content_id: pid(12, { ...ALBUM, key: "8", title: "Bad" }), content_type: "music" }] }] };
    if (req.action === "albums") {
      const all = Array.from({ length: 230 }, (_, n) => ALB(n + 1));
      const from = Number(req.offset) || 0;
      return { items: all.slice(from, from + 100), count: 230, next: Math.min(230, from + 100) };
    }
    const P = req.path ? JSON.parse(req.path) : [];
    if (req.action === "browse" && P.length === 1 && /artists/.test(req.path)) {
      return { items: [{ title: "Michael Jackson", subtitle: "9 Albums", image: THUMB("mj"), path: [{ root: "artists" }, { title: "Michael Jackson" }], hint: "list" },
                       { title: "Nobody Pictured", subtitle: "1 Album", image: "", path: [{ root: "artists" }, { title: "Nobody Pictured" }], hint: "list" }], count: 2, next: 2 };
    }
    if (req.action === "browse" && P.length === 1 && /composers/.test(req.path)) {
      return { items: [{ title: "Mozart", image: THUMB("wam"), path: [{ root: "composers" }, { title: "Mozart" }], hint: "list" }], count: 1, next: 1 };
    }
    if (req.action === "browse" && P.length === 2 && P[0].root === "composers") {
      return { items: [{ title: "Play Composer", hint: "action", kind: "action" },
                       { title: "Requiem", subtitle: "2 performances", image: "", path: [...P, { title: "Requiem" }], hint: "list" },
                       { title: "Eine kleine Nachtmusik", subtitle: "", image: "", path: [...P, { title: "Eine kleine Nachtmusik" }], hint: "list" }], count: 3, next: 3 };
    }
    if (req.action === "browse" && P.length === 3) {
      return { items: [{ title: "Play Album", hint: "action", kind: "action" },
                       { title: "1. Wanna Be Startin' Somethin'", subtitle: "Michael Jackson", image: "", path: [...P, { title: "1. Wanna Be Startin' Somethin'" }], hint: "action_list" },
                       { title: "2. Baby Be Mine", subtitle: "Michael Jackson", image: "", path: [...P, { title: "2. Baby Be Mine" }], hint: "action_list" }], count: 3, next: 3 };
    }
    if (req.action === "browse") {
      /* an artist's records: Roon lists a record's pressings apart (the doubles Michel saw) */
      return { items: [{ title: "Thriller", subtitle: "Michael Jackson", image: THUMB("t"), content_id: pid(12, ALBUM), content_type: "music",
                         path: [{ root: "artists" }, { title: "Michael Jackson" }, { title: "Thriller" }], hint: "list" },
                       { title: "Thriller (Remastered 2001)", subtitle: "Michael Jackson", image: THUMB("t2"),
                         path: [{ root: "artists" }, { title: "Michael Jackson" }, { title: "Thriller (Remastered 2001)" }], hint: "list" },
                       { title: "Bad", subtitle: "Michael Jackson", image: THUMB("bad"),
                         path: [{ root: "artists" }, { title: "Michael Jackson" }, { title: "Bad" }], hint: "list" },
                       { title: "Unplayable", image: THUMB("u"), hint: "action" }], count: 4, next: 4 };
    }
    if (req.action === "about") return { kind: "album", year: 1982, label: "Epic", total: 9, minutes: 42 };
    if (req.action === "actions") return { actions: ["Play Now", "Add Next", "Queue", "Start Radio"], error: "" };
    if (req.action === "text") return { text: "Michael Jackson was an American singer." };
    if (req.action === "covers") return { covers: [THUMB("on19")], error: "" };
    if (req.action === "play_item") return { ok: true };
    if (req.action === "titles") return { titles: [
      { id: 9, name: "Alien", year: 1979, image: THUMB("al"), favorite: true, added: "2026-10-01", genres: "Sci-Fi", content_id: pid(11, FILM), content_type: "movie" },
      { id: 10, name: "Blade Runner", year: 1982, image: THUMB("br"), favorite: false, added: "2026-10-20", genres: "Sci-Fi" }] };
    if (req.action === "people") return { people: [{ name: "Ridley Scott", portrait: THUMB("rs"), titles: [9, 10] }] };
    return {};
  };
  const browse_hass = (opts = {}) => ({
    states: { "media_player.cinewall": { state: "playing", attributes: { ...playing.attributes, activity_id: 12 } } },
    user: { is_admin: true },
    callService: (domain, service, data) => {
      calls.push([domain, service, data]);
      if (service === "start_activity") opts.started = true;
      return Promise.resolve();
    },
    callWS: (msg) => {
      browse_ws.push(msg);
      if (msg.type === "artiled_control/find") {
        if (opts.idle && msg.request.action === "play" + "_item" && !opts.started) {
          return Promise.reject({ code: "not_running", message: "Start the room's music first" });
        }
        let ans = find_answers(msg.request);
        /* 0.3.23: the integration served a kept answer (its age) - the fresh one differs */
        if (opts.kept && msg.request.action === "recent") {
          if (!msg.fresh) return Promise.resolve({ service: "roon", activity: 12, idle: false, kept: 300, answer: ans });
          ans = JSON.parse(JSON.stringify(ans));
          if (opts.kept === "changed") ans.sections[0].items[0].title = "Thriller (fresh)";
        }
        return Promise.resolve({ service: msg.movies ? "movies" : "roon", activity: 12, idle: !!opts.idle && !opts.started, kept: 0, answer: ans });
      }
      if (msg.type === "artiled_control/search") {
        return opts.search_fails ? Promise.reject({ message: "Kitchen: The Roon albums could not be read now" })
          : Promise.resolve({ result: [
            { title: "Thriller 25", subtitle: "Michael Jackson", activity: 12, content_type: "music",
              content_id: pid(12, { ...ALBUM, key: "41" }), image: THUMB("t25") },
            { title: "Dangerous", subtitle: "Michael Jackson", activity: 11, content_type: "music",
              content_id: pid(11, { kind: "music", source: "zidoo_album", key: "zidoo:8:" + "1".repeat(40), title: "Dangerous" }), image: THUMB("d") }] });
      }
      return Promise.resolve({});
    },
  });
  const page = (kind, opts) => {
    const c = win.document.createElement("artiled-control-browse-card");
    c.setConfig({ entity: "media_player.cinewall", kind });
    win.document.body.appendChild(c);
    c.hass = browse_hass(opts);
    return c;
  };
  browse_ws.length = 0;
  const mpage = page("music");
  await settle();
  const mr = mpage.shadowRoot;
  check("browse page: the Roon player's Find Music tabs, in its order and names (+ TIDAL and Qobuz from answer.accounts.roon, kept then fresh)",
        Array.from(mr.querySelectorAll(".ftab")).map((t) => t.textContent).join("|") === "Recent|Favorites|Playlists|Albums|Artists|Composers|TIDAL|Qobuz");
  check("browse page: only HA's websocket - the webapp's own requests (accounts kept, accounts fresh=1, then Recent's)",
        browse_ws.every((m) => m.type === "artiled_control/find" && m.entity_id === "media_player.cinewall") &&
        JSON.stringify(browse_ws.map((m) => m.request.action + (m.request.fresh ? "+fresh" : ""))) === JSON.stringify(["accounts", "accounts+fresh", "recent"]),
        JSON.stringify(browse_ws.map((m) => m.request)));
  check("browse page: Recent as the webapp draws it - its two sections", Array.from(mr.querySelectorAll(".view .section")).map((s) => s.textContent).join("|") ===
        "Recently played|Recently added");
  let tiles = Array.from(mr.querySelectorAll(".view .tile"));
  check("browse page: covers lazy (drawn at once without IntersectionObserver), each with its title",
        tiles.length === 2 && /media_player_proxy/.test(tiles[0].querySelector(".cover").style.backgroundImage) && tiles[0].querySelector(".tile-name").textContent === "Thriller");
  calls.length = 0;
  tiles[0].click();
  await settle();
  check("browse page: a playable tile plays (media_player.play_media - running-activity aware)",
        JSON.stringify(calls[0]) === JSON.stringify(["media_player", "play_media",
          { entity_id: "media_player.cinewall", media_content_id: pid(12, ALBUM), media_content_type: "music" }]), JSON.stringify(calls[0]));
  browse_ws.length = 0;
  mr.querySelector('.ftab[data-t="albums"]').click();
  await settle();
  check("browse page Albums: the webapp's request (Control's own album list, from 0)",
        browse_ws.length === 1 && browse_ws[0].request.action === "albums" && browse_ws[0].request.offset === 0 && browse_ws[0].request.favorites === "");
  check("browse page Albums: a hundred tiles, then 'Loading more… (100 of 230)' (find-music.js moreHtml)",
        mr.querySelectorAll(".view .tile").length === 100 && /Loading more… \(100 of 230\)/.test(mr.querySelector(".view .more").textContent));
  browse_ws.length = 0;
  mr.querySelector(".view .more").click();
  await settle();
  check("browse page: the next part asked from where the Control said (offset 100)",
        browse_ws.length === 1 && browse_ws[0].request.offset === 100 && mr.querySelectorAll(".view .tile").length === 200);
  mr.querySelector(".view .more").click();
  await settle();
  check("browse page: the rest; no more 'Loading more'", mr.querySelectorAll(".view .tile").length === 230 && !mr.querySelector(".view .more"));
  mr.querySelector('.ftab[data-t="artists"]').click();
  await settle();
  browse_ws.length = 0;
  mr.querySelector(".view .tile").click();
  await settle();
  check("browse page: an artist OPENS its page - the webapp's openItem reads (about, actions, text, its records by its path)",
        JSON.stringify(browse_ws.map((m) => m.request.action).sort()) === JSON.stringify(["about", "actions", "browse", "text"]) &&
        /Michael Jackson/.test(browse_ws.find((m) => m.request.action === "browse").request.path) &&
        browse_ws.find((m) => m.request.action === "text").request.artist === "Michael Jackson" &&
        mr.querySelector(".app-find__open-title").textContent === "Michael Jackson" && !!mr.querySelector(".fback"));
  check("artist page: the person's picture, no Roon count under the name, the bio clamped to four lines",
        /mj/.test(mr.querySelector(".app-find__open-cover").getAttribute("style") || "") &&
        !Array.from(mr.querySelectorAll(".app-find__open-sub")).some((n) => /9 Albums/.test(n.textContent)) &&
        /American singer/.test(mr.querySelector(".app-find__text").textContent));
  check("artist page: Roon's own play options in one row (find-music.js .app-find__plays)",
        Array.from(mr.querySelectorAll(".app-find__plays .app-find__play")).map((b) => b.textContent).join("|") === "Play Now|Add Next|Queue|Start Radio");
  check("artist page: ONE tile a record (Michel: 'Lots of doubles') - Thriller's remaster folded into Thriller, headed 'Albums · 3'",
        Array.from(mr.querySelectorAll(".view .tile .tile-name")).map((n) => n.textContent).join("|") === "Thriller|Bad|Unplayable" &&
        /Albums · 3/.test(mr.querySelector(".app-find__section").textContent));
  tiles = Array.from(mr.querySelectorAll(".view .tile"));
  check("browse page: a row the Control does not mark playable is shown, dimmed, does nothing",
        tiles[2].classList.contains("dead"));
  calls.length = 0;
  tiles[2].click();
  await settle();
  check("browse page: ...no call", calls.length === 0);
  browse_ws.length = 0;
  mr.querySelector(".fback").click();
  await settle();
  check("browse page: Back - the artists again", mr.querySelector(".view .tile .tile-name").textContent === "Michael Jackson" && !mr.querySelector(".app-find__open"));
  check("artists: a person with no picture is not drawn (find-music.js pictureless); no Roon count under a person",
        mr.querySelectorAll(".view .tile").length === 1 && !mr.querySelector(".view .tile .tile-sub"));

  /* --- the album page (0.3.21, Michel: "cover and Info and play options") --- */
  mr.querySelector(".view .tile").click();
  await settle();
  browse_ws.length = 0;
  calls.length = 0;
  const thr = mr.querySelectorAll(".view .tile")[0];
  check("a tile that opens AND plays carries the play button on its cover", !!thr.querySelector(".cover .tile-play"));
  thr.querySelector(".tile-play").click();
  await settle();
  check("one tap on it plays (play_media), the page does not open",
        calls.length === 1 && calls[0][1] === "play_media" && calls[0][2].media_content_id === pid(12, ALBUM) && !!mr.querySelector(".app-find__open-title") &&
        mr.querySelector(".app-find__open-title").textContent === "Michael Jackson");
  thr.click();
  await settle();
  check("a tap on the tile opens the album's page", mr.querySelector(".app-find__open-title").textContent === "Thriller");
  const info = Array.from(mr.querySelectorAll(".app-find__open-sub")).map((n) => n.textContent);
  check("album page: cover, title, artist, then the info line '1982 · Epic · 9 tracks · 42 min' (openHtml)",
        /url\(/.test(mr.querySelector(".app-find__open-cover").getAttribute("style") || "") &&
        info.join("|") === "Michael Jackson|1982 · Epic · 9 tracks · 42 min", info.join("|"));
  check("album page: the play options right under the words, on the left - not a lone button at the far edge",
        mr.querySelector(".app-find__plays").previousElementSibling.classList.contains("app-find__text") &&
        /\.app-find__plays \{ display: flex; gap: 4px; margin-top: 12px; \}/.test(mr.querySelector("style").textContent));
  const trows = Array.from(mr.querySelectorAll(".app-queue__row"));
  check("album page: the tracks as the Queue's rows, Roon's 'Play Album' row left out, headed 'Tracks · 2'",
        trows.length === 2 && /Tracks · 2/.test(mr.querySelector(".app-find__section").textContent));
  check("album page: each track has artwork - the album's own cover (Michel: 'Tracks no artwork')",
        trows.every((r) => /\/t[^2]|t\b|thumb/i.test(r.querySelector(".app-queue__cover").style.backgroundImage || "") &&
                           !r.querySelector(".app-queue__cover").classList.contains("app-queue__cover--number")));
  check("album page: the number once, before the name; no artist repeated on an album",
        trows[0].querySelector(".app-queue__no").textContent === "1" && /Wanna Be Startin/.test(trows[0].querySelector(".app-queue__name").textContent) &&
        !trows[0].querySelector(".app-queue__sub"));
  browse_ws.length = 0;
  mr.querySelector('.app-find__play[data-act="Add Next"]').click();
  await settle();
  const pi = browse_ws.find((m) => m.request.action === "play_item");
  check("album page: 'Add Next' while the music runs = the webapp's play_item, Roon's own word, the album's path",
        pi && pi.request.do === "Add Next" && JSON.parse(pi.request.item).path.length === 3);
  browse_ws.length = 0;
  trows[1].querySelector(".app-queue__jump").click();
  await settle();
  const pt = browse_ws.find((m) => m.request.action === "play_item");
  check("album page: a track's play button plays THAT track (Play Now by its path)",
        pt && pt.request.do === "Play Now" && /Baby Be Mine/.test(pt.request.item));
  mr.querySelector(".fback").click();
  await settle();
  browse_ws.length = 0;
  const bad_play = mr.querySelectorAll(".view .tile")[1].querySelector(".tile-play");
  check("a record inside an artist (no play of the Control's own) has the cover play button too", !!bad_play);
  bad_play.click();
  await settle();
  const bp = browse_ws.find((m) => m.request.action === "play_item");
  check("...one tap plays it through the door: Play Now by its path, the page stays",
        bp && bp.request.do === "Play Now" && /"Bad"/.test(bp.request.item) && mr.querySelector(".app-find__open-title").textContent === "Michael Jackson");
  check("Back from the album: the artist's page again (title and records)",
        mr.querySelector(".app-find__open-title").textContent === "Michael Jackson" && mr.querySelectorAll(".view .tile").length === 3);
  mr.querySelector(".fback").click();
  await settle();

  /* nothing running: a play option starts the room's music first, then plays */
  const ipage = page("music", { idle: true });
  await settle();
  const ir = ipage.shadowRoot;
  ir.querySelector('.ftab[data-t="artists"]').click();
  await settle();
  ir.querySelector(".view .tile").click();
  await settle();
  ir.querySelectorAll(".view .tile")[1].click();   /* Bad: no play of its own */
  await settle();
  calls.length = 0;
  browse_ws.length = 0;
  ir.querySelector('.app-find__play[data-act="Play Now"]').click();
  await settle();
  check("idle: the door says not running -> the room's music activity is started (start_activity), 'Starting' with dots",
        calls.some((c) => c[1] === "start_activity" && c[2].activity === 12) && /Starting/.test(ir.querySelector(".view").textContent) &&
        !!ir.querySelector(".app-find__note .dots"));
  await new Promise((r) => setTimeout(r, 1700));
  check("idle: ...then the same play is sent again and goes through (no note left)",
        browse_ws.filter((m) => m.request.action === "play_item").length === 2 && !ir.querySelector(".app-find__note"));
  ir.querySelector(".fback").click();
  await settle();
  calls.length = 0;
  ir.querySelectorAll(".view .tile")[0].click();   /* Thriller: the Control handed it a play */
  await settle();
  browse_ws.length = 0;
  ipage.hass = browse_hass({ idle: true });
  ipage._idle = true;
  ir.querySelector('.app-find__play[data-act="Play Now"]').click();
  await settle();
  check("idle + the Control's own play: Play Now = play_media (starts the activity and plays it in one step)",
        calls.length === 1 && calls[0][1] === "play_media" && !browse_ws.some((m) => m.request.action === "play_item"));
  ipage.remove();

  /* composers: never a work without artwork (Michel: "compositions, no artwork") */
  mr.querySelector('.ftab[data-t="composers"]').click();
  await settle();
  mr.querySelector(".view .tile").click();
  await settle();
  await settle();
  const crow = Array.from(mr.querySelectorAll(".app-queue__row"));
  check("composer page: the works as a list headed 'Compositions · 2' ('Play Composer' left out)",
        crow.length === 2 && /Compositions · 2/.test(mr.querySelector(".app-find__section").textContent));
  check("composer page: each work shows its record's cover (the Control's covers read)",
        browse_ws.filter((m) => m.request.action === "covers").length >= 2 &&
        crow.every((r) => /on19/.test(r.querySelector(".app-queue__cover").style.backgroundImage || "")));
  check("composer page: a work's line keeps what Roon says under it", /2 performances/.test(crow[0].textContent));
  check("NO rendered cover is empty: every tile and row has a picture, four covers or the webapp's letter",
        Array.from(mr.querySelectorAll(".view .cover, .view .app-queue__cover, .view .app-find__open-cover")).every((c) =>
          !!(c.style.backgroundImage || c.getAttribute("data-bg") || c.querySelector(".app-find__mosaic") || c.textContent.trim())));
  mr.querySelector(".fback").click();
  await settle();
  const field = mr.querySelector(".search");
  browse_ws.length = 0;
  field.value = "thriller";
  field.dispatchEvent(new win.Event("input"));
  await settle();
  check("browse page: typing waits 400 ms before it searches (find-music-bind.js)", !browse_ws.some((m) => m.type === "media_player/search_media"));
  await new Promise((r) => setTimeout(r, 450));
  const sq = browse_ws.find((m) => m.type === "artiled_control/search");
  check("browse page: SEARCH stays the quick field across everything (the integration's own search)",
        sq && sq.query === "thriller" && sq.entity_id === "media_player.cinewall" && !sq.movies);
  const rtiles = Array.from(mr.querySelectorAll(".results .tile"));
  check("browse page: its results INSTEAD of the tab, title and ARTIST as the webapp's tile - not the running activity's name",
        mr.querySelector(".view").hidden === true && rtiles[0].querySelector(".tile-name").textContent === "Thriller 25" &&
        Array.from(rtiles[0].querySelectorAll(".tile-sub")).map((n) => n.textContent).join("|") === "Michael Jackson");
  check("browse page: the activity named ONLY when it would play on another one than runs (Michel 2026-10-26)",
        Array.from(rtiles[1].querySelectorAll(".tile-sub")).map((n) => n.textContent).join("|") === "Michael Jackson|on Netflix");
  check("browse page: tiles of the webapp's phone size, as many columns as fit",
        /\.tiles \{[^}]*grid-template-columns: repeat\(auto-fill, minmax\(104px, 1fr\)\)/.test(mr.querySelector("style").textContent));
  mpage.hass = browse_hass({ search_fails: true });
  field.value = "x";
  field.dispatchEvent(new win.Event("change"));
  await settle();
  check("browse page: a failed search says why", /could not be read/.test(mr.querySelector(".results").textContent));
  field.value = "";
  field.dispatchEvent(new win.Event("change"));
  check("browse page: cleared - the tab again", mr.querySelector(".view").hidden === false);
  mpage.remove();

  /* 0.3.24: a tapped tile stays, with the starting look, never a blank list */
  {
    let release;
    const sp = page("music");
    await settle();
    sp.hass = { ...browse_hass(), callService: (d, sv, data) => { calls.push([d, sv, data]); return new Promise((r) => { release = r; }); } };
    sp.shadowRoot.querySelector('.ftab[data-t="recent"]').click();
    await settle();
    const t0 = sp.shadowRoot.querySelector(".view .tile");
    const before = sp.shadowRoot.querySelectorAll(".view .tile").length;
    t0.click();
    await settle();
    check("a tapped tile: the starting look and 'Starting' with the moving dots, the list untouched",
          t0.classList.contains("is-starting") && /Starting/.test(t0.textContent) && !!t0.querySelector(".tile-starting .dots") &&
          sp.shadowRoot.querySelectorAll(".view .tile").length === before);
    release();
    await new Promise((r) => setTimeout(r, 1300));
    check("...gone once the Control took the play", !t0.classList.contains("is-starting") && !t0.querySelector(".tile-starting"));
    sp.remove();
    /* a rebuilt Browse card draws its tab from what this browser kept, before any answer */
    const hold = win.document.createElement("artiled-control-browse-card");
    hold.setConfig({ entity: "media_player.cinewall", kind: "music" });
    win.document.body.appendChild(hold);
    hold.hass = { ...browse_hass(), callWS: () => new Promise(() => {}) };
    await settle();
    check("rebuilt Browse page: its tabs and the open tab's tiles drawn at once from what was kept (nothing answered yet)",
          hold.shadowRoot.querySelectorAll(".ftab").length > 3 && hold.shadowRoot.querySelectorAll(".view .tile").length > 0);
    hold.remove();
  }

  /* kept, then fresh (0.3.23) */
  browse_ws.length = 0;
  const kpage = page("music", { kept: "changed" });
  await settle();
  browse_ws.length = 0;
  kpage.shadowRoot.querySelector('.ftab[data-t="recent"]').click();
  await settle();
  await settle();
  const recents = browse_ws.filter((m) => m.request.action === "recent");
  check("kept: the kept Recent drawn, then asked again with fresh=true (once)",
        recents.length === 2 && !recents[0].fresh && recents[1].fresh === true, JSON.stringify(browse_ws.map((m) => [m.request.action, m.fresh])));
  check("kept: the fresh answer that differs is drawn", kpage.shadowRoot.querySelector(".view .tile-name").textContent === "Thriller (fresh)");
  kpage.remove();
  browse_ws.length = 0;
  const kpage2 = page("music", { kept: "same" });
  await settle();
  browse_ws.length = 0;
  kpage2.shadowRoot.querySelector('.ftab[data-t="recent"]').click();
  await new Promise((r) => setTimeout(r, 0));
  const first_tile = kpage2.shadowRoot.querySelector(".view .tile");
  await settle();
  await settle();
  check("kept: a fresh answer that is the same draws nothing again (no jump)",
        browse_ws.filter((m) => m.request.action === "recent").length === 2 && kpage2.shadowRoot.querySelector(".view .tile") === first_tile);
  kpage2.remove();

  browse_ws.length = 0;
  const films = page("movies");
  await settle();
  const fr2 = films.shadowRoot;
  check("browse page Movies: Find Movies' tabs (Recent, Favorites, Movies, Directors, Actors, Genres), its titles read once",
        Array.from(fr2.querySelectorAll(".ftab")).map((t) => t.textContent).join("|") === "Recent|Favorites|Movies|Directors|Actors|Genres" &&
        browse_ws.filter((m) => m.request.action === "titles").length === 1 && browse_ws.every((m) => m.movies === true));
  check("browse page Movies Recent: newest first, posters (2:3)",
        Array.from(fr2.querySelectorAll(".view .tile-name")).map((n) => n.textContent).join() === "Blade Runner,Alien" &&
        fr2.querySelector(".view .cover").classList.contains("poster"));
  fr2.querySelector('.ftab[data-t="loved"]').click();
  await settle();
  check("browse page Movies Favorites: the hearted film", Array.from(fr2.querySelectorAll(".view .tile-name")).map((n) => n.textContent).join() === "Alien");
  fr2.querySelector('.ftab[data-t="movies"]').click();
  await settle();
  check("browse page Movies: a film on another player is shown, not playable", fr2.querySelectorAll(".view .tile")[1].classList.contains("dead"));
  fr2.querySelector('.ftab[data-t="directors"]').click();
  await settle();
  fr2.querySelector(".view .tile").click();
  await settle();
  check("browse page Movies Directors: a director opens his films (Find Movies' person)",
        fr2.querySelector(".ftitle").textContent === "Ridley Scott" && fr2.querySelectorAll(".view .tile").length === 2);
  films.remove();
  win.history.pushState(null, "", "/artiled-control/zone-3-music?search=1");
  const focused = page("music");
  await settle();
  check("browse page opened by Search: the field has the focus", focused.shadowRoot.activeElement === focused.shadowRoot.querySelector(".search"));
  focused.remove();
  win.history.pushState(null, "", "/artiled-control/home");
  const no_off = { state: "off", attributes: { ...off_state.attributes, off_activity: 0 } };
  player.hass = make_hass({ "media_player.cinewall": no_off });
  check("player: no Off row for a unit without an Off activity (it could only fail)",
        !pr.querySelector('.arow[data-off="1"]'));

  /* --- the voice card --------------------------------------------- */
  const vc = win.document.createElement("artiled-control-voice-card");
  vc.setConfig({
    rooms: [{ entity: "media_player.kitchen", name: "Kitchen" }],
    zones: [{ entity: "media_player.cinewall", name: "Cinewall" }],
    units: [
      { kind: "zone", entity: "media_player.cinewall", name: "Cinewall", icon: "articontrol:fa-tv",
        activities: [{ id: 11, name: "Netflix" }, { id: 12, name: "Radio" }],
        chosen: [{ activity: 11, name: "Netflix on the wall", entity: "scene.netflix_on_the_wall" }] },
    ],
  });
  win.document.body.appendChild(vc);
  calls.length = 0;
  vc.hass = make_hass({
    "media_player.cinewall": playing,
    "media_player.kitchen": { state: "off", attributes: { friendly_name: "Kitchen" } },
    "light.kitchen": { state: "on", attributes: { friendly_name: "Kitchen" } },
  });
  await settle();
  const vr = vc.shadowRoot;
  check("voice: tabs Rooms, Zones, Activities (the Hub's layout)",
        Array.from(vr.querySelectorAll(".tab")).map((x) => x.textContent.trim()).join("|") === "Rooms (1)|Zones (1)|Activities (1)");
  check("voice: the exposure read from the registry", vr.querySelector('.pill[data-a="conversation"][data-e="media_player.kitchen"]').classList.contains("on"));
  check("voice: aliases shown, the stray null dropped", vr.querySelector('input[data-e="media_player.kitchen"]').value === "Telly room");
  check("voice: a name clash across the instance is warned", /light\.kitchen/.test(vr.querySelector('.item[data-e="media_player.kitchen"] .clash').getAttribute("title")));
  /* a clash with something no assistant has is no clash (Michel 2026-10-26) */
  const clash_hass = () => make_hass({
    "media_player.cinewall": playing,
    "media_player.kitchen": { state: "off", attributes: { friendly_name: "Kitchen" } },
    "light.kitchen": { state: "on", attributes: { friendly_name: "Kitchen" } },
  });
  expose_list = { exposed_entities: { "light.kitchen": { conversation: false, "cloud.alexa": false } } };
  vc.hass = clash_hass(); await vc._build(); await settle();
  check("voice: a same-named entity no assistant has gives no warning",
        !vr.querySelector('.item[data-e="media_player.kitchen"] .clash'));
  expose_list = { exposed_entities: { "light.kitchen": { conversation: true } } };
  vc.hass = clash_hass(); await vc._build(); await settle();
  check("voice: a same-named entity Assist has still warns",
        /light\.kitchen/.test((vr.querySelector('.item[data-e="media_player.kitchen"] .clash') || { getAttribute: () => "" }).getAttribute("title")));
  expose_list = null;
  calls.length = 0;
  vr.querySelector('.pill[data-a="cloud.alexa"][data-e="media_player.kitchen"]').click();
  await settle();
  check("voice: a pill exposes the unit to that assistant", calls.some((c) => c[0] === "ws" && c[1] === "homeassistant/expose_entity" &&
        c[2].assistants[0] === "cloud.alexa" && c[2].entity_ids[0] === "media_player.kitchen" && c[2].should_expose === true));
  const det = vr.querySelector("details");
  det.open = true;
  det.dispatchEvent(new win.Event("toggle"));
  await settle();
  const opts = Array.from(vr.querySelectorAll(".opt"));
  check("voice: one row per activity, the chosen one ticked with its name",
        opts.length === 2 && opts[0].querySelector(".tick").classList.contains("on") &&
        opts[0].querySelector("input").value === "Netflix on the wall");
  check("voice: the suggested name is '<activity> in <unit>'", opts[1].querySelector("input").placeholder === "Radio in the Cinewall");
  calls.length = 0;
  opts[1].querySelector(".tick").click();
  await settle();
  check("voice: ticking adds a voice activity by the unit's entity and the activity id",
        JSON.stringify(calls[0]) === JSON.stringify(["artiled_control", "add_voice_activity",
          { entity_id: "media_player.cinewall", activity: 12, name: "Radio in the Cinewall" }]));
  calls.length = 0;
  opts[0].querySelector(".tick").click();
  await settle();
  check("voice: unticking removes it (start-only: nothing is 'turned off')",
        JSON.stringify(calls[0]) === JSON.stringify(["artiled_control", "remove_voice_activity",
          { entity_id: "media_player.cinewall", activity: 11 }]));

  /* Adding or renaming reloads the integration and the strategy hands the page
     a NEW card with a new config (Michel 2026-10-26: "when I press a + I go back
     to the main menu right away", "when I change it it jumps back"). */
  vr.querySelector('.tab[data-t="activities"]').click();
  const radio_in = vr.querySelectorAll(".opt")[1].querySelector("input");
  radio_in.value = "Radio time";
  radio_in.dispatchEvent(new win.Event("change"));
  await settle();
  const voice_config = (radio_name) => ({
    rooms: [{ entity: "media_player.kitchen", name: "Kitchen" }],
    zones: [{ entity: "media_player.cinewall", name: "Cinewall" }],
    units: [
      { kind: "zone", entity: "media_player.cinewall", name: "Cinewall", icon: "articontrol:fa-tv",
        activities: [{ id: 11, name: "Netflix" }, { id: 12, name: "Radio" }],
        chosen: [{ activity: 12, name: radio_name, entity: "scene.radio" }] },
    ],
  });
  const rebuilt = async (radio_name) => {
    vc.remove();
    const fresh = win.document.createElement("artiled-control-voice-card");
    fresh.setConfig(voice_config(radio_name));
    win.document.body.appendChild(fresh);
    fresh.hass = make_hass({ "media_player.cinewall": playing, "media_player.kitchen": { state: "off", attributes: {} } });
    await settle();
    return fresh.shadowRoot;
  };
  let r2 = await rebuilt("Radio in the Cinewall");   /* the reload has not carried the new name yet */
  check("voice after a reload: still on the Activities tab",
        (r2.querySelector(".tab.on") || {}).textContent.startsWith("Activities"));
  check("voice after a reload: the room is still open", r2.querySelector("details").open === true &&
        r2.querySelectorAll(".opt").length === 2);
  check("voice after a reload: the typed name stays until the reload carries it",
        r2.querySelectorAll(".opt")[1].querySelector("input").value === "Radio time");
  check("voice after a reload: the + reads ticked", r2.querySelectorAll(".opt")[1].querySelector(".tick").classList.contains("on"));
  r2 = await rebuilt("Radio time");              /* now it does */
  check("voice: the reloaded name shown", r2.querySelectorAll(".opt")[1].querySelector("input").value === "Radio time");
  r2 = await rebuilt("Renamed elsewhere");       /* the typed one is no longer held */
  check("voice: once carried, the typed name is let go (a later change elsewhere shows)",
        r2.querySelectorAll(".opt")[1].querySelector("input").value === "Renamed elsewhere");
  const alias_in = r2.querySelector('.aliases input[data-e="scene.radio"]');
  check("voice: an added activity shows its aliases under it, the Rooms tab's field",
        alias_in && alias_in.value === "Telly room" && alias_in.placeholder === "also called - comma separated" &&
        !r2.querySelector('.opt:first-child + .aliases'));
  check("voice: a row not added has no alias field", r2.querySelectorAll(".opts .aliases").length === 1);
  calls.length = 0;
  alias_in.value = "Telly room, Tunes";
  alias_in.dispatchEvent(new win.Event("change"));
  await settle();
  const upd = calls.find((c) => c[1] === "config/entity_registry/update");
  check("voice: editing it saves the scene's aliases, HA's computed name kept",
        upd && upd[2].entity_id === "scene.radio" && JSON.stringify(upd[2].aliases) === JSON.stringify([null, "Telly room", "Tunes"]),
        JSON.stringify(upd));
  r2.querySelector('.tab[data-t="rooms"]').click();
  calls.length = 0;
  const room_alias = r2.querySelector('.item input[data-e="media_player.kitchen"]');
  room_alias.value = "Telly room, Den";
  room_alias.dispatchEvent(new win.Event("change"));
  await settle();
  const upd2 = calls.find((c) => c[1] === "config/entity_registry/update");
  check("voice: a room's aliases keep HA's computed name too",
        upd2 && JSON.stringify(upd2[2].aliases) === JSON.stringify([null, "Telly room", "Den"]), JSON.stringify(upd2));
  r2 = await rebuilt("Renamed elsewhere");
  check("voice: the tab chosen last is the one kept", (r2.querySelector(".tab.on") || {}).textContent.startsWith("Rooms"));

  /* "Listen to Zidoo" and "Watch Zidoo": the second keeps its verb. */
  vc.remove();
  const zc = win.document.createElement("artiled-control-voice-card");
  zc.setConfig({ rooms: [], zones: [{ entity: "media_player.cinewall", name: "Cinewall" }], units: [
    { kind: "zone", entity: "media_player.cinewall", name: "Kitchen",
      activities: [{ id: 13, name: "Listen to Zidoo" }, { id: 14, name: "Watch Zidoo" }],
      chosen: [{ activity: 13, name: "Zidoo in the Kitchen", entity: "scene.zidoo_in_the_kitchen" }] }] });
  win.document.body.appendChild(zc);
  zc.hass = make_hass({ "media_player.cinewall": playing,
    "scene.zidoo_in_the_kitchen": { state: "x", attributes: { friendly_name: "Zidoo in the Kitchen" } } });
  await settle();
  const zd = zc.shadowRoot.querySelector("details");
  zd.open = true; zd.dispatchEvent(new win.Event("toggle")); await settle();
  const zopts = zc.shadowRoot.querySelectorAll(".opt");
  check("voice: a taken short name -> that row suggests the long form",
        zopts[1].querySelector("input").placeholder === "Watch Zidoo in the Kitchen" &&
        zopts[1].querySelector(".opt__warn").textContent === "", zopts[1].querySelector("input").placeholder);
  check("voice: the added row keeps its own name", zopts[0].querySelector("input").value === "Zidoo in the Kitchen");

  /* --- the Favourites tab (Michel 2026-10-26: "favourites are favourites in ANY room") --- */
  zc.remove();
  const THR = { kind: "music", source: "sonos_favorite", key: "Thriller (Album)", title: "Thriller" };
  const R2 = { kind: "music", source: "sonos_favorite", key: "Radio 2", title: "Radio 2" };
  let fav_items = [
    { tab: "albums", play: THR, title: "Thriller", subtitle: "Spotify", type: "Album", service: "sonos", rooms: ["Kitchen", "Cinewall"],
      image: "/api/media_player_proxy/media_player.kitchen/browse_media/image/abc?token=ha", suggest: "Thriller",
      chosen: { name: "Thriller", auto: false, entity_id: "scene.thriller" }, excluded: false },
    { tab: "albums", play: R2, title: "Radio 2", subtitle: "", type: "Album", service: "sonos", rooms: ["Kitchen"], image: "",
      suggest: "Radio 2", chosen: null, excluded: false },
    { tab: "playlists", play: { ...R2, key: "Chill" }, title: "Chill", subtitle: "", type: "Playlist", service: "sonos", rooms: ["Kitchen"],
      image: "", suggest: "Chill", chosen: null, excluded: false },
    { tab: "movies", play: { kind: "movie", title_id: 9, title: "Alien" }, title: "Alien", subtitle: "1979", type: "Movie", service: "movies",
      rooms: ["Cinewall"], image: "", suggest: "Alien", chosen: null, excluded: false },
  ];
  let all_on = false;
  const fav_calls = [];
  const fav_hass = (states) => ({
    states, user: { is_admin: true },
    callService: (domain, service, data) => { fav_calls.push([domain, service, data]); return Promise.resolve(); },
    callWS: (msg) => {
      fav_calls.push(["ws", msg.type, msg]);
      if (msg.type === "artiled_control/favorites") {
        return Promise.resolve({ items: fav_items, errors: [], all_favorites: all_on, tabs: ["albums", "playlists", "movies"],
                                 kinds_on: all_on ? ["albums", "movies"] : [],
                                 services: [{ id: "sonos", name: "Sonos" }, { id: "movies", name: "Movies" }] });
      }
      if (msg.type === "config/entity_registry/get") return Promise.resolve({ aliases: [null, "Thriller album"] });
      return Promise.resolve({});
    },
  });
  const fav_config = () => ({ rooms: [{ entity: "media_player.kitchen", name: "Kitchen" }], zones: [], units: [
    { kind: "room", entity: "media_player.kitchen", name: "Kitchen", ident: "c0ffee000001", player: true,
      activities: [{ id: 20, name: "Listen to Sonos" }], chosen: [] },
    { kind: "room", entity: "media_player.garage", name: "Garage", ident: "c0ffee000001", player: false,
      activities: [{ id: 40, name: "TV" }], chosen: [] }] });
  const fav_card = async () => {
    const c = win.document.createElement("artiled-control-voice-card");
    c.setConfig(fav_config());
    win.document.body.appendChild(c);
    c.hass = fav_hass({ "media_player.kitchen": { state: "off", attributes: { friendly_name: "Kitchen" } } });
    await settle();
    return c;
  };
  let fc = await fav_card();
  let fr = fc.shadowRoot;
  const ftab = fr.querySelector('.tab[data-t="favourites"]');
  check("favourites: a Favourites tab", !!ftab && ftab.textContent.startsWith("Favourites"));
  ftab.click();
  await settle();
  check("favourites: ONE list for the Control - no room to open first",
        fr.querySelectorAll('.panel[data-p="favourites"] details').length === 0 && fr.querySelectorAll(".favlist").length === 1);
  const sw = fr.querySelector('.panel[data-p="favourites"] .allfav .pill');
  check("favourites: the sub-tabs in the webapp's order, each with its count; Artists absent (none, not 2.02)",
        Array.from(fr.querySelectorAll(".kindtabs .stab")).map((b) => b.textContent).join("|") === "Albums (2)|Playlists (1)|Movies (0)");
  /* 0.3.25: one list, grouped per SERVICE first (Michel: "the same in all rooms ... but NOT in all services") */
  check("favourites: grouped per service first - Sonos (3), Movies (1), as the webapp names them",
        Array.from(fr.querySelectorAll(".svtabs .stab")).map((b) => b.textContent).join("|") === "Sonos (3)|Movies (1)");
  check("favourites: the switch of the sub-tab at its top - 'Make all albums available to voice' (off, as stored)",
        sw && !sw.classList.contains("on") && /Make all albums available to voice/.test(fr.querySelector(".allfav").textContent));
  let frows = Array.from(fr.querySelectorAll(".favlist .fav"));
  check("favourites: the Albums sub-tab's rows, read when the tab opens",
        frows.length === 2 && fav_calls.some((c) => c[1] === "artiled_control/favorites"));
  check("favourites: the cover through Home Assistant's proxy; none without one",
        frows[0].querySelector("img.thumb").getAttribute("src").startsWith("/api/media_player_proxy/") && !frows[1].querySelector("img"));
  check("favourites: title and artist only (Michel: no echo of what plays it)",
        frows[0].querySelector(".opt__name small").textContent === "Spotify" && !/Kitchen|Cinewall/.test(frows[0].textContent));
  check("favourites: an added one ticked with its name (no room in it); one not added offers the suggestion",
        frows[0].querySelector(".tick").classList.contains("on") && frows[0].querySelector("input").value === "Thriller" &&
        !frows[1].querySelector(".tick").classList.contains("on") && frows[1].querySelector("input").placeholder === "Radio 2");
  check("favourites: an added one has its aliases field", !!fr.querySelector('.favlist .aliases input[data-e="scene.thriller"]'));
  fav_calls.length = 0;
  const keep_row = frows[0];
  fav_items = fav_items.map((it, n) => n === 1 ? { ...it, chosen: { name: "Radio 2", auto: false, entity_id: "scene.radio_2" } } : it);
  frows[1].querySelector(".tick").click();
  await settle();
  check("favourites: a tick changes THAT row in place - the other rows are the same elements (no re-render, no collapse)",
        fr.querySelectorAll(".favlist .fav")[0] === keep_row && !!fr.querySelector('.favlist .aliases input[data-e="scene.radio_2"]'));
  const addc = fav_calls.find((c) => c[1] === "add_voice_favorite");
  check("favourites: + adds it for the Control, with the Control's own play and the name",
        addc && JSON.stringify(addc[2]) === JSON.stringify({ control: "c0ffee000001", play: R2, title: "Radio 2", type: "Album", name: "Radio 2" }),
        JSON.stringify(addc));
  fav_calls.length = 0;
  frows[0].querySelector(".tick").click();
  await settle();
  const remc = fav_calls.find((c) => c[1] === "remove_voice_favorite");
  check("favourites: ✓ removes it by its play", remc && JSON.stringify(remc[2]) === JSON.stringify({ control: "c0ffee000001", play: THR }));
  fav_calls.length = 0;
  all_on = true;   /* what the integration stores */
  sw.click();
  await settle();
  const allc = fav_calls.find((c) => c[1] === "set_voice_favorites_all");
  check("favourites: the switch sets the Control's setting FOR THAT KIND",
        allc && JSON.stringify(allc[2]) === JSON.stringify({ control: "c0ffee000001", on: true, kind: "albums" }), JSON.stringify(allc));
  check("favourites: ...then shows what the integration STORED, read back",
        fav_calls.filter((c) => c[1] === "artiled_control/favorites").length >= 1 &&
        fr.querySelector('.allfav .pill').classList.contains("on"));
  all_on = false;   /* the integration did not keep it */
  fr.querySelector('.allfav .pill').click();
  await settle();
  all_on = false;
  fr.querySelector('.allfav .pill').classList.add("on");
  fav_calls.length = 0;
  fr.querySelector('.allfav .pill').click();
  await settle();
  check("favourites: a switch the integration did not store shows as it is stored, not as clicked",
        !fr.querySelector('.allfav .pill').classList.contains("on"));
  all_on = true;
  await fc._fav_fill(fr.querySelector(".favlist"));
  fr.querySelector('.stab[data-k="playlists"]').click();
  check("favourites: Playlists: its row, its own switch (off: the setting is on for albums and movies)",
        Array.from(fr.querySelectorAll(".favlist .fav")).map((r) => r.querySelector(".opt__name").firstChild.textContent).join() === "Chill" &&
        !fr.querySelector(".allfav .pill").classList.contains("on") && /Make all playlists available to voice/.test(fr.querySelector(".allfav").textContent));
  fr.querySelector('.svtabs .stab[data-s="movies"]').click();
  fr.querySelector('.stab[data-k="movies"]').click();
  check("favourites: the Movies service shows the film", Array.from(fr.querySelectorAll(".favlist .fav")).map((r) => r.querySelector(".opt__name").firstChild.textContent).join() === "Alien");
  check("favourites: Movies: its switch on", fr.querySelector(".allfav .pill").classList.contains("on"));
  fav_calls.length = 0;
  fr.querySelector(".allfav .pill").click();
  await settle();
  check("favourites: the Movies switch speaks for movies only",
        JSON.stringify((fav_calls.find((c) => c[1] === "set_voice_favorites_all") || [])[2]) ===
        JSON.stringify({ control: "c0ffee000001", on: false, kind: "movies" }));
  fr.querySelector('.svtabs .stab[data-s="sonos"]').click();
  fr.querySelector('.stab[data-k="albums"]').click();
  all_on = false;

  /* Michel 2026-10-26: "when I click a favourite, immediately it closes up" - and on a rename or an alias.
     The integration reloads, the page gets a NEW card: the tab, the typed text, the focus and the scroll stay. */
  fc.remove();
  fc = await fav_card();
  fr = fc.shadowRoot;
  frows = Array.from(fr.querySelectorAll(".favlist .fav"));
  const name_in = frows[0].querySelector("input");
  name_in.focus();
  name_in.value = "Thriller tonight";
  name_in.dispatchEvent(new win.Event("input"));
  Object.defineProperty(win, "scrollY", { value: 420, configurable: true });
  fav_calls.length = 0;
  name_in.dispatchEvent(new win.Event("change"));
  await settle();
  check("favourites: a rename sends the new name", fav_calls.some((c) => c[1] === "add_voice_favorite" && c[2].name === "Thriller tonight"));
  scrolled.length = 0;
  fc.remove();
  fc = await fav_card();
  fr = fc.shadowRoot;
  frows = Array.from(fr.querySelectorAll(".favlist .fav"));
  check("after the reload: still on the Favourites tab", (fr.querySelector(".tab.on") || {}).dataset.t === "favourites");
  check("after the reload: the list is there (nothing to open again)", frows.length === 2);
  check("after the reload: the typed name stays until the integration carries it", frows[0].querySelector("input").value === "Thriller tonight");
  check("after the reload: the field has the focus again", fr.activeElement === frows[0].querySelector("input"));
  check("after the reload: the page scrolled back to where it was", scrolled.includes(420), JSON.stringify(scrolled));
  const al = fr.querySelector('.favlist .aliases input[data-e="scene.thriller"]');
  al.focus();
  al.value = "Thriller album, MJ";
  al.dispatchEvent(new win.Event("change"));
  await settle();
  fc.remove();
  fc = await fav_card();
  fr = fc.shadowRoot;
  check("an alias edit: after the reload the alias field has the focus again",
        fr.activeElement === fr.querySelector('.favlist .aliases input[data-e="scene.thriller"]'));
  fc.remove();
  fc = await fav_card();
  fc.shadowRoot.querySelector('.svtabs .stab[data-s="movies"]').click();
  fc.shadowRoot.querySelector('.stab[data-k="movies"]').click();
  fc.remove();
  fc = await fav_card();
  check("after a reload: the same service and sub-tab (Movies)", (fc.shadowRoot.querySelector(".kindtabs .stab.on") || {}).dataset.k === "movies"
        && (fc.shadowRoot.querySelector(".svtabs .stab.on") || {}).dataset.s === "movies");
  fc.shadowRoot.querySelector('.svtabs .stab[data-s="sonos"]').click();
  fc.shadowRoot.querySelector('.stab[data-k="albums"]').click();
  fc.remove();

  /* 0.3.24: the card Home Assistant rebuilds (a voice favourite added) draws the list it had AT ONCE */
  const slow = { hold: true, kept: 0 };
  const slow_hass = (states) => ({
    ...fav_hass(states),
    callWS: (msg) => {
      fav_calls.push(["ws", msg.type, msg]);
      if (msg.type === "artiled_control/favorites") {
        if (slow.hold) return new Promise(() => {});
        return Promise.resolve({ items: fav_items, errors: [], all_favorites: all_on, tabs: ["albums", "playlists", "movies"],
                                 kinds_on: [], kept: msg.fresh ? 0 : slow.kept });
      }
      return Promise.resolve({});
    },
  });
  const rc = win.document.createElement("artiled-control-voice-card");
  rc.setConfig(fav_config());
  win.document.body.appendChild(rc);
  rc.hass = slow_hass({ "media_player.kitchen": { state: "off", attributes: { friendly_name: "Kitchen" } } });
  rc.shadowRoot.querySelector('.tab[data-t="favourites"]').click();
  await settle();
  check("rebuilt card: the Favourites list is there at once from what this browser kept, though the integration has not answered",
        rc.shadowRoot.querySelectorAll(".favlist .fav").length === 2);
  check("...and it was kept in sessionStorage too (a reload of the dashboard draws at once)",
        Object.keys(win.sessionStorage).some((k) => k.startsWith("articontrol_kept:fav|media_player.kitchen")));
  rc.remove();
  slow.hold = false;
  slow.kept = 400;
  fav_calls.length = 0;
  const rc2 = win.document.createElement("artiled-control-voice-card");
  rc2.setConfig(fav_config());
  win.document.body.appendChild(rc2);
  rc2.hass = slow_hass({ "media_player.kitchen": { state: "off", attributes: { friendly_name: "Kitchen" } } });
  rc2.shadowRoot.querySelector('.tab[data-t="favourites"]').click();
  await settle();
  await settle();
  const favws = fav_calls.filter((c) => c[0] === "ws" && c[1] === "artiled_control/favorites");
  check("an answer the integration kept (its age) is asked again with fresh=true, once",
        favws.length === 2 && !favws[0][2].fresh && favws[1][2].fresh === true, JSON.stringify(favws.map((c) => c[2])));
  rc2.remove();

  /* --- the room's Favorites (0.3.25): one list per service, played in THIS room --- */
  const RB = { kind: "music", source: "music_album", key: "8", title: "Thriller" };
  const room_items = [
    { tab: "albums", play: THR, title: "Thriller", subtitle: "Michael Jackson", type: "Album", service: "sonos", image: "", here: true },
    { tab: "albums", play: RB, title: "Thriller", subtitle: "Michael Jackson", type: "Album", service: "roon", image: "", here: false },
    { tab: "movies", play: { kind: "movie", title_id: 9, title: "Alien" }, title: "Alien", subtitle: "1979", type: "Movie", service: "movies", image: "", here: true },
  ];
  const room_calls = [];
  let refuse = "";
  const room_hass = {
    states: { "media_player.kitchen": { state: "off", attributes: { friendly_name: "Kitchen" } } }, user: { is_admin: false },
    callService: () => Promise.resolve(),
    callWS: (msg) => {
      room_calls.push(msg);
      if (msg.type === "artiled_control/favorites") {
        return Promise.resolve({ items: room_items, errors: [], tabs: ["albums", "movies"], kinds_on: [], kept: 0,
                                 services: [{ id: "sonos", name: "Sonos" }, { id: "roon", name: "Roon" }, { id: "movies", name: "Movies" }] });
      }
      if (msg.type === "artiled_control/play_favorite") {
        return refuse ? Promise.reject({ code: "not_played", message: refuse }) : Promise.resolve({ room: "Kitchen", activity: 20, state: "attached" });
      }
      return Promise.resolve({});
    },
  };
  const fvc = win.document.createElement("artiled-control-favorites-card");
  fvc.setConfig({ entity: "media_player.kitchen" });
  win.document.body.appendChild(fvc);
  const fv_notes = [];
  fvc.addEventListener("hass-notification", (e) => fv_notes.push(e.detail.message));
  fvc.hass = room_hass;
  await settle();
  const fvr = fvc.shadowRoot;
  check("room favorites: grouped per service - Sonos | Roon | Movies",
        Array.from(fvr.querySelectorAll(".ftab")).map((b) => b.textContent).join("|") === "Sonos|Roon|Movies");
  check("room favorites: the first service's (Sonos) Thriller only - the Roon one is under Roon, not merged",
        Array.from(fvr.querySelectorAll(".view .tile-name")).map((n) => n.textContent).join() === "Thriller");
  fvr.querySelector(".view .tile").click();
  await settle();
  const rpf = room_calls.find((m) => m.type === "artiled_control/play_favorite");
  check("room favorites: a tap plays it in THIS room, by ITS play (no room guessed, no other service's id)",
        rpf && rpf.entity_id === "media_player.kitchen" && JSON.stringify(rpf.play) === JSON.stringify(THR));
  fvr.querySelector('.ftab[data-s="roon"]').click();
  const away = fvr.querySelector(".view .tile");
  check("room favorites: a favourite this room has no activity for is dimmed", away.classList.contains("away"));
  refuse = "Kitchen has no activity for Roon - nothing was sent";
  away.click();
  await settle();
  check("room favorites: its tap says why, the tile stays", fv_notes[fv_notes.length - 1] === refuse &&
        away.classList.contains("failed") && fvr.querySelectorAll(".view .tile").length === 1);
  fvc.remove();
  dom.window.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
