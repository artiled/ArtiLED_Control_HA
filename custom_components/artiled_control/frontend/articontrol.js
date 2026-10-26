// Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
/* ArtiCONTROL - Home Assistant frontend module (plan section 3, slice S2).
 *
 *   ll-strategy-dashboard-artiled-control   builds the ArtiCONTROL dashboard from
 *                                           the registries, nothing hard-coded
 *   artiled-control-unit-card               one room or zone: the webapp's Home
 *                                           tile, plus its activities as buttons
 *   artiled-control-player-card             the unit page: the remote's keys,
 *                                           volume, Now Playing, then the
 *                                           activity rows
 *   artiled-control-voice-card              the Voice page: exposure, aliases and
 *                                           voice activities (admin only)
 *
 * ⚠ A PORT, NOT A REINTERPRETATION. The tile is the Control webapp's
 * (app/includes/tiles.php + app/js/paint.js + app.css .app-tile), the activity
 * rows are its unit page's (includes/rows.php, app.css .app-activity), and the
 * player is its remote's (app/remote/index.php, app.css .app-remote, .app-key).
 * The voice card is the ArtiLED Hub integration's (artiled-hub.js
 * ArtiLedVoiceCard). Values below come from those sources; do not adjust them to
 * taste.
 *
 * ⚠ NEVER THE HUB'S NAMES (plan 3.2). The ArtiLED Hub integration owns the tags
 * artiled-tile-card / -entity-card / -colors-card / -voice-card, the strategy
 * ll-strategy-dashboard-artiled-hub, the static path /artiled_hub_frontend and
 * the iconset `artiled`. A second define() of a tag fails silently, and the
 * Hub's `artiled` iconset resolves room icons from the HUB's cache - if this
 * module registered `artiled` first, the Hub's room icons would break in that
 * load order only. So everything here is `artiled-control-*` / `articontrol`,
 * and this file never reads or writes window.customIconsets.artiled.
 *
 * ⚠ START-ONLY (Michel 2026-10-25). An activity button starts that activity;
 * nothing turns an activity off. Off is the unit's Off activity: the unit's
 * media_player turn_off.
 */

const DOMAIN = "artiled_control";

/* MediaPlayerEntityFeature bits (homeassistant/components/media_player/const.py). */
const F = {
  PAUSE: 1, VOLUME_SET: 4, VOLUME_MUTE: 8, PREVIOUS_TRACK: 16, NEXT_TRACK: 32,
  TURN_ON: 128, TURN_OFF: 256, VOLUME_STEP: 1024, SELECT_SOURCE: 2048,
  STOP: 4096, PLAY: 16384, SEEK: 2, SHUFFLE_SET: 32768, REPEAT_SET: 262144,
};

/* ------------------------------------------------------------------ *
 * The ported rules - pure, and tested in tests/test_frontend.js.
 * ------------------------------------------------------------------ */

/* Classification comes from the DEVICE IDENTIFIER, never from an entity_id or a
   name (the Hub rule, plan 3.3). Identifiers are the ones __init__.py registers:
   "<ident>" for the Control, "<ident>_room_7" / "<ident>_zone_3" for a unit -
   unit_unique_id() in entity_ids.py. */
function classify(device) {
  for (const pair of (device && device.identifiers) || []) {
    if (pair[0] !== DOMAIN) continue;
    const m = String(pair[1]).match(/^(.+)_(room|zone)_(\d+)$/);
    if (m) return { kind: m[2], id: Number(m[3]), ident: m[1] };
    /* S3c: a Control device's own HA device (its remote) - never a Control */
    const dv = String(pair[1]).match(/^(.+)_device_(\d+)$/);
    if (dv) return { kind: "device", id: Number(dv[2]), ident: dv[1] };
    return { kind: "control", id: 0, ident: String(pair[1]) };
  }
  return null;
}

/* The media_player says on with on, playing and paused (units.py media_state);
   off and anything unknown is not on. */
function unit_on(state) {
  if (!state) return false;
  return ["on", "playing", "paused", "idle", "buffering"].includes(state.state);
}

function is_unavailable(state) {
  return !state || state.state === "unavailable" || state.state === "unknown";
}

function attrs(state) {
  return (state && state.attributes) || {};
}

/* app_tile_status() over app_unit_record()'s status (includes/units.php):
   the activity's name, "<name> · Starting" while it starts, "Off · Starting"
   while the Off activity starts, else On / Off. `starting` says the moving dots
   follow (feedback_waiting_text_has_moving_dots). */
function tile_status(state) {
  if (is_unavailable(state)) return { text: "Unavailable", starting: false };
  const a = attrs(state);
  const on = unit_on(state);
  if (a.starting) {
    const name = on ? String(a.starting_name || a.source || "") : "";
    return { text: name ? `${name} · Starting` : (on ? "Starting" : "Off · Starting"), starting: true };
  }
  if (on) return { text: String(a.source || "") || "On", starting: false };
  return { text: "Off", starting: false };
}

/* The running activity's row in the unit's activities, or null. */
function running_activity(state) {
  const a = attrs(state);
  const id = Number(a.activity_id || 0);
  if (!id) return null;
  return (a.activities || []).find((r) => Number(r.id) === id) || null;
}

/* app_tile_icon(): the running activity's icon while on, the unit's own
   otherwise. While an activity starts the webapp blanks activity_icon
   (app_unit_record), so the unit's icon shows. */
function tile_icon(state) {
  const a = attrs(state);
  if (unit_on(state) && !a.starting) {
    const row = running_activity(state);
    if (row && row.icon) return String(row.icon);
  }
  return String(a.unit_icon || "");
}

/* The artwork: only while on and not starting (units.php $art; the
   integration already suppresses it while starting). entity_picture is Home
   Assistant's own proxy URL - never the Control's address or token. */
function tile_art(state) {
  const a = attrs(state);
  if (!unit_on(state) || a.starting) return "";
  return String(a.entity_picture || "");
}

/* The webapp tile's wash: artwork under its scrim, the light "on" card without
   artwork, nothing when off (tiles.php, paint.js tile()). */
function tile_wash(state) {
  if (tile_art(state)) return "art";
  return unit_on(state) ? "on" : "";
}

/* paint.js volume_slider(): shown while on and the volume can be set; a level
   that is NOT KNOWN shows greyed with the knob in the middle and no number
   (G2) - never 50 %, never a guess. */
function volume_view(state) {
  const a = attrs(state);
  const settable = unit_on(state) && (Number(a.supported_features || 0) & F.VOLUME_SET) !== 0;
  const known = settable && typeof a.volume_level === "number";
  return {
    shown: settable,
    known,
    p: known ? Math.max(0, Math.min(1, a.volume_level)) : 0.5,
    display: settable && a.volume_display !== null && a.volume_display !== undefined
      ? String(a.volume_display) : "",
    max: settable ? Number(a.volume_max || 0) : 0,
    scale: String(a.volume_scale || "absolute"),
  };
}

/* controls.js level_for_percent(): volume_level_for_percent() in
   artiled_steps.py, whose round() goes half to even (4.5 -> 4). */
function level_for_percent(percent, max) {
  const x = percent * max / 100;
  const f = Math.floor(x);
  if (Math.abs(x - f - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}

/* controls.js volume_text(): artiled_volume_display() in includes/unit.php. */
function volume_text(level, scale) {
  if (scale === "db") {
    const db = level - 80;
    return (db > 0 ? "+" : "") + db.toFixed(1) + " dB";
  }
  return String(level);
}

/* The number over the knob while it moves: the device's own number, computed
   as the webapp computes it. ⚠ Without the device's maximum there is no number
   at all - never a 0-100 percentage presented as the volume. */
function slider_value_text(percent, max, scale) {
  if (!(max > 0)) return "";
  return volume_text(level_for_percent(percent, max), scale || "absolute");
}

/* app_remote_key_shown() (includes/remote.php) / key_shown() (paint.js): only
   the keys the activity uses (#570). entry = {buttons, play_pause}, or null when
   the activity did not say - then every key shows, the toggle not. */
function key_shown(key, entry) {
  const toggle = !!entry && entry.play_pause === "toggle";
  if (key === "play_pause") {
    return toggle && Array.isArray(entry.buttons) && entry.buttons.includes("play_pause");
  }
  if (!entry || !Array.isArray(entry.buttons) || key === "power") return true;
  if (toggle && (key === "play" || key === "pause")) return false;
  return entry.buttons.includes(key);
}

const TRANSPORT = [
  /* key, FA glyph (app/remote/index.php), MDI fallback, service, feature bits */
  ["previous", "backward-step", "mdi:skip-previous", "media_previous_track", F.PREVIOUS_TRACK],
  ["stop", "stop", "mdi:stop", "media_stop", F.STOP],
  ["play", "play", "mdi:play", "media_play", F.PLAY],
  ["pause", "pause", "mdi:pause", "media_pause", F.PAUSE],
  ["play_pause", "play-pause", "mdi:play-pause", "media_play_pause", F.PLAY | F.PAUSE],
  ["next", "forward-step", "mdi:skip-next", "media_next_track", F.NEXT_TRACK],
];

function keys_entry(state) {
  const a = attrs(state);
  return Array.isArray(a.buttons)
    ? { buttons: a.buttons, play_pause: a.play_pause === "toggle" ? "toggle" : "separate" }
    : null;
}

/* The transport row: only while on, only the keys the running activity uses,
   and only what the entity supports (Home Assistant refuses the rest). */
function transport_keys(state) {
  if (!unit_on(state) || attrs(state).starting) return [];
  const entry = keys_entry(state);
  const features = Number(attrs(state).supported_features || 0);
  return TRANSPORT.filter(([key, , , , bits]) => key_shown(key, entry) && (features & bits) !== 0)
    .map(([key]) => key);
}

/* Mute: the webapp's mute_toggle key, offered while the volume is. */
function mute_shown(state) {
  if (!unit_on(state) || attrs(state).starting) return false;
  const features = Number(attrs(state).supported_features || 0);
  return (features & F.VOLUME_MUTE) !== 0 && key_shown("mute_toggle", keys_entry(state));
}

/* The now-playing words: title over "artist · album" (paint.js remote_info,
   join()). Text only. */
function np_words(state) {
  const a = attrs(state);
  if (!unit_on(state) || a.starting) return { title: "", meta: "" };
  const title = String(a.media_title || "").trim();
  const meta = [a.media_artist, a.media_album_name]
    .map((p) => String(p || "").trim()).filter((p) => p !== "").join(" · ");
  return { title, meta: title ? meta : "" };
}

/* The progress bar (paint.js remote_bar): position moved on by the time since
   the daemon read it while playing. null = no bar. */
function np_position(state, now_ms) {
  const a = attrs(state);
  if (!unit_on(state) || a.starting) return null;
  const duration = Number(a.media_duration || 0);
  if (!(duration > 0) || typeof a.media_position !== "number") return null;
  let position = a.media_position;
  if (state.state === "playing" && a.media_position_updated_at) {
    const at = Date.parse(a.media_position_updated_at);
    if (!isNaN(at)) position += Math.max(0, (now_ms - at) / 1000);
  }
  return { position: Math.max(0, Math.min(duration, position)), duration };
}

/* Shuffle, repeat and seek (app/js/playback.js): what the PLAYER said, as the
   integration publishes it - shown only while the entity offers it (it does
   only once the player has said; unknown is never shown as off). */
const REPEAT_NEXT = { off: "all", all: "one", one: "off" };
function play_modes(state) {
  const a = attrs(state);
  const features = Number(a.supported_features || 0);
  if (!unit_on(state) || a.starting) return { shuffle: null, repeat: null, can_seek: false };
  return {
    shuffle: (features & F.SHUFFLE_SET) && typeof a.shuffle === "boolean" ? a.shuffle : null,
    repeat: (features & F.REPEAT_SET) && REPEAT_NEXT[a.repeat] ? a.repeat : null,
    can_seek: (features & F.SEEK) !== 0 && Number(a.media_duration || 0) > 0,
  };
}

/* playback.js seekSeconds: the second a place on the bar points at. */
function seek_to(fraction, duration) {
  const f = Math.max(0, Math.min(1, Number(fraction) || 0));
  const d = Math.max(0, Math.floor(Number(duration) || 0));
  return Math.min(d, Math.round(f * d));
}

/* paint.js clock(). */
function clock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const mm = (h > 0 && m < 10) ? "0" + m : String(m);
  return (h > 0 ? h + ":" + mm : mm) + ":" + (r < 10 ? "0" + r : String(r));
}

/* The activity rows (paint.js activities(), rows.php): the running activity in
   the lighter box, a starting one the same plus "Starting" (Michel 2026-09-29:
   the active look, never blue). The Off activity is not among them (it is never
   offered as a source); it is the separate Off row. */
function activity_rows(state) {
  const a = attrs(state);
  const on = unit_on(state);
  const current = Number(a.activity_id || 0);
  return (a.activities || []).map((r) => {
    const running = on && Number(r.id) === current;
    return {
      id: Number(r.id), name: String(r.name || ""), icon: String(r.icon || ""),
      running, starting: running && !!a.starting,
    };
  });
}

/* The Off row: shown only when the unit HAS an Off activity - a button that can
   only fail ("No Off activity for ...", Q5) is not offered. Lit while the unit
   is off; "Starting" while the Off activity starts. */
function off_row(state) {
  const a = attrs(state);
  if (!(Number(a.off_activity || 0) > 0)) return null;
  const off = !unit_on(state) && !is_unavailable(state);
  return { running: off, starting: off && !!a.starting };
}

/* "Sonos in the Kitchen" - voice.default_spoken_name(): the activity's own
   leading verb dropped, "the" before the room. */
const LEADING_VERBS = ["listen to ", "watch ", "play ", "start "];
function voice_suggest(activity, unit) {
  let a = String(activity).trim();
  const verb = LEADING_VERBS.find((v) => a.toLowerCase().startsWith(v) && a.length > v.length);
  if (verb) a = a.slice(verb.length).trim();
  let u = String(unit).trim();
  if (!u.toLowerCase().startsWith("the ")) u = `the ${u}`;
  return `${a} in ${u}`;
}

/* "Listen to Sonos in the Kitchen" - voice.long_spoken_name(): the full
   activity name, verb kept. The scene's alias, and the suggestion when the
   short form is taken. */
function voice_suggest_long(activity, unit) {
  let u = String(unit).trim();
  if (!u.toLowerCase().startsWith("the ")) u = `the ${u}`;
  return `${String(activity).trim()} in ${u}`;
}

/* voice.suggest_spoken_name(): the short form unless it is in use ("Listen to
   Zidoo" and "Watch Zidoo" in one Kitchen), then the long one. */
function suggest_name(activity, unit, in_use) {
  const short = voice_suggest(activity, unit);
  return in_use(short) ? voice_suggest_long(activity, unit) : short;
}

/* Aliases as the field shows them, and back. HA 2026.10 keeps the entity's own
   computed name IN its aliases list (null over the websocket); the field shows
   only the user's words, and a save puts the null back - without it the
   spoken name itself would stop being a name Assist answers to. */
function aliases_shown(raw) {
  return (raw || []).filter((a) => typeof a === "string" && a.trim() !== "").join(", ");
}

function aliases_to_save(text, raw) {
  const typed = String(text || "").split(",").map((a) => a.trim()).filter(Boolean);
  const keep_computed = raw === undefined || raw === null || (raw || []).some((a) => a === null);
  return keep_computed ? [null, ...typed] : typed;
}

/* Unit subview paths: "zone-3"; with more than one Control the ident's tail is
   added, because two Controls can each have a zone 3. */
function unit_path(unit, many) {
  const base = `${unit.kind}-${unit.id}`;
  if (!many) return base;
  const tail = String(unit.ident || "").toLowerCase().replace(/[^a-z0-9]/g, "").slice(-6);
  return tail ? `${base}-${tail}` : base;
}

/* "fas fa-tv" / "fa-solid fa-tv" / "tv" -> "tv" - the Hub's fa_name(). */
function fa_name(icon) {
  const parts = String(icon || "").trim().split(/\s+/).filter(Boolean);
  const token = parts.filter((p) => p.startsWith("fa-")).pop() || parts.pop() || "";
  return token.replace(/^fa-/, "");
}

function esc(text) {
  return String(text === undefined || text === null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* An error a person can act on (feedback_problem_comes_with_a_solution). The
   integration's own errors already say what to do ("No Off activity for
   Kitchen"); anything without words gets the generic remedy. Toasts ONLY on
   errors (feedback_messages_only_on_error). */
function error_text(err) {
  const msg = err && (err.message || (err.error && err.error.message));
  return msg ? String(msg)
    : "The Control did not answer. Check that it is on and reachable, then try again.";
}

function toast(node, err) {
  node.dispatchEvent(new CustomEvent("hass-notification", {
    detail: { message: error_text(err) }, bubbles: true, composed: true,
  }));
}

/* ------------------------------------------------------------------ *
 * Icons: the `articontrol` iconset.
 * ------------------------------------------------------------------ */

/* The ArtiLED mark - the same app/img/icon-512.png on Hub and Control (md5
   equal), so the same measured path as the Hub's ARTILED_LOGO_PATH. */
const ARTICONTROL_LOGO_PATH =
  "M2.37,21.52 L2.37,12.14 A9.61,9.61 0 0 1 21.59,12.14 L21.59,21.52 " +
  "L16.57,21.52 L16.57,12.14 A4.59,4.59 0 0 0 7.38,12.14 L7.38,21.52 Z " +
  "M9.54,15.09 a2.44,2.44 0 1 0 4.88,0 a2.44,2.44 0 1 0 -4.88,0 Z";

/* ⚠ Font Awesome Pro paths are NOT shipped: icons.py reads them from the
   customer's own Control and caches them in the config directory, served here.
   Cache-busted with this module's own ?v= (see the Hub's fa_url()). */
const FA_BASE = "/artiled_control_icons/fa-icons.json";

function fa_url() {
  try {
    const tag = document.querySelector('script[src*="articontrol.js"]');
    const v = tag && new URL(tag.src, location.href).searchParams.get("v");
    return v ? `${FA_BASE}?v=${encodeURIComponent(v)}` : FA_BASE;
  } catch (e) {
    return FA_BASE;
  }
}

let fa_icons_promise = null;

function fa_icons() {
  if (!fa_icons_promise) {
    fa_icons_promise = fetch(fa_url())
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}));   /* an icon is cosmetic; never break the dashboard */
  }
  return fa_icons_promise;
}

/* `articontrol:logo`, and `articontrol:fa-<name>` for a Control icon. Unknown
   -> the mark rather than an empty box. ⚠ Our own name only: the Hub's
   `artiled` iconset is never touched (see the header). */
function register_iconset() {
  window.customIconsets = window.customIconsets || {};
  if (window.customIconsets.articontrol) return;
  window.customIconsets.articontrol = async (name) => {
    if (name === "logo" || name === "a") {
      return { path: ARTICONTROL_LOGO_PATH, viewBox: "0 0 24 24" };
    }
    const entry = (await fa_icons())[fa_name(name.replace(/^fa-/, ""))];
    if (!entry) return { path: ARTICONTROL_LOGO_PATH, viewBox: "0 0 24 24" };
    return { path: entry[0], viewBox: entry[1] };
  };
}

/* The icon name for a Font Awesome class: articontrol:fa-<name> when the
   Control served it, else the fallback (an MDI icon for the player's keys, so
   a Play key never shows the logo). */
/* The cache key of a class: its name, or "light-<name>" for the light style
   (icons.py icon_key). */
function fa_key(fa_class) {
  const name = fa_name(fa_class);
  return name && /(^|\s)(fa-light|fal)(\s|$)/.test(String(fa_class || "")) ? `light-${name}` : name;
}

function icon_name(fa_class, have, fallback) {
  const name = fa_key(fa_class);
  if (name && have && have[name]) return `articontrol:fa-${name}`;
  return fallback || "articontrol:logo";
}

const KIND_ICON = { room: "mdi:sofa", zone: "mdi:vector-rectangle" };

/* ------------------------------------------------------------------ *
 * The unit tile - app/includes/tiles.php + app/js/paint.js tile().
 * ------------------------------------------------------------------ */

/* Values from app.css: .app-tile (92.5 high, radius 16, #444, shadow), the
   inner box 90% x 85%, the top row 50 high, icon flex 1.5 at 34, text 3.5,
   name font_size.xl 600, status md; .app-switch (51x31 iOS switch at .8,
   purple #7a51a1 on); .app-tile__slider/__track/__knob; the wash for artwork
   (scrim to top right .75 -> .35, icon and name at .7) and for on without
   artwork (.24 box, .6 frame). The activity buttons are the unit page's Grid
   view at Small size (app.css .ad-grid .app-rows--list, .ad-size-small): three
   across, icon 22, name font_size.sm, the running one in the lighter box.
   ⚠ ONE departure: the webapp's cards are see-through (#444 at .55) because
   they sit on its dark page over the artwork. A Lovelace page is whatever theme
   the user picked, and on the light one the lit (running) card came out white
   on white - measured in a headless render. So the cards carry the Home card's
   own solid #444 and the lighter box is laid over it (the Hub's lesson: only
   what carries its own dark background may keep the app's white text).
   The moving dots are app.css app-searching-dots. */
const DOTS_STYLE = `
.dots::before, .dots::after { display: inline-block; width: 3ch; vertical-align: bottom; }
.dots::before { content: ''; }
.dots::after {
  content: '...'; text-align: left;
  -webkit-clip-path: inset(0 100% 0 0); clip-path: inset(0 100% 0 0);
  animation: app-searching-dots 1.4s steps(4, jump-none) infinite;
}
.dots.inline::before { display: none; }
@keyframes app-searching-dots {
  from { -webkit-clip-path: inset(0 100% 0 0); clip-path: inset(0 100% 0 0); }
  to   { -webkit-clip-path: inset(0 0 0 0); clip-path: inset(0 0 0 0); }
}
@media (prefers-reduced-motion: reduce) {
  .dots::after { animation: none; -webkit-clip-path: none; clip-path: none; }
}
`;

const TILE_STYLE = `
:host { display: block; }
.tile {
  position: relative; display: flex; align-items: center; justify-content: center;
  height: 92.5px; border-radius: 16px; background: #444;
  box-shadow: 0 2px 4px rgba(0,0,0,.2); overflow: hidden; color: #fff;
  font-family: -apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", Roboto, sans-serif;
}
.wash { position: absolute; inset: 0; border-radius: 16px; }
.wash.art { background-size: cover; background-position: center; }
.wash.art::after {
  content: ''; position: absolute; inset: 0; border-radius: 16px;
  background: linear-gradient(to top right, rgba(0,0,0,.75), rgba(0,0,0,.35));
}
.wash.art ~ .inner .icon, .wash.art ~ .inner .name { opacity: .7; }
.wash.on { background: rgba(255,255,255,.24); box-shadow: inset 0 0 0 1px rgba(255,255,255,.6); }
.inner {
  position: relative; display: flex; flex-direction: column;
  justify-content: space-between; height: 85%; width: 90%;
}
.top { display: flex; align-items: center; height: 50px; }
.icon { flex: 1.5; display: flex; align-items: center; }
.icon ha-icon { --mdc-icon-size: 34px; }
.text { flex: 3.5; min-width: 0; }
.name { font-size: 20px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.caption { margin-left: 8px; font-size: 14px; font-weight: 400; color: rgba(255,255,255,.6); }
.status { font-size: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sw {
  cursor: pointer; flex: 0 0 auto; position: relative; display: inline-block;
  width: 51px; height: 31px; margin-top: -1px; border-radius: 15.5px;
  background: #767577; transform: scale(.8); transition: background-color .18s ease;
  -webkit-tap-highlight-color: transparent; touch-action: manipulation;
}
.sw::after {
  content: ''; position: absolute; top: 2px; left: 2px; width: 27px; height: 27px;
  border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.3);
  transition: left .18s ease;
}
.sw.on { background: #7a51a1; }
.sw.on::after { left: 22px; }
.sw.busy { opacity: .7; }
.sw.failed { background: #c0392b; }
.slider {
  --knob: 20px; display: flex; align-items: center; height: 20px;
  touch-action: none; cursor: pointer; -webkit-tap-highlight-color: transparent;
}
.slider[hidden] { display: none; }
.slider.unknown { opacity: .55; }
.slider.failed .track { outline: 1px solid rgba(192,57,43,.9); outline-offset: 3px; }
.track { position: relative; width: 100%; height: 3px; border-radius: 2px; background: currentColor; opacity: .55; }
.knob {
  position: absolute; top: 50%; left: calc(var(--p, 0) * (100% - var(--knob)));
  width: var(--knob); height: var(--knob); border-radius: 50%; background: currentColor;
  transform: translateY(-50%); box-shadow: 0 6px 10px rgba(0,0,0,.6);
  transition: transform 90ms ease-out, box-shadow 90ms ease-out;
}
.slider.grabbed .knob { transform: translateY(-50%) scale(1.2); box-shadow: 0 3px 6px rgba(0,0,0,.4); }
.unavail { opacity: .45; }
.acts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }
.acts[hidden] { display: none; }
.act {
  display: flex; flex-wrap: wrap; align-items: center; align-content: space-between;
  height: 92.5px; box-sizing: border-box; padding: 13px 9px 16px 19px; margin: 0;
  border: 0; border-radius: 16px; cursor: pointer; text-align: left;
  background: #444; box-shadow: 0 2px 4px rgba(0,0,0,.2);
  color: #fff; font: inherit;
  font-family: -apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", Roboto, sans-serif;
  -webkit-tap-highlight-color: transparent; touch-action: manipulation;
}
.act:active { background: linear-gradient(rgba(255,255,255,.1), rgba(255,255,255,.1)), #444; }
.act ha-icon { --mdc-icon-size: 22px; color: rgba(255,255,255,.5); }
.act .label {
  order: 1; flex: 0 0 100%; min-width: 0; height: 38px; display: flex; align-items: center;
  font-size: 14px; font-weight: 700; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.act .label span { overflow: hidden; text-overflow: ellipsis; }
.act.running { background: linear-gradient(rgba(255,255,255,.16), rgba(255,255,255,.16)), #444; box-shadow: inset 0 0 0 1px rgba(255,255,255,.3), 0 2px 4px rgba(0,0,0,.2); }
.act.running ha-icon { color: #fff; }
.act.failed { outline: 2px solid #b3261e; }
.act[disabled] { opacity: .45; cursor: default; }
@media (prefers-reduced-motion: reduce) { .sw, .sw::after { transition: none; } }
` + DOTS_STYLE + `
/* ---- the webapp's layouts (Settings > Layout, app/js/layout.js) ----------
   PORTED from the CONTROL's app/app.css (.layout-grid / .layout-square /
   .layout-list), rule for rule, as the Hub integration's 0.32.5 did from the
   Hub's; only the selectors change (.layout-grid .app-tile__text ->
   :host([layout="grid"]) .text). No layout attribute = Rows, the card above,
   which a unit's own page keeps (the webapp's unit page, .app-rows). Square
   carries every Grid rule, as html.layout-grid.layout-square does. The
   spacing between cards and the columns are the list's (TILES_STYLE). */

/* Grid - [icon | switch] on top, the NAME ALONE in a 38-high box below; no
   status line, no slider. Inner box padded 6/10, stacked from the top. */
:host([layout="grid"]) .inner,
:host([layout="square"]) .inner { padding: 6px 10px; justify-content: flex-start; }
:host([layout="grid"]) .top,
:host([layout="square"]) .top { display: flex; flex-wrap: wrap; align-items: center; height: auto; }
/* the Control's own cap (2026-10-24): never so wide that the switch wraps under
   the icon - the box is what the switch leaves, 41 px with its overhang + 1 */
:host([layout="grid"]) .icon,
:host([layout="square"]) .icon { flex: 0 0 auto; margin-right: 1px; max-width: calc(100% - 42px); }
/* the switch pins to the TOP of the row and hangs 10 past the padding */
:host([layout="grid"]) .sw,
:host([layout="square"]) .sw { margin-left: auto; align-self: flex-start; margin-right: -10px; }
/* the markup order is icon, text, switch: the text is ordered LAST or its
   100% basis pushes the switch onto a third line */
:host([layout="grid"]) .text,
:host([layout="square"]) .text {
  order: 1; flex: 0 0 100%; display: flex; flex-direction: column; justify-content: center; height: 38px;
}
:host([layout="grid"]) .status,
:host([layout="grid"]) .slider,
:host([layout="square"]) .status,
:host([layout="square"]) .slider { display: none; }
:host([layout="grid"]) .name,
:host([layout="square"]) .name { font-size: 18px; }   /* font_size.lg semi_bold */

/* Square - the Grid card as a square; its two lines go to the top and the foot */
:host([layout="square"]) .tile { height: auto; aspect-ratio: 1; }
:host([layout="square"]) .top { height: 100%; align-content: space-between; }

/* List - one slim line per room or zone: the Rows card's top line with no card
   round it, no wash and no slider (the settings row: padding 8, radius 8, the
   same press highlight). The divider between lines is the list's. */
:host([layout="list"]) .tile {
  height: auto; padding: 0 8px; border-radius: 8px; background: transparent;
  box-shadow: none; overflow: visible; -webkit-tap-highlight-color: transparent;
}
:host([layout="list"]) .tile:active {
  background: rgba(127, 127, 127, .15);
  background: color-mix(in srgb, var(--primary-text-color, #fff) 10%, transparent);
}
:host([layout="list"]) .wash,
:host([layout="list"]) .slider { display: none; }
:host([layout="list"]) .inner { width: 100%; height: auto; }
/* No card, so the words sit on the page. ⚠ Home Assistant's theme text colour,
   not the app's white: on its light theme white words would be invisible (the
   webapp's page is always dark). */
:host([layout="list"]) .icon,
:host([layout="list"]) .name,
:host([layout="list"]) .status { color: var(--primary-text-color, #fff); }
:host([layout="list"]) .caption { color: var(--secondary-text-color, rgba(255,255,255,.6)); }
`;

/* controls.js: only the knob moves the volume (Michel 2026-09-16) - a touch on
   the bare track does nothing; the thumb keeps the grip it was picked up with;
   one send in flight at a time and the value the finger left lands last. */
const KNOB = 20;
const GRAB = 25;
const SEND_MS = 5;
const VALUE_MS = 2000;
const SENT_HOLD_MS = 3000;

function bind_volume_slider(owner, slider, knob, on_value) {
  let dragging = false;
  let grab_dx = 0;
  let in_flight = false;
  let pending = null;
  let last_sent = null;
  const track = slider.querySelector(".track") || slider;
  const travel = (box) => Math.max(1, box.width - KNOB);
  const fraction = () => parseFloat(knob.style.getPropertyValue("--p")) || 0;
  const flush = () => {
    if (in_flight || pending === null) return;
    const level = pending;
    pending = null;
    if (level === last_sent) return;
    last_sent = level;
    in_flight = true;
    owner._hass.callService("media_player", "volume_set", {
      entity_id: owner._config.entity, volume_level: level / 100,
    }).then(() => slider.classList.remove("failed"))
      .catch((err) => { last_sent = null; slider.classList.add("failed"); toast(owner, err); })
      .then(() => { in_flight = false; window.setTimeout(flush, SEND_MS); });
  };
  const drive = (ev) => {
    const box = track.getBoundingClientRect();
    if (!(box.width > 0)) return;
    let p = (ev.clientX - grab_dx - box.left - KNOB / 2) / travel(box);
    p = Math.max(0, Math.min(1, p));
    knob.style.setProperty("--p", String(p));
    const level = Math.round(p * 100);
    if (on_value) on_value(level, true);
    pending = level;
    flush();
  };
  slider.addEventListener("pointerdown", (e) => {
    const box = track.getBoundingClientRect();
    const centre = box.left + KNOB / 2 + fraction() * travel(box);
    const dx = e.clientX - centre;
    e.stopPropagation();
    if (Math.abs(dx) > GRAB) return;
    e.preventDefault();
    dragging = true;
    if (slider.setPointerCapture) slider.setPointerCapture(e.pointerId);
    grab_dx = dx;
    last_sent = null;   /* a new drag sends its first level, even a repeat */
    slider.classList.remove("unknown");
    slider.classList.add("grabbed");
    drive(e);
  });
  slider.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    e.preventDefault();
    drive(e);
  });
  const release = (e) => {
    if (!dragging) return;
    dragging = false;
    e.preventDefault();
    e.stopPropagation();
    drive(e);
    slider.setAttribute("data-sent-at", String(Date.now()));
    grab_dx = 0;
    slider.classList.remove("grabbed");
    if (on_value) on_value(null, false);
  };
  slider.addEventListener("pointerup", release);
  slider.addEventListener("pointercancel", release);
  slider.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); });
}

/* paint.js volume_slider(): never while a finger is on it, and not in the 3 s
   after a send - the poll may still carry the level from before it. */
function paint_volume_slider(slider, knob, view) {
  if (!slider || slider.classList.contains("grabbed")) return;
  slider.hidden = !view.shown;
  const row = slider.closest ? slider.closest(".vol-row") : null;
  if (row) row.hidden = !view.shown;
  const sent_at = Number(slider.getAttribute("data-sent-at") || 0);
  if (Date.now() - sent_at > SENT_HOLD_MS) {
    slider.classList.toggle("unknown", view.shown && !view.known);
    if (row) row.classList.toggle("unknown", view.shown && !view.known);
    if (view.shown) knob.style.setProperty("--p", String(view.p));
  }
}

class ArtiControlUnitCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._have = null;
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("artiled-control-unit-card needs an entity");
    this._config = { activities: true, ...config };
    this._built = false;
    if (this.shadowRoot) this.shadowRoot.innerHTML = "";
  }

  getCardSize() { return this._config && this._config.activities ? 5 : 2; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._paint();
  }

  _build() {
    const c = this._config;
    this.shadowRoot.innerHTML = `
      <style>${TILE_STYLE}</style>
      <div class="tile">
        <div class="inner">
          <div class="top">
            <div class="icon"><ha-icon></ha-icon></div>
            <div class="text">
              <div class="name"><span class="n"></span><span class="caption"></span></div>
              <div class="status"><span class="st"></span><span class="dots inline" hidden></span></div>
            </div>
            <span class="sw" role="switch" tabindex="0"></span>
          </div>
          <div class="slider" hidden><div class="track"><span class="knob"></span></div></div>
        </div>
      </div>
      ${c.activities ? '<div class="acts" hidden></div>' : ""}
    `;
    const root = this.shadowRoot;
    this._el = {
      tile: root.querySelector(".tile"),
      inner: root.querySelector(".inner"),
      icon: root.querySelector(".icon ha-icon"),
      name: root.querySelector(".n"),
      caption: root.querySelector(".caption"),
      status: root.querySelector(".st"),
      dots: root.querySelector(".status .dots"),
      sw: root.querySelector(".sw"),
      slider: root.querySelector(".slider"),
      knob: root.querySelector(".knob"),
      acts: root.querySelector(".acts"),
    };
    this._el.sw.addEventListener("click", (e) => { e.stopPropagation(); this._toggle(); });
    this._el.sw.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); this._toggle(); }
    });
    /* tiles.php: the card is a link to the unit page; the switch and the slider
       stop the click first. */
    if (c.navigate) {
      this._el.tile.style.cursor = "pointer";
      this._el.tile.addEventListener("click", () => {
        history.pushState(null, "", c.navigate);
        window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
      });
    }
    bind_volume_slider(this, this._el.slider, this._el.knob, null);
    this._built = true;
    fa_icons().then((have) => { this._have = have; this._acts_sig = null; this._paint(); });
  }

  _toggle() {
    const state = this._hass.states[this._config.entity];
    if (is_unavailable(state)) return;
    const on = unit_on(state);
    const sw = this._el.sw;
    sw.classList.add("busy");
    sw.classList.remove("failed");
    this._hass.callService("media_player", on ? "turn_off" : "turn_on", { entity_id: this._config.entity })
      .catch((err) => { sw.classList.add("failed"); toast(this, err); })
      .finally(() => sw.classList.remove("busy"));
  }

  _start(btn) {
    const id = Number(btn.dataset.id);
    btn.classList.remove("failed");
    const call = btn.dataset.off
      ? this._hass.callService("media_player", "turn_off", { entity_id: this._config.entity })
      : this._hass.callService(DOMAIN, "start_activity", { entity_id: this._config.entity, activity: id });
    call.catch((err) => { btn.classList.add("failed"); toast(this, err); });
  }

  _paint() {
    if (!this._el) return;
    const c = this._config;
    const state = this._hass.states[c.entity];
    const el = this._el;
    const a = attrs(state);
    el.tile.classList.toggle("unavail", is_unavailable(state));
    el.name.textContent = c.name || a.friendly_name || c.entity;
    /* tiles.php: a zone's room after its name. */
    el.caption.textContent = a.unit_kind === "zone" && a.room ? a.room : "";
    const status = tile_status(state);
    el.status.textContent = status.text;
    el.dots.hidden = !status.starting;
    const icon = icon_name(tile_icon(state), this._have, KIND_ICON[a.unit_kind] || "articontrol:logo");
    if (el.icon.getAttribute("icon") !== icon) el.icon.setAttribute("icon", icon);

    const on = unit_on(state);
    if (!el.sw.classList.contains("busy")) {
      el.sw.classList.toggle("on", on);
      el.sw.setAttribute("aria-checked", on ? "true" : "false");
    }
    el.sw.setAttribute("aria-label", el.name.textContent);

    /* The wash: changed only when the picture changes, so a poll does not
       re-request the image (paint.js). */
    const wash_kind = tile_wash(state);
    const art = tile_art(state);
    let wash = el.tile.querySelector(".wash");
    if (wash_kind) {
      if (!wash) {
        wash = document.createElement("div");
        el.tile.insertBefore(wash, el.tile.firstChild);
      }
      wash.className = `wash ${wash_kind}`;
      if (art && wash.getAttribute("data-art") !== art) {
        wash.setAttribute("data-art", art);
        wash.style.backgroundImage = `url("${art.replace(/"/g, "%22")}")`;
      } else if (!art && wash.getAttribute("data-art")) {
        wash.removeAttribute("data-art");
        wash.style.backgroundImage = "";
      }
    } else if (wash) {
      wash.parentNode.removeChild(wash);
    }

    paint_volume_slider(el.slider, el.knob, volume_view(state));
    if (el.acts) this._paint_acts(state);
  }

  /* The room's activities, directly as buttons (Michel 2026-10-26: Home
     Assistant's own window hides Source behind an icon, "not very intuitive").
     Start-only; Off is the unit's Off activity. Rebuilt only when what they
     show changes, so a press is not lost to a repaint. */
  _paint_acts(state) {
    const rows = activity_rows(state);
    const off = off_row(state);
    const dead = is_unavailable(state);
    const sig = JSON.stringify([rows, off, dead, !!this._have]);
    if (this._acts_sig === sig) return;
    this._acts_sig = sig;
    const box = this._el.acts;
    box.hidden = !rows.length && !off;
    box.innerHTML = rows.map((r) => `
      <button class="act${r.running ? " running" : ""}" data-id="${r.id}"${dead ? " disabled" : ""}
              title="${esc(r.name)}" aria-pressed="${r.running ? "true" : "false"}">
        <ha-icon icon="${esc(icon_name(r.icon, this._have, "articontrol:logo"))}"></ha-icon>
        <span class="label"><span>${esc(r.name)}</span>${r.starting ? '<span class="dots inline"></span>' : ""}</span>
      </button>`).join("") + (off ? `
      <button class="act${off.running ? " running" : ""}" data-off="1"${dead ? " disabled" : ""}
              title="Off" aria-pressed="${off.running ? "true" : "false"}">
        <ha-icon icon="${esc(icon_name("fa-solid fa-power-off", this._have, "mdi:power"))}"></ha-icon>
        <span class="label"><span>Off</span>${off.starting ? '<span class="dots inline"></span>' : ""}</span>
      </button>` : "");
    box.querySelectorAll(".act").forEach((b) =>
      b.addEventListener("click", (e) => { e.stopPropagation(); this._start(b); }));
  }
}

/* ------------------------------------------------------------------ *
 * Tile layouts - the webapp's Settings > Layout (Michel 2026-10-26: "Also
 * add grid view"). The Hub integration's 0.32.5 implementation, copied; the
 * column counts and the tile rules are the CONTROL webapp's.
 * ------------------------------------------------------------------ */

/* PORTED FROM app/js/layout.js: the same four words and the same default.
 * ⛔ ITS OWN localStorage KEY, `articontrol_layout` (Michel 2026-10-26: the
 * Control home and the Hub's dashboard choose separately) - never the Hub's
 * `artiled_layout`, which is neither read nor written nor migrated here.
 * Per BROWSER, as in the webapp - a phone and a wall tablet
 * can want different layouts; the strategy option `layout:` fixes one for a
 * dashboard instead (the switch is then not drawn).
 * The webapp's Size (Small / Normal / Large) is not ported, as the Hub's is
 * not: Normal.
 * ⚠ localStorage can throw (private mode, blocked site data). The layout is a
 * nicety, so a throw is the default rather than a broken dashboard. */
const LAYOUTS = ["rows", "grid", "square", "list"];   /* the webapp's order */
const LAYOUT_DEFAULT = "grid";
const LAYOUT_KEY = "articontrol_layout";
const LAYOUT_EVENT = "artiled-control-layout";
const LAYOUT_OLD_NAMES = { compact: "square" };
const LAYOUT_LABEL = { rows: "Rows", grid: "Grid", square: "Square", list: "List" };

function layout_word(value) {
  if (typeof value === "string" && Object.prototype.hasOwnProperty.call(LAYOUT_OLD_NAMES, value)) {
    value = LAYOUT_OLD_NAMES[value];
  }
  return LAYOUTS.includes(value) ? value : LAYOUT_DEFAULT;
}

/* The choice made on this page when storage refused it. */
let layout_unsaved = null;

/* A valid fixed (strategy) choice wins; otherwise this browser's. */
function layout_read(fixed) {
  if (fixed && LAYOUTS.includes(fixed)) return fixed;
  try {
    return layout_word(window.localStorage.getItem(LAYOUT_KEY));
  } catch (e) {
    return layout_word(layout_unsaved);
  }
}

/* Stores the choice and tells every list on every view of this page. */
function layout_store(value) {
  const word = layout_word(value);
  layout_unsaved = word;
  try {
    window.localStorage.setItem(LAYOUT_KEY, word);
  } catch (e) {
    /* nothing to do: the page still shows the choice until it is left */
  }
  window.dispatchEvent(new CustomEvent(LAYOUT_EVENT, { detail: word }));
}

/* How many cards share a row - the Control's app.css on a phone, its
 * tablet.css on an iPad:
 *
 *   phone              Rows 1   Grid 2   Square 2   List 1
 *   tablet portrait    Rows 2   Grid 4   Square 4   List 2
 *   tablet landscape   Rows 2   Grid 6   Square 6   List 2
 *
 * (The Hub's tablet.css drops Grid to 5 under 1100 px; the Control's does not,
 * so neither does this.)
 * ⚠ Keyed off the LIST's width, not the viewport's as tablet.css is: a
 * dashboard sits beside Home Assistant's sidebar and inside its padding. The
 * webapp's tablet step (700) less the 2 x 24 gutter = 652; a landscape iPad
 * 1024 wide less 2 x 40 = 944, so landscape from 900. A wide list in portrait
 * keeps the portrait counts, as tablet.css's orientation queries do. */
function layout_columns(layout, width, landscape) {
  const word = layout_word(layout);
  const w = Number(width) || 0;
  let tier = "phone";
  if (w >= 652) tier = landscape && w >= 900 ? "landscape" : "portrait";
  let cols;
  if (tier === "phone") cols = word === "grid" || word === "square" ? 2 : 1;
  else if (word === "rows" || word === "list") cols = 2;
  else cols = tier === "portrait" ? 4 : 6;
  return { cols, tier };
}

function is_landscape() {
  try {
    return !!(window.matchMedia && window.matchMedia("(orientation: landscape)").matches);
  } catch (e) {
    return true;
  }
}

/* The LIST: .app-tiles from app.css and tablet.css. The cards are separate
 * custom elements, so the spacing between them lives here, on the hosts. */
const TILE_TAG = "artiled-control-unit-card";
const TILES_STYLE = `
.app-tiles { display: grid; grid-template-columns: repeat(var(--cols, 1), minmax(0, 1fr)); }
.app-tiles > ${TILE_TAG} { position: relative; margin-top: 15px; }
/* Grid on a phone: two cells of 48.75%, 2.5% apart */
.app-tiles[data-layout="grid"][data-tier="phone"] { column-gap: 2.5%; }
/* tablet.css: --tablet-gap 15 */
.app-tiles[data-tier="portrait"], .app-tiles[data-tier="landscape"] { column-gap: 15px; }
/* Square: each square W/n - 10.5; 10.5 between columns and rows, 5.25 at either edge */
.app-tiles[data-layout="square"][data-tier] { column-gap: 10.5px; padding: 0 5.25px; }
.app-tiles[data-layout="square"] > ${TILE_TAG} { margin-top: 10.5px; }
/* List: lines 4 apart, the 1px rule at .3 between them, hanging 4px above */
.app-tiles[data-layout="list"] > ${TILE_TAG} { margin: 4px 0; }
.app-tiles[data-layout="list"] > ${TILE_TAG} + ${TILE_TAG}::before {
  content: ''; position: absolute; top: -4px; left: 0; right: 0; height: 1px;
  background: var(--primary-text-color, #fff); opacity: .3;
}
/* two across: the divider runs between ROWS, so the first row's right-hand
   line has none above it */
.app-tiles[data-layout="list"]:not([data-tier="phone"]) > ${TILE_TAG}:nth-child(2)::before { display: none; }
`;

/* Builds one list of tiles into `parent`. */
function tiles_list(parent, configs, hass) {
  const box = document.createElement("div");
  box.className = "app-tiles";
  configs.forEach((cfg) => {
    const card = document.createElement(TILE_TAG);
    card.setConfig(cfg);
    if (hass) card.hass = hass;
    box.appendChild(card);
  });
  parent.appendChild(box);
  return box;
}

/* Puts a list into a layout at its current width. */
function tiles_layout(box, layout) {
  const { cols, tier } = layout_columns(layout, box.clientWidth, is_landscape());
  box.dataset.layout = layout;
  box.dataset.tier = tier;
  box.style.setProperty("--cols", String(cols));
  for (const card of box.children) {
    if (card.getAttribute("layout") !== layout) card.setAttribute("layout", layout);
  }
}

/* Keeps the lists in `root` in the layout: on a switch anywhere on the page,
 * on a resize of the card, on a rotation. */
function tiles_follow(host, root, fixed) {
  const redo = () => {
    const layout = layout_read(fixed);
    root.querySelectorAll(".app-tiles").forEach((box) => tiles_layout(box, layout));
    return layout;
  };
  const on_event = () => redo();
  window.addEventListener(LAYOUT_EVENT, on_event);
  let ro = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => redo());
    ro.observe(host);
  }
  let mq = null;
  try {
    mq = window.matchMedia ? window.matchMedia("(orientation: landscape)") : null;
    if (mq && mq.addEventListener) mq.addEventListener("change", on_event);
  } catch (e) {
    mq = null;
  }
  return {
    redo,
    stop() {
      window.removeEventListener(LAYOUT_EVENT, on_event);
      if (ro) ro.disconnect();
      if (mq && mq.removeEventListener) mq.removeEventListener("change", on_event);
    },
  };
}

/* The switch - the webapp's Settings > Layout choices (.tt-tile with the app's
 * own phone-preview artwork, the chosen one framed), drawn small on the
 * dashboard because Home Assistant has no Settings > Layout of ours to put it
 * in. The artwork is settings/layout/index.php's, stroked in currentColor so
 * it follows Home Assistant's theme. */
const LAYOUT_ART_FRAME =
  '<rect x="0.5" y="0.5" width="87.7" height="158.9" rx="8.6" stroke="currentColor" stroke-opacity="0.8"/>' +
  '<path d="M24 1.1H65.1V4.6C65.1 7.1 63.1 9.1 60.6 9.1H28.6C26 9.1 24 7.1 24 4.6V1.1Z" fill="currentColor" fill-opacity="0.85"/>' +
  '<path d="M8 37.4H81.1" stroke="currentColor" stroke-opacity="0.7" stroke-width="0.5"/>';
const art_rect = (x, y, w, h, rx) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="currentColor" fill-opacity="0.8"/>`;
const LAYOUT_ART = {
  rows: [43.4, 74.3, 105.1, 136].map((y) => art_rect(8, y, 73.1, 22.9, 4.6)).join(""),
  grid: [43.4, 74.3, 105.1].map((y) => art_rect(8, y, 34.3, 22.9, 4.6) + art_rect(46.9, y, 34.3, 22.9, 4.6)).join(""),
  square: [43.4, 82.3, 121.2].map((y) => art_rect(8, y, 34.3, 34.3, 4.6) + art_rect(46.9, y, 34.3, 34.3, 4.6)).join(""),
  list: [0, 1, 2, 3, 4, 5, 6].map((i) => {
    const y = 44 + i * 16;
    return art_rect(8, y, 8, 8, 2) + art_rect(22, y + 1.5, 36, 5, 2.5) + art_rect(69, y + 0.5, 12.1, 7, 3.5) +
      (i < 6 ? `<path d="M8 ${y + 12}H81.1" stroke="currentColor" stroke-opacity="0.7" stroke-width="0.5"/>` : "");
  }).join(""),
};

/* app.css .tt-tiles / .tt-tile at the size of a toolbar (face 44 px, radius
   scaled with it); .app-section for a Control's heading. */
const HOME_STYLE = `
:host { display: block; }
.layouts { display: flex; justify-content: flex-end; gap: 10px; margin: 4px 0 0; }
.tt-tile {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 0; border: 0; background: none; font: inherit; cursor: pointer;
  color: var(--primary-text-color, #fff); -webkit-tap-highlight-color: transparent;
}
.tt-tile__face {
  width: 44px; aspect-ratio: 1; box-sizing: border-box;
  display: flex; align-items: center; justify-content: center;
  border: 1px solid rgba(127, 127, 127, .45);
  border: 1px solid color-mix(in srgb, var(--primary-text-color, #fff) 30%, transparent);
  border-radius: 9px; opacity: .72;
}
.tt-tile:active .tt-tile__face { background: color-mix(in srgb, var(--primary-text-color, #fff) 10%, transparent); }
.tt-tile.is-running .tt-tile__face {
  opacity: 1;
  background: rgba(127, 127, 127, .15);
  background: color-mix(in srgb, var(--primary-text-color, #fff) 12%, transparent);
  border-color: rgba(127, 127, 127, .9);
  border-color: color-mix(in srgb, var(--primary-text-color, #fff) 80%, transparent);
}
.tt-tile__face svg { width: 46%; height: auto; }
.tt-tile__name { font-size: 12px; line-height: 1.2; }
.app-section { margin: 18px 0 0; font-size: 16px; font-weight: 400; color: var(--primary-text-color, #fff); }
.layouts + .app-section { margin-top: 0; }
${TILES_STYLE}
`;

/* Home - app/index.php: the rooms and zones, nothing else (Michel 2026-10-26:
 * "Showing all the activities right away is not a good idea. Only when you
 * click a room. So first page only Rooms/Zones"). A tile still says what the
 * room is doing - the running activity, its cover - as the webapp's tile; its
 * activities are on the room's own page (the player's rows).
 * ONE card, not a card per tile: Home Assistant's section grid cannot give a
 * list the webapp's column counts and gaps, and a per-browser switch could not
 * regenerate it (the Hub's lesson, 0.32.5). One list per Control, headed by the
 * Control's name only when there is more than one. */
class ArtiControlHomeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
  }

  setConfig(config) {
    if (!config || !Array.isArray(config.groups)) throw new Error("artiled-control-home-card needs groups");
    this._config = config;
    this._built = false;
    if (this._follow) this._follow.stop();
    this._follow = null;
    if (this.shadowRoot) this.shadowRoot.innerHTML = "";
  }

  getCardSize() { return 12; }

  getGridOptions() { return { columns: "full" }; }

  /* a valid strategy `layout:` fixes it; anything else leaves it to the switch */
  _fixed() {
    const l = this._config && this._config.layout;
    return LAYOUTS.includes(l) ? l : null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this.shadowRoot.querySelectorAll(TILE_TAG).forEach((c) => { c.hass = hass; });
  }

  connectedCallback() {
    if (this._built && !this._follow) this._start();
  }

  disconnectedCallback() {
    if (this._follow) this._follow.stop();
    this._follow = null;
  }

  _build() {
    const root = this.shadowRoot;
    const fixed = this._fixed();
    root.innerHTML = `<style>${HOME_STYLE}</style>` + (fixed ? "" :
      `<div class="layouts" role="radiogroup" aria-label="Layout">${LAYOUTS.map((l) => `
        <button class="tt-tile" type="button" role="radio" data-layout="${l}" title="${LAYOUT_LABEL[l]}">
          <span class="tt-tile__face"><svg viewBox="0 0 90 160" fill="none" aria-hidden="true">${LAYOUT_ART_FRAME}${LAYOUT_ART[l]}</svg></span>
          <span class="tt-tile__name">${LAYOUT_LABEL[l]}</span>
        </button>`).join("")}</div>`);
    this._config.groups.forEach((g) => {
      if (!g || !Array.isArray(g.tiles) || !g.tiles.length) return;
      if (g.heading) {
        const h = document.createElement("h2");
        h.className = "app-section";
        h.textContent = String(g.heading);
        root.appendChild(h);
      }
      tiles_list(root, g.tiles.map((t) => ({ activities: false, ...t })), null);
    });
    root.querySelectorAll(".tt-tile[data-layout]").forEach((b) =>
      b.addEventListener("click", () => layout_store(b.dataset.layout)));
    this._built = true;
    this._start();
  }

  _start() {
    this._follow = tiles_follow(this, this.shadowRoot, this._fixed());
    const paint = () => {
      const layout = this._follow.redo();
      this.shadowRoot.querySelectorAll(".tt-tile[data-layout]").forEach((b) => {
        const on = b.dataset.layout === layout;
        b.classList.toggle("is-running", on);
        b.setAttribute("aria-checked", on ? "true" : "false");
      });
    };
    const stop = this._follow.stop;
    window.addEventListener(LAYOUT_EVENT, paint);
    this._follow.stop = () => { stop(); window.removeEventListener(LAYOUT_EVENT, paint); };
    paint();
  }
}

/* ------------------------------------------------------------------ *
 * The player - the unit page's rows and the webapp remote.
 * ------------------------------------------------------------------ */

/* The order is the webapp remote's (app/remote/index.php): the keys first,
   then the volume row, then Now Playing and its progress bar at the bottom;
   the unit page's activity rows follow below the remote.
   Values from app.css: the page gradient (#111 -> #444, the webapp's dark page,
   so its white words read on any Home Assistant theme); .app-remote__art (cover,
   .35); .app-remote__now (title lg 700, meta sm at .75); .app-remote__progress /
   __track (4 high, .25) / __bar (.9) / __times (xs, .75, tabular); .app-row +
   .app-activity (padding 16/8, radius 8, icon 28 in 40, grey .5 / white when
   running, sub .6, the running one .12 with a .3 frame); .app-key (60 round,
   #444 at .6, 20 glyph, pressed purple); .app-remote__row--transport (340
   wide); .app-remote__volume-row (22 | 1fr | 22, gap 12) and the slider value
   pill (.app-remote__slider-value). */
const PLAYER_STYLE = `
:host { display: block; }
.remote {
  position: relative; overflow: hidden; border-radius: 16px; padding: 12px 12px 16px;
  background: linear-gradient(#111, #444); color: #fff;
  font-family: -apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", Roboto, sans-serif;
}
.art {
  position: absolute; inset: 0; z-index: 0; opacity: .35;
  background-position: center; background-size: cover; background-repeat: no-repeat;
}
.art[hidden] { display: none; }
.remote > :not(.art) { position: relative; z-index: 1; }
.now { width: 100%; max-width: 340px; margin: 6px auto 0; text-align: center; }
.now[hidden], .progress[hidden], .row[hidden], .vol-row[hidden] { display: none; }
.now-title { overflow: hidden; font-size: 18px; font-weight: 700; white-space: nowrap; text-overflow: ellipsis; }
.now-meta { overflow: hidden; margin-top: 2px; font-size: 14px; color: rgba(255,255,255,.75); white-space: nowrap; text-overflow: ellipsis; }
.progress { max-width: 340px; margin: 0 auto; padding: 6px 4px 2px; }
.ptrack { height: 4px; border-radius: 2px; background: rgba(255,255,255,.25); overflow: hidden; }
.bar { width: 0; height: 100%; background: rgba(255,255,255,.9); }
.times { display: flex; justify-content: space-between; margin-top: 4px; font-size: 12px; color: rgba(255,255,255,.75); font-variant-numeric: tabular-nums; }
.progress.seekable .ptrack { cursor: pointer; touch-action: none; background-clip: content-box; padding: 8px 0; margin: -8px 0; height: 4px; box-sizing: content-box; }
.modes { display: flex; gap: 14px; }
.modes[hidden], .mode[hidden], .mode-one[hidden] { display: none; }
.mode { position: relative; padding: 0 4px; border: 0; background: none; color: #fff; font: inherit; opacity: .35; cursor: pointer; touch-action: manipulation; -webkit-tap-highlight-color: transparent; }
.mode ha-icon { --mdc-icon-size: 14px; }
.mode.on { opacity: 1; }
.mode-one { position: absolute; top: -4px; right: -4px; font-size: 9px; font-weight: 700; }
.rows { margin-top: 8px; }
.arow {
  display: flex; align-items: center; padding: 16px 8px; margin: 4px 0; border-radius: 8px;
  cursor: pointer; color: #fff; -webkit-tap-highlight-color: transparent; touch-action: manipulation;
}
.arow:active { background: rgba(255,255,255,.1); }
.arow ha-icon { --mdc-icon-size: 28px; flex: 0 0 40px; width: 40px; margin-right: 16px; color: rgba(255,255,255,.5); display: flex; justify-content: center; }
.arow .text { flex: 3.5; min-width: 0; }
.arow .aname { font-size: 18px; font-weight: 700; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.arow .asub { font-size: 16px; color: rgba(255,255,255,.6); }
.arow.running { background: rgba(255,255,255,.12); box-shadow: inset 0 0 0 1px rgba(255,255,255,.3); }
.arow.running ha-icon, .arow.running .aname { color: #fff; }
.arow.failed { outline: 2px solid #b3261e; }
.arow.dead { opacity: .45; cursor: default; }
.offrow { margin-top: 14px; }
.row { display: flex; align-items: center; justify-content: space-around; width: 100%; max-width: 340px; margin: 22px auto 0; }
.key {
  display: flex; align-items: center; justify-content: center; width: 60px; height: 60px;
  padding: 0; border: 0; border-radius: 50%; background: rgba(68,68,68,.6); color: #fff;
  font: inherit; box-shadow: 0 2px 4px rgba(0,0,0,.3); cursor: pointer;
  touch-action: manipulation; -webkit-tap-highlight-color: transparent; user-select: none;
  transition: background .1s, transform .1s;
}
.key ha-icon { --mdc-icon-size: 22px; }
.key.pressed { background: #7a51a1; transform: scale(.94); }
.key.failed { background: #a33; }
.vol-row {
  display: grid; grid-template-columns: 22px minmax(0, 1fr) 22px; align-items: center;
  column-gap: 12px; width: 100%; max-width: 340px; margin: 15px auto 0; color: #fff;
}
.vol-row > .step { cursor: pointer; padding: 14px 11px; margin: -14px -11px; opacity: .8; display: flex; justify-content: center; -webkit-tap-highlight-color: transparent; touch-action: manipulation; }
.vol-row > .step ha-icon { --mdc-icon-size: 18px; }
.vol-row > .step:active { opacity: 1; transform: scale(1.2); }
.vol-row.unknown > .step { opacity: .55; }
.slider { --knob: 20px; display: flex; align-items: center; width: 100%; height: 36px; touch-action: none; cursor: pointer; color: #fff; -webkit-tap-highlight-color: transparent; }
.slider.unknown { opacity: .55; }
.slider.failed .track { outline: 1px solid rgba(192,57,43,.9); outline-offset: 3px; }
.track { position: relative; width: 100%; height: 3px; border-radius: 2px; background: currentColor; opacity: .55; }
.knob {
  position: absolute; top: 50%; left: calc(var(--p, 0) * (100% - var(--knob)));
  width: var(--knob); height: var(--knob); border-radius: 50%; background: currentColor;
  transform: translateY(-50%); box-shadow: 0 6px 10px rgba(0,0,0,.6);
  transition: transform 90ms ease-out, box-shadow 90ms ease-out;
}
.slider.grabbed .knob { transform: translateY(-50%) scale(1.2); box-shadow: 0 3px 6px rgba(0,0,0,.4); }
.value {
  position: absolute; bottom: calc(50% + var(--knob) / 2 + 6px); left: 50%;
  padding: 1px 8px; border-radius: 10px; background: rgba(0,0,0,.6); color: #fff;
  font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap;
  transform: translateX(-50%); opacity: 0; pointer-events: none; transition: opacity .4s;
}
.value.shown { opacity: 1; transition: none; }
.empty { padding: 16px 8px; color: rgba(255,255,255,.6); text-align: center; }
/* The whole cover above the keys: the webapp remote's .app-remote__info-art. */
.cover { display: block; width: 100%; max-width: 340px; max-height: 200px; margin: 4px auto 0; object-fit: contain; }
.cover[hidden], .vol-row .mute[hidden] { display: none; }
.vol-row.with-mute { grid-template-columns: 22px 22px minmax(0, 1fr) 22px; }
.vol-row .mute { display: flex; }
/* the webapp remote's .app-remote__shortcuts / .app-remote__shortcut */
/* the webapp remote's shortcut row, but as big as this card's transport keys
   (.key: 60 round, 22 glyph) - Michel 2026-10-26: "quite small" */
.shortcuts { display: flex; align-items: center; justify-content: center; gap: 22px; max-width: 340px; margin: 0 auto 6px; }
.shortcuts[hidden], .shortcut[hidden] { display: none; }
.shortcut {
  position: relative; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
  width: 60px; height: 60px; padding: 0; border: 0; border-radius: 50%; background: rgba(68,68,68,.6);
  color: #fff; font: inherit; box-shadow: 0 2px 4px rgba(0,0,0,.3);
  touch-action: manipulation; -webkit-tap-highlight-color: transparent; user-select: none; cursor: pointer;
  transition: background .1s, transform .1s;
}
.shortcut:active { background: #7a51a1; transform: scale(.94); }
.shortcut ha-icon { --mdc-icon-size: 22px; }
` + DOTS_STYLE;

const BROWSE_TILES_CSS = `
/* Browse Music / Browse Movies: a port of the webapp's Find Music / Find Movies
   (app.css .app-find__search, __tiles, __tile, __cover, __cover--poster,
   __tile-name, __tile-sub, __section, __note; --fs-sm 14, --fs-md 16, --fs-lg 18). */
.browse .search {
  width: 100%; box-sizing: border-box; -webkit-appearance: none; appearance: none; font-family: inherit;
  padding: 10px 12px; border: 1px solid rgba(255,255,255,.25); border-radius: 8px;
  background: rgba(255,255,255,.08); color: #fff; font-size: 16px;
}
.section { margin: 14px 0 6px; font-size: 14px; font-weight: 700; opacity: .7; text-transform: uppercase; letter-spacing: .06em; }
/* The webapp's Find grid is three columns on a phone (app.css .app-find__tiles:
   ~111px tiles at 400px); a dashboard is wider, so the TILE keeps that size and
   the columns grow (Michel 2026-10-26: "covers WAY too big"). */
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); align-items: start; gap: 10px; }
.tile { display: block; width: 100%; padding: 0; border: 0; background: none; color: #fff; text-align: left; font: inherit; cursor: pointer; }
.tile.failed .cover { outline: 2px solid #b3261e; }
.cover {
  position: relative; width: 100%; aspect-ratio: 1 / 1; border-radius: 8px; background-color: rgba(255,255,255,.08);
  background-size: cover; background-position: center; display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 700; opacity: .95;
}
.cover.poster { aspect-ratio: 2 / 3; }
.tile-name { display: -webkit-box; margin-top: 4px; font-size: 14px; line-height: 1.2; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.tile-sub { display: block; font-size: 14px; opacity: .6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.note { opacity: .7; font-size: 16px; text-align: center; margin: 16px 0; }
.favs[hidden] { display: none; }
.more { display: block; width: 100%; margin: 12px 0; padding: 10px; border: 0; border-radius: 8px; background: rgba(255,255,255,.08); color: #fff; font: inherit; font-size: 14px; opacity: .8; cursor: pointer; }
`;

class ArtiControlPlayerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._have = null;
    this._timer = null;
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("artiled-control-player-card needs an entity");
    this._config = config;
    this._built = false;
    if (this.shadowRoot) this.shadowRoot.innerHTML = "";
  }

  getCardSize() { return 8; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._paint();
  }

  connectedCallback() {
    /* The bar moves between polls while playing (app/js/remote.js). */
    if (!this._timer) this._timer = window.setInterval(() => this._paint_progress(), 1000);
  }

  disconnectedCallback() {
    if (this._timer) window.clearInterval(this._timer);
    this._timer = null;
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>${PLAYER_STYLE}</style>
      <div class="remote">
        <div class="art" hidden></div>
        <div class="shortcuts" hidden>
          <button type="button" class="shortcut" data-goto="browse" aria-label="Browse" hidden><ha-icon></ha-icon></button>
          <button type="button" class="shortcut" data-goto="search" aria-label="Search" hidden><ha-icon></ha-icon></button>
        </div>
        <img class="cover" alt="" hidden>
        <div class="now" hidden><div class="now-title"></div><div class="now-meta"></div></div>
        <div class="progress" hidden>
          <div class="ptrack"><div class="bar"></div></div>
          <div class="times"><span class="elapsed"></span><span class="modes" hidden><button type="button" class="mode" data-mode="shuffle" aria-label="Shuffle" aria-pressed="false"><ha-icon></ha-icon></button><button type="button" class="mode" data-mode="repeat" aria-label="Repeat" aria-pressed="false"><ha-icon></ha-icon><span class="mode-one" hidden>1</span></button></span><span class="total"></span></div>
        </div>
        <div class="row transport" hidden></div>
        <div class="vol-row" hidden>
          <span class="mute" hidden></span>
          <span class="step down" role="button" aria-label="Volume down"><ha-icon></ha-icon></span>
          <div class="slider" hidden><div class="track"><span class="knob"></span><span class="value" aria-hidden="true"></span></div></div>
          <span class="step up" role="button" aria-label="Volume up"><ha-icon></ha-icon></span>
        </div>
        <div class="rows"></div>
      </div>`;
    const root = this.shadowRoot;
    this._el = {
      art: root.querySelector(".art"),
      now: root.querySelector(".now"),
      title: root.querySelector(".now-title"),
      meta: root.querySelector(".now-meta"),
      progress: root.querySelector(".progress"),
      bar: root.querySelector(".bar"),
      elapsed: root.querySelector(".elapsed"),
      total: root.querySelector(".total"),
      ptrack: root.querySelector(".ptrack"),
      modes: root.querySelector(".modes"),
      shuffle: root.querySelector('.mode[data-mode="shuffle"]'),
      repeat: root.querySelector('.mode[data-mode="repeat"]'),
      rows: root.querySelector(".rows"),
      transport: root.querySelector(".transport"),
      mute: root.querySelector(".vol-row .mute"),
      cover: root.querySelector("img.cover"),
      volrow: root.querySelector(".vol-row"),
      down: root.querySelector(".step.down"),
      up: root.querySelector(".step.up"),
      slider: root.querySelector(".slider"),
      knob: root.querySelector(".slider .knob"),
      value: root.querySelector(".value"),
    };
    /* The speakers step the volume (Michel 2026-10-24) - and, as in the webapp
       (controls.js), do NOTHING while the level is not known: greyed, a step
       from an unknown level is not sent. */
    const step = (service, node) => {
      if (!volume_view(this._hass.states[this._config.entity]).known) return;
      this._call("media_player", service, {}, node);
    };
    this._el.down.addEventListener("click", () => step("volume_down", this._el.down));
    this._el.up.addEventListener("click", () => step("volume_up", this._el.up));
    bind_volume_slider(this, this._el.slider, this._el.knob, (level, moving) => this._show_value(level, moving));
    /* Shuffle and repeat (playback.js): a tap sets the next one - shuffle on
       <-> off, repeat off -> all -> one -> off - and the player's answer is
       what shows; nothing while the player has not said. */
    this._el.shuffle.addEventListener("click", () => {
      const m = play_modes(this._hass.states[this._config.entity]);
      if (m.shuffle === null) return;
      this._call("media_player", "shuffle_set", { shuffle: !m.shuffle }, this._el.shuffle);
    });
    this._el.repeat.addEventListener("click", () => {
      const m = play_modes(this._hass.states[this._config.entity]);
      if (m.repeat === null) return;
      this._call("media_player", "repeat_set", { repeat: REPEAT_NEXT[m.repeat] }, this._el.repeat);
    });
    this._bind_seek();
    this._bind_browse();
    this._el.cover.addEventListener("error", () => {
      this._el.cover.setAttribute("data-failed", this._el.cover.getAttribute("src") || "");
      this._el.cover.hidden = true;
    });
    this._built = true;
    fa_icons().then((have) => { this._have = have; this._sig = null; this._paint(); });
  }

  _call(domain, service, data, node) {
    if (node) { node.classList.add("pressed"); node.classList.remove("failed"); }
    return this._hass.callService(domain, service, { entity_id: this._config.entity, ...data })
      .catch((err) => { if (node) node.classList.add("failed"); toast(this, err); })
      .finally(() => { if (node) window.setTimeout(() => node.classList.remove("pressed"), 120); });
  }

  /* The device's own number over the knob while it moves, gone 2 s after the
     finger lifts (controls.js show_value / fade_value). Nothing without the
     device's maximum. */
  _show_value(level, moving) {
    const v = this._el.value;
    if (this._value_timer) { window.clearTimeout(this._value_timer); this._value_timer = null; }
    if (moving) {
      const view = volume_view(this._hass.states[this._config.entity]);
      const text = slider_value_text(level, view.max, view.scale);
      v.textContent = text;
      v.style.left = `calc(${level / 100} * (100% - ${KNOB}px) + ${KNOB / 2}px)`;
      v.classList.toggle("shown", text !== "");
    } else {
      this._value_timer = window.setTimeout(() => v.classList.remove("shown"), VALUE_MS);
    }
  }

  /* The shortcut row - the webapp remote's (app/includes/remote.php
     APP_REMOTE_SHORTCUTS) and the Touch's: Browse (folder-open) opens the
     Browse page of what runs (a film activity's: Browse Movies), Search
     (magnifying-glass) the same page with its search field ready (Michel
     2026-10-26). Info, Queue and Lyrics are left out: the integration has no
     page for them. The strategy passes the pages' paths; none, no button. */
  _bind_browse() {
    const el = this._el;
    el.shortcuts = this.shadowRoot.querySelector(".shortcuts");
    el.go_browse = this.shadowRoot.querySelector('.shortcut[data-goto="browse"]');
    el.go_search = this.shadowRoot.querySelector('.shortcut[data-goto="search"]');
    [el.go_browse, el.go_search].forEach((b) => b.addEventListener("click", () => {
      const to = this._browse_path();
      if (!to) return;
      window.history.pushState(null, "", b.dataset.goto === "search" ? `${to}?search=1` : to);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    }));
  }

  /* 0.3.23: what Browse Music asks first (its accounts and Recent), read
     while the room's page is looked at, so Browse opens on kept answers.
     Once per room per 5 minutes, 1.5 s after the page shows, only while it
     is still shown; a read like any other (find.py keeps it). */
  _prefetch() {
    const entity = this._config.entity;
    const now = Date.now();
    if (now - (BROWSE_PREFETCHED.get(entity) || 0) < BROWSE_PREFETCH_MS) return;
    BROWSE_PREFETCHED.set(entity, now);
    window.setTimeout(() => {
      if (!this.isConnected || !this._hass) return;
      const ask = (request) => this._hass.callWS({ type: "artiled_control/find", entity_id: entity, request });
      ask({ action: "accounts" }).then(() => ask({ action: "recent" })).catch(() => { /* Browse asks again itself */ });
    }, BROWSE_PREFETCH_DELAY_MS);
  }

  /* Which page: what the running activity plays, else what the room plays. */
  _browse_path() {
    const paths = this._config.browse || {};
    const a = attrs(this._hass.states[this._config.entity]);
    const kind = a.browse_kind === "movies" || a.browse_kind === "music" ? a.browse_kind : (a.plays_music ? "music" : "movies");
    return paths[kind] || paths.music || paths.movies || "";
  }

  _paint_browse(state) {
    const can = !!this._browse_path() && !is_unavailable(state) && (attrs(state).plays_music || attrs(state).plays_movies);
    if (can && attrs(state).plays_music) this._prefetch();
    this._el.go_browse.hidden = !can;
    this._el.go_search.hidden = !can;
    this._el.shortcuts.hidden = !can;
    [[this._el.go_browse, "fa-light fa-folder-open", "mdi:folder-open-outline"],
     [this._el.go_search, "fa-light fa-magnifying-glass", "mdi:magnify"]].forEach(([node, fa, mdi]) => {
      const want = icon_name(fa, this._have, mdi);
      const icon = node.querySelector("ha-icon");
      if (icon.getAttribute("icon") !== want) icon.setAttribute("icon", want);
    });
  }

  /* A tap on the bar, or a drag along it, seeks there (remote.js): the bar
     follows the finger, and on release that second is sent. Only while the
     player can seek; while the finger is down nothing repaints the bar. */
  _bind_seek() {
    const track = this._el.ptrack;
    const fraction = (x) => {
      const r = track.getBoundingClientRect();
      return r && r.width > 0 ? (x - r.left) / r.width : NaN;
    };
    const follow = (x) => {
      const pos = np_position(this._hass.states[this._config.entity], Date.now());
      const f = fraction(x);
      if (!pos || isNaN(f)) return;
      const s = seek_to(f, pos.duration);
      this._el.bar.style.width = (s / pos.duration * 100).toFixed(3) + "%";
      this._el.elapsed.textContent = clock(s);
    };
    track.addEventListener("pointerdown", (e) => {
      if (!play_modes(this._hass.states[this._config.entity]).can_seek) return;
      this._seeking = true;
      if (track.setPointerCapture && e.pointerId !== undefined) { try { track.setPointerCapture(e.pointerId); } catch (_) { /* gone */ } }
      follow(e.clientX);
    });
    track.addEventListener("pointermove", (e) => { if (this._seeking) follow(e.clientX); });
    track.addEventListener("pointerup", (e) => {
      if (!this._seeking) return;
      this._seeking = false;
      const pos = np_position(this._hass.states[this._config.entity], Date.now());
      const f = fraction(e.clientX);
      if (!pos || isNaN(f)) return;
      this._call("media_player", "media_seek", { seek_position: seek_to(f, pos.duration) }, null);
    });
    track.addEventListener("pointercancel", () => { this._seeking = false; this._paint_progress(); });
  }

  _paint_progress() {
    if (!this._el || !this._hass) return;
    const state = this._hass.states[this._config.entity];
    const pos = np_position(state, Date.now());
    this._el.progress.hidden = !pos;
    if (!pos) return;
    const modes = play_modes(state);
    this._el.progress.classList.toggle("seekable", modes.can_seek);
    this._el.modes.hidden = modes.shuffle === null && modes.repeat === null;
    this._el.shuffle.hidden = modes.shuffle === null;
    this._el.repeat.hidden = modes.repeat === null;
    this._el.shuffle.classList.toggle("on", modes.shuffle === true);
    this._el.shuffle.setAttribute("aria-pressed", String(modes.shuffle === true));
    this._el.repeat.classList.toggle("on", modes.repeat === "all" || modes.repeat === "one");
    this._el.repeat.setAttribute("aria-pressed", String(modes.repeat === "all" || modes.repeat === "one"));
    this._el.repeat.querySelector(".mode-one").hidden = modes.repeat !== "one";
    for (const [node, fa, mdi] of [[this._el.shuffle, "fa-solid fa-shuffle", "mdi:shuffle"], [this._el.repeat, "fa-solid fa-repeat", "mdi:repeat"]]) {
      const want = icon_name(fa, this._have, mdi);
      const icon = node.querySelector("ha-icon");
      if (icon.getAttribute("icon") !== want) icon.setAttribute("icon", want);
    }
    if (this._seeking) return;
    this._el.bar.style.width = (pos.position / pos.duration * 100).toFixed(3) + "%";
    this._el.elapsed.textContent = clock(pos.position);
    this._el.total.textContent = clock(pos.duration);
  }

  _paint() {
    if (!this._el) return;
    const state = this._hass.states[this._config.entity];
    const el = this._el;
    const art = tile_art(state);
    /* The WHOLE cover above the keys (Michel 2026-10-26), as the webapp
       remote's Now Playing picture (.app-remote__info-art: object-fit
       contain, never cropped), besides the see-through background. */
    el.cover.hidden = !art || el.cover.getAttribute("data-failed") === art;
    if (art && el.cover.getAttribute("src") !== art) {
      el.cover.removeAttribute("data-failed");
      el.cover.setAttribute("src", art);
    }
    el.art.hidden = !art;
    if (art && el.art.getAttribute("data-art") !== art) {
      el.art.setAttribute("data-art", art);
      el.art.style.backgroundImage = `url("${art.replace(/"/g, "%22")}")`;
    }
    const words = np_words(state);
    el.now.hidden = !words.title;
    el.title.textContent = words.title;
    el.meta.textContent = words.meta;
    this._paint_progress();
    this._paint_browse(state);

    const rows = activity_rows(state);
    const off = off_row(state);
    const dead = is_unavailable(state);
    const keys = transport_keys(state);
    const mute = mute_shown(state);
    const sig = JSON.stringify([rows, off, dead, keys, mute, !!this._have]);
    if (this._sig !== sig) {
      this._sig = sig;
      this._build_rows(rows, off, dead);
      this._build_keys(keys, mute);
    }
    const view = volume_view(state);
    el.volrow.hidden = !view.shown;
    const set_icon = (node, fa, mdi) => {
      const want = icon_name(fa, this._have, mdi);
      const icon = node.querySelector("ha-icon");
      if (icon.getAttribute("icon") !== want) icon.setAttribute("icon", want);
    };
    set_icon(el.down, "fa-solid fa-volume-low", "mdi:volume-low");
    set_icon(el.up, "fa-solid fa-volume-high", "mdi:volume-high");
    paint_volume_slider(el.slider, el.knob, view);
  }

  /* The activity rows: rows.php markup, paint.js marks. A starting row has the
     running look and says "Starting" with the moving dots. */
  _build_rows(rows, off, dead) {
    const row = (r, attrs_html, icon, sub) => `
      <div class="arow${r.running ? " running" : ""}${dead ? " dead" : ""}" role="button" tabindex="0" ${attrs_html}>
        <ha-icon icon="${esc(icon)}"></ha-icon>
        <div class="text">
          <div class="aname">${esc(r.name)}</div>
          <div class="asub">${sub}</div>
        </div>
      </div>`;
    const sub = (r) => (r.starting ? 'Starting<span class="dots inline"></span>' : (r.running ? "On" : "Off"));
    let html = rows.map((r) => row(r, `data-id="${r.id}"`, icon_name(r.icon, this._have, "articontrol:logo"), sub(r))).join("");
    if (!rows.length) html = '<div class="empty">No activities for this room</div>';
    if (off) {
      html += `<div class="offrow">${row({ name: "Off", ...off }, 'data-off="1"',
        icon_name("fa-solid fa-power-off", this._have, "mdi:power"), sub(off))}</div>`;
    }
    this._el.rows.innerHTML = html;
    this._el.rows.querySelectorAll(".arow").forEach((n) => {
      const go = () => {
        if (dead) return;
        n.classList.remove("failed");
        const call = n.dataset.off
          ? this._hass.callService("media_player", "turn_off", { entity_id: this._config.entity })
          : this._hass.callService(DOMAIN, "start_activity", { entity_id: this._config.entity, activity: Number(n.dataset.id) });
        call.catch((err) => { n.classList.add("failed"); toast(this, err); });
      };
      n.addEventListener("click", go);
      n.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  _build_keys(keys, mute) {
    const by_key = new Map(TRANSPORT.map((t) => [t[0], t]));
    this._el.transport.hidden = !keys.length;
    this._el.transport.innerHTML = keys.map((k) => {
      const [, fa, mdi] = by_key.get(k);
      return `<button class="key" data-k="${k}" aria-label="${k}"><ha-icon icon="${esc(icon_name("fa-" + fa, this._have, mdi))}"></ha-icon></button>`;
    }).join("");
    this._el.transport.querySelectorAll(".key").forEach((b) =>
      b.addEventListener("click", () => this._call("media_player", by_key.get(b.dataset.k)[3], {}, b)));
    /* Mute on the volume row, beside the speakers (Michel 2026-10-26: "Mute is
       on its own line. Waste of space") - never a line of its own. */
    this._el.mute.hidden = !mute;
    this._el.volrow.classList.toggle("with-mute", !!mute);
    this._el.mute.innerHTML = mute
      ? `<span class="step mutekey" role="button" data-k="mute" aria-label="Mute"><ha-icon icon="${esc(icon_name("fa-solid fa-volume-xmark", this._have, "mdi:volume-mute"))}"></ha-icon></span>`
      : "";
    const m = this._el.mute.querySelector(".mutekey");
    if (m) {
      /* Home Assistant has discrete mute / unmute, no toggle: unmute when the
         Control says muted, otherwise mute. */
      m.addEventListener("click", () => {
        const muted = attrs(this._hass.states[this._config.entity]).is_volume_muted === true;
        this._call("media_player", "volume_mute", { is_volume_muted: !muted }, m);
      });
    }
  }
}

/* ------------------------------------------------------------------ *
 * The Browse Music / Browse Movies page (Michel 2026-10-26) - a port of
 * the webapp's Find Music / Find Movies page.
 * ------------------------------------------------------------------ */

/* Find Music's numbers: a hundred tiles a page (films.js PAGE_TILES), the next
   asked 600px before the end (find-music-bind.js rootMargin), a search 400 ms
   after the last key (find-music-bind.js). */
/* 0.3.24 THE LAST ANSWERS, KEPT IN THE BROWSER (Michel 2026-10-26: "When I
   click one everything disappears ... and comes back but takes long. Also
   initial load"). Home Assistant rebuilds the dashboard's cards when its
   entities change - adding a voice favourite does - and a rebuilt card used
   to start from nothing. Every read a page draws is kept here per room and
   request: in memory for the page, and in sessionStorage so a reload of the
   dashboard draws at once too; the page then asks again and redraws only
   what changed. ⚠ sessionStorage can throw or be full: memory still works. */
const KEPT_MEM = new Map();
const KEPT_PREFIX = "articontrol_kept:";
const KEPT_MAX = 80;

function kept_get(key) {
  if (KEPT_MEM.has(key)) return KEPT_MEM.get(key);
  try {
    const raw = window.sessionStorage.getItem(KEPT_PREFIX + key);
    if (raw) {
      const value = JSON.parse(raw);
      KEPT_MEM.set(key, value);
      return value;
    }
  } catch (e) { /* no storage: nothing kept */ }
  return undefined;
}

function kept_put(key, value) {
  KEPT_MEM.delete(key);
  KEPT_MEM.set(key, value);
  while (KEPT_MEM.size > KEPT_MAX) {
    const old = KEPT_MEM.keys().next().value;
    KEPT_MEM.delete(old);
    try { window.sessionStorage.removeItem(KEPT_PREFIX + old); } catch (e) { /* none */ }
  }
  try { window.sessionStorage.setItem(KEPT_PREFIX + key, JSON.stringify(value)); } catch (e) { /* full: memory only */ }
}

const BROWSE_PAGE = 100;
const BROWSE_SEARCH_MS = 400;
/* a play while nothing runs: the music activity is started, then the play is
   sent again every 1.5 s for up to 30 s (an activity start takes 5-20 s) */
const BROWSE_START_MS = 1500;
const BROWSE_WRITES = new Set(["play_item", "play"]);
const BROWSE_STARTING_MIN_MS = 1200;
const BROWSE_PREFETCHED = new Map();
const BROWSE_PREFETCH_MS = 5 * 60 * 1000;
const BROWSE_PREFETCH_DELAY_MS = 1500;
const BROWSE_START_TRIES = 20;
/* the tab chosen per Browse page, kept while the dashboard is open */
const BROWSE_UI = { tab: {} };


/* The tabs of the webapp's Find Music per service (app/js/find-music.js:
   TABS, SPOTIFY_TABS, SONOS_TABS + SVC_TABS, heosTabsPresent, zidooTab), in
   its order and with its names, each with the request find-music.js askFor()
   sends for it. A Sonos / HEOS kind tab is one kind of the one `lists` answer. */
/* ---- the album / artist page's rules, PORTED from app/js/find-music.js --
   (albumName, albumKey, dedupe, isPerson, isAlbum, insideLabel, isListed,
   trackParts, tileSub, pictureless, spotifyIdentity), word for word where the
   webapp has them. */
const FOLD_VERSION = /remaster|deluxe|edition|expanded|anniversary|bonus|version|\bmono\b|stereo|reissue|special|collector|legacy|hi-?res|24[- ]?bit|khz|\bmfsl\b|explicit|\bmix\b|\bsacd\b|\bdsd\d*\b|\bmqa\b|\batmos\b|binaural|\b[57]\.1\b|\b(44\.1|48|88\.2|96|176\.4|192|352\.8)\s*(khz)?\s*[-\/]\s*(16|24|32)\b|\bseries\b|\bint[’']?l\b|\breg\.?\s*tracks\b|complete sessions/i;
const FOLD_SOUNDTRACK = /soundtrack|motion picture|music from|\bscore\b|\bost\b|bande originale|muzyka z filmu|サウンドトラック/i;
const FOLD_NEVER = /\blive\b|remix|acoustic|instrumental|karaoke|\bdemo\b/i;

function album_name(x) {
  return String(x || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[-‐‑–—]/g, " ").replace(/\s*&\s*/g, " and ")
    .replace(/[:,.!?'"‘’“”<>()\[\]]/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
}

function album_key(title) {
  let x = String(title || "").trim();
  let m;
  x = x.replace(/\s*[\(\[]([^\(\)\[\]]*)[\)\]](\s*[\)\]])$/, (all, inner, close) =>
    ((!FOLD_NEVER.test(inner) && (FOLD_VERSION.test(inner) || FOLD_SOUNDTRACK.test(inner))) ? close : all));
  x = x.replace(/\s+([-–—\/]\s+)?(the\s+)?original\s+(((motion\s+picture|television|film)\s+)?(soundtrack|score)|motion\s+picture)$/i, "")
    .replace(/\s+([-–—\/]\s+)?music\s+from\s+the\s+(original\s+)?motion\s+picture$/i, "").replace(/\s+[57]\.1$/, "");
  while ((m = /\s*[\(\[]([^\(\)\[\]]*)[\)\]]\s*$/.exec(x) || /\s+[-–—\/]\s+([^-–—\/]+)$/.exec(x))) {
    if (FOLD_NEVER.test(m[1]) || !(FOLD_VERSION.test(m[1]) || FOLD_SOUNDTRACK.test(m[1]) || /^aka\b/i.test(m[1]))) break;
    x = x.slice(0, m.index).replace(/\s+$/, "");
  }
  return album_name(x);
}

/* Keeps the FIRST of each record (Roon lists the plain pressing first); a
   row with no artist beside it - a person, a playlist - is left alone.
   `folded` maps the id shown to the ids hidden behind it. */
function dedupe(items, folded) {
  const seen = Object.create(null);
  return (items || []).filter((it) => {
    const artist = String((it && it.subtitle) || "").trim().toLowerCase();
    if (!artist) return true;
    const key = album_key(it.title) + "\u0000" + artist;
    if (seen[key]) {
      if (folded && seen[key].id && it.id) {
        const mine = (folded[seen[key].id] = folded[seen[key].id] || []);
        if (!mine.includes(it.id)) mine.push(it.id);
      }
      return false;
    }
    seen[key] = it;
    return true;
  });
}

function path_root(it) {
  const path = (it && it.path) || [];
  return path.length ? String(path[0].root || "") : "";
}

function is_person(it) {
  const path = (it && it.path) || [];
  const root = path_root(it);
  return path.length === 2 && (root === "artists" || root === "composers");
}

function spotify_identity(it) {
  const m = /^spotify:(album|playlist|track|artist):/.exec(String((it && (it.uri || it.key)) || ""));
  return m ? m[1] : "";
}

function is_album(it) {
  const path = (it && it.path) || [];
  const root = path_root(it);
  if (it && !path.length) {
    if (/^spotify:album:/.test(String(it.uri || "")) || /^spotify:album:/.test(String(it.key || ""))) return true;
    if ((it.source === "heos" || it.source === "zidoo") && String(it.kind || "") === "album") return true;
    const sonos = typeof it.source === "string" && it.source.indexOf("sonos_") === 0;
    if (sonos && (String(it.type || "") === "Album" || String(it.kind || "") === "album")) return true;
    if (/^(tidal|deezer|qobuz|apple):album:|^A:ALBUM\//.test(String(it.uri || ""))) return true;
  }
  return (root === "albums" && path.length === 2) ||
    ((root === "artists" || root === "genres" || root === "composers") && path.length === 3);
}

/* the heading says what is in there: an artist holds Albums, a composer
   Compositions (Roon's word), an album or a playlist Tracks */
function inside_label(it, inside) {
  const all = (k) => (inside || []).length > 0 && (inside || []).every((x) => x.kind === k);
  if (it && it.source === "zidoo") return String(it.kind || "") === "album" ? "Tracks" : "Albums";
  if (it && it.source === "heos") {
    const hk = String(it.kind || "");
    if (hk === "album" || hk === "playlist" || all("track")) return "Tracks";
    if (all("album")) return "Albums";
    if (all("playlist")) return "Playlists";
    if (all("station")) return "Stations";
    return String(it.title || "");
  }
  if (it && typeof it.source === "string" && it.source.indexOf("sonos_") === 0 && !(it.path || []).length) {
    const k = String(it.type || "");
    return (k === "Album" || k === "Playlist") ? "Tracks" : (k === "Artist" ? "Albums" : "");
  }
  const path = (it && it.path) || [];
  if (!path.length) {
    const sid = `${String((it && it.key) || "")} ${String((it && it.uri) || "")}`;
    if (/(^|\s)[a-z]+:(album|playlist):|A:ALBUM\//.test(sid)) return "Tracks";
    if (/(^|\s)[a-z]+:artist:|sonoslib:artist:/.test(sid)) return "Albums";
    return "";
  }
  const root = path_root(it);
  if (root === "search" && (inside || []).length && (inside || []).every((x) => album_key(x.title) === album_key(it.title))) return "Versions";
  if (path.length < 2) return "";
  if (path.length === 2) {
    if (root === "artists" || root === "genres") return "Albums";
    if (root === "composers") return "Compositions";
  }
  return "Tracks";
}

/* a work and a song are a LIST (the Queue's rows), not covers */
function is_listed(label) {
  return label === "Tracks" || label === "Compositions";
}

/* "1. So Far Away": the number once, out of the title */
function track_parts(title) {
  const m = /^\s*(\d{1,3})\.\s*(.+)$/.exec(String(title || ""));
  return m ? { number: m[1], name: m[2] } : { number: "", name: String(title || "") };
}

/* a person's tile loses Roon's bare count ("9 Albums") */
function tile_sub(it) {
  const sub = String((it && it.subtitle) || "");
  return (is_person(it) && /^\s*\d+\s+\S+\s*$/.test(sub)) ? "" : sub;
}

/* a person with no picture is not drawn ("No artist image, don't show it") */
function pictureless(it) {
  return !!it && (is_person(it) || it.type === "Artist" || it.kind === "artist") && !it.image && !(it.covers && it.covers.length);
}

/* what is worth four covers from inside: a Roon playlist; and (0.3.21) a
   composition, whose record's cover the Control's `covers` read names */
function wants_covers(it) {
  const path = (it && it.path) || [];
  const root = path_root(it);
  return (root === "playlists" && path.length === 2) || (root === "composers" && path.length === 3);
}

function mosaic_html(covers) {
  return '<span class="app-find__mosaic">' + (covers || []).slice(0, 4).map((u) =>
    `<span style="background-image: url(&quot;${esc(u)}&quot;)"></span>`).join("") + "</span>";
}

/* The album page's styling - app.css .app-find__open / __open-cover /
   __open-text / __open-title / __open-sub / __text / __plays / __play /
   __back / __more / __note / __section / __mosaic and the Queue's rows
   (.app-queue__list / __row / __open / __cover / __cover--number / __text /
   __name / __sub / __time / __jump), rule for rule, on the webapp's own
   tokens (--fg-rgb 255,255,255, --text #fff, --text-dim .3, --purple). */
/* the webapp's fa-play, drawn inline (no icon set needed to see it) */
const PLAY_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>';

const DETAIL_CSS = `
.browse.is-open .search, .browse.is-open .ftabs, .browse.is-open .results { display: none; }
.page { --fg-rgb: 255, 255, 255; --text: #fff; --text-dim: rgba(255,255,255,.3); --purple: #7a51a1; }
.app-find__open { display: flex; align-items: flex-start; gap: 12px; margin-top: 6px; }
.app-find__open-cover {
  position: relative; flex: 0 0 auto; width: 38vw; max-width: 180px; aspect-ratio: 1 / 1; border-radius: 8px;
  background-color: rgba(var(--fg-rgb), .08); background-size: cover; background-position: center;
  display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 700;
}
.app-find__open-text { flex: 1 1 auto; min-width: 0; }
.app-find__open-title { font-size: 18px; font-weight: 700; margin-top: 6px; }
.app-find__open-sub { font-size: 16px; opacity: .7; }
.app-find__text {
  margin: 10px 0 0; font-size: 14px; line-height: 1.45; opacity: .8; overflow: hidden; cursor: pointer;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
}
.app-find__text.is-open { display: block; -webkit-line-clamp: none; }
.app-find__plays { display: flex; gap: 4px; margin-top: 12px; }
.app-find__play, .app-find__more, .app-find__back {
  padding: 10px 14px; border: 0; border-radius: 8px; background: rgba(var(--fg-rgb), .12); color: #fff;
  font-family: inherit; font-size: 16px; cursor: pointer;
}
.app-find__play { flex: 1 1 0; min-width: 0; padding: 9px 4px; font-size: 12px; line-height: 1.2; text-align: center; }
.app-find__play.busy, .app-queue__jump.busy { opacity: .6; }
.app-find__more { display: block; width: 100%; margin-top: 12px; }
.app-find__back { align-self: flex-start; padding: 6px 10px; font-size: 12px; margin: 6px 0 4px; }
.app-find__note { opacity: .7; font-size: 16px; text-align: center; margin: 16px 0; }
.app-find__section { margin: 14px 0 2px; font-size: 14px; font-weight: 700; opacity: .7; text-transform: uppercase; letter-spacing: .06em; }
.app-find__mosaic {
  position: absolute; inset: 0; display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr;
  border-radius: inherit; overflow: hidden;
}
.app-find__mosaic > span { background-size: cover; background-position: center; background-color: rgba(var(--fg-rgb), .06); }
.app-find__mosaic > span:only-child { grid-column: 1 / -1; grid-row: 1 / -1; }
.app-queue__list { display: flex; flex-direction: column; gap: 2px; }
.app-queue__row { display: flex; align-items: center; gap: 10px; padding: 6px 4px; border-radius: 8px; }
.app-queue__open {
  flex: 1 1 auto; display: flex; align-items: center; gap: 10px; min-width: 0; padding: 0; border: 0;
  background: none; color: var(--text); text-align: left; font-family: inherit; cursor: pointer;
}
.app-queue__cover {
  flex: 0 0 auto; width: 46px; height: 46px; border-radius: 6px; background-color: rgba(var(--fg-rgb), .08);
  background-size: cover; background-position: center; display: flex; align-items: center; justify-content: center;
  font-size: 16px; font-weight: 700;
}
.app-queue__cover--number { background-color: transparent; color: var(--text-dim); font-size: 14px; font-weight: 400; }
.app-queue__text { flex: 1 1 auto; min-width: 0; }
.app-queue__name { display: block; font-size: 14px; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-queue__no { display: inline-block; min-width: 1.6em; color: rgba(var(--fg-rgb), .5); font-variant-numeric: tabular-nums; }
.app-queue__sub { display: block; font-size: 14px; opacity: .6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-queue__time { flex: 0 0 auto; font-size: 14px; opacity: .6; font-variant-numeric: tabular-nums; }
.app-queue__jump {
  flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; padding: 0; width: 38px; height: 38px;
  border: 0; border-radius: 50%; background: rgba(var(--fg-rgb), .12); color: #fff; cursor: pointer;
}
.app-queue__jump svg { width: 14px; height: 14px; }
/* tablet.css: an opened album is a page to read - the readable column, centred, in portrait */
@media (orientation: portrait) and (min-width: 700px) { .browse.is-open .view { max-width: 720px; margin-left: auto; margin-right: auto; } }
/* 0.3.21: one tap plays - the Queue's round play button on the cover's corner */
.tile-play {
  position: absolute; right: 6px; bottom: 6px; display: inline-flex; align-items: center; justify-content: center;
  width: 38px; height: 38px; border-radius: 50%; background: rgba(0,0,0,.55); color: #fff; cursor: pointer;
  box-shadow: 0 1px 4px rgba(0,0,0,.4);
}
.tile-play svg { width: 18px; height: 18px; }
.tile-play:hover, .tile-play:focus-visible { background: #7a51a1; outline: none; }
.cover > .app-find__mosaic ~ .tile-play, .cover .tile-play { z-index: 1; }
`;

const BROWSE_STYLE = `
:host { display: block; }
.page {
  padding: 12px 12px 16px; border-radius: 16px; background: linear-gradient(#111, #444); color: #fff;
  font-family: -apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", Roboto, sans-serif;
}
.browse { margin: 0 auto; }
/* the webapp's Find tab strip (.app-tabs.app-tabs--scroll.app-find__tabs / .app-tab) */
.ftabs { display: flex; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; margin: 10px 0 4px; border-bottom: 2px solid rgba(255,255,255,.5); }
.ftabs[hidden], .view[hidden] { display: none; }
.ftab { position: relative; flex: 0 0 auto; padding: 14px 16px; min-height: 48px; font: inherit; font-size: 14px; color: rgba(255,255,255,.5); background: none; border: 0; cursor: pointer; white-space: nowrap; }
.ftab.on { color: #fff; }
.ftab.on::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: #fff; }
.fhead { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
.ftitle { flex: 1 1 auto; font-size: 18px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fback, .fplay { padding: 6px 10px; border: 0; border-radius: 8px; background: rgba(255,255,255,.12); color: #fff; font: inherit; font-size: 12px; cursor: pointer; }
.fplay { background: #7a51a1; }
.tile.dead { cursor: default; opacity: .6; }
/* starting: the active look (the lit activity's light frame), never blue */
.tile.is-starting .cover { box-shadow: inset 0 0 0 2px rgba(255,255,255,.85); }
.tile-starting { color: #fff; opacity: .9; }
` + BROWSE_TILES_CSS + DETAIL_CSS + DOTS_STYLE;

/* The /unit_find answers as the Control's door gives them (app/includes/
   music_answer.php, music_heos.php) - MEASURED live 2026-10-26: the flags are
   one level down, answer.accounts = {tidal, apple, sonoslib, sonos: {...},
   roon: {tidal, qobuz}, heos: {...}} beside `spotify` (this Control's own
   Spotify) and `kept`; heos_account answers {account: {sources, favorites,
   playlists, recent}}. A flat answer (no `accounts`) is read as the flags
   themselves. */
function find_accounts(answer) {
  const a = answer && typeof answer === "object" ? answer : {};
  const nested = a.accounts && typeof a.accounts === "object" && !Array.isArray(a.accounts);
  const accounts = nested ? a.accounts : a;
  return { accounts, spotify: a.spotify === true, known: Object.keys(accounts).length > 0 };
}

function heos_account(answer) {
  const a = answer && typeof answer === "object" ? answer : {};
  return a.account && typeof a.account === "object" && !Array.isArray(a.account) ? a.account : a;
}

const FIND_TABS = {
  roon: [
    { id: "recent", label: "Recent", ask: () => ({ action: "recent" }) },
    { id: "favorites", label: "Favorites", ask: (o) => ({ action: "albums", offset: o, sort: "", dir: "", category: "", favorites: "1" }), paged: true },
    { id: "playlists", label: "Playlists", ask: (o) => ({ action: "browse", path: JSON.stringify([{ root: "playlists" }]), offset: o }), paged: true },
    { id: "albums", label: "Albums", ask: (o) => ({ action: "albums", offset: o, sort: "", dir: "", category: "", favorites: "" }), paged: true },
    { id: "artists", label: "Artists", ask: (o) => ({ action: "browse", path: JSON.stringify([{ root: "artists" }]), offset: o }), paged: true, people: true },
    { id: "composers", label: "Composers", ask: (o) => ({ action: "browse", path: JSON.stringify([{ root: "composers" }]), offset: o }), paged: true, people: true },
  ],
  spotify: [
    { id: "spotify_recent", label: "Recent", ask: () => ({ action: "recent" }) },
    { id: "spotify_albums", label: "Albums", ask: () => ({ action: "library", what: "albums", sort: "", dir: "" }) },
    { id: "spotify_playlists", label: "Playlists", ask: () => ({ action: "lists" }), list: "playlists" },
    { id: "spotify_liked", label: "Songs", ask: (o) => ({ action: "library", what: "liked", offset: o }), paged: true },
  ],
  zidoo: [
    { id: "zidoo_recent", label: "Recent", ask: () => ({ action: "library", what: "zrec" }) },
    { id: "zidoo_fav", label: "Favorites", ask: () => ({ action: "lists" }), list: "favorites" },
    { id: "zidoo_albums", label: "Albums", ask: () => ({ action: "library", what: "zalbums" }) },
    { id: "zidoo_artists", label: "Artists", ask: () => ({ action: "library", what: "zartists" }), people: true },
  ],
};
/* Sonos (and HEOS): the kinds of favourite as tabs, only those it holds (sonosTabsPresent) */
const FIND_KINDS = [["Playlist", "Playlists"], ["Album", "Albums"], ["Artist", "Artists"], ["Track", "Songs"], ["Radio", "Radio"], ["Other", "Other"]];
/* the web GUI's service tabs (SVC_TABS), named as it names them */
const FIND_SVC = {
  sonos: [["spotify", "Spotify"], ["tidal", "TIDAL"], ["apple", "Apple"], ["deezer", "Deezer"], ["tunein", "TuneIn"], ["sonoslib", "Local"]],
  roon: [["tidal", "TIDAL"], ["qobuz", "Qobuz"]],
};
/* Find Movies (app/js/find-movies.js TABS) */
const FIND_MOVIE_TABS = [["recent", "Recent"], ["loved", "Favorites"], ["movies", "Movies"], ["directors", "Directors"], ["actors", "Actors"], ["genres", "Genres"]];
const MUSIC_CLASSES = ["album", "playlist", "channel", "track", "artist"];

class ArtiControlBrowseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("artiled-control-browse-card needs an entity");
    this._config = { kind: "music", ...config };
    this._built = false;
  }

  getCardSize() { return 12; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
      this._start();
    }
  }

  get _movies() { return this._config.kind === "movies"; }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>${BROWSE_STYLE}</style>
      <div class="page"><div class="browse">
        <input type="search" class="search" autocomplete="off" placeholder="Search">
        <div class="ftabs" role="tablist"></div>
        <div class="results"></div>
        <div class="view"></div>
      </div></div>`;
    const root = this.shadowRoot;
    this._el = {
      browse: root.querySelector(".browse"),
      search: root.querySelector(".search"),
      tabs: root.querySelector(".ftabs"),
      results: root.querySelector(".results"),
      view: root.querySelector(".view"),
    };
    let timer = null;
    this._el.search.addEventListener("change", () => { if (timer) window.clearTimeout(timer); this._search(); });
    this._el.search.addEventListener("input", () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => this._search(), BROWSE_SEARCH_MS);
    });
    this._built = true;
    /* opened from the Search shortcut: the field ready to type in */
    if (/[?&]search=1\b/.test(String((window.location || {}).search || ""))) {
      window.setTimeout(() => this._el.search.focus(), 0);
    }
  }

  disconnectedCallback() {
    if (this._covers) this._covers.disconnect();
  }

  /* One Find request through Home Assistant (never the Control from here). */
  _find(request, fresh) {
    return this._hass.callWS({ type: "artiled_control/find", entity_id: this._config.entity, request,
                               ...(this._movies ? { movies: true } : {}), ...(fresh ? { fresh: true } : {}) }).then((r) => {
      /* whether the room's music runs (a play goes to the door only then) and
         the activity Browse is for (started first when it does not) */
      if (r && typeof r.idle === "boolean") this._idle = r.idle;
      if (r && Number(r.activity) > 0) this._activity = Number(r.activity);
      if (r && !BROWSE_WRITES.has(request.action)) kept_put(this._mem_key(request), r);
      return r;
    });
  }

  _mem_key(request) {
    return `find|${this._config.entity}|${this._movies ? "m" : ""}|${JSON.stringify(request)}`;
  }

  /* the last answer this browser saw for a request, or undefined */
  _mem(request) {
    return BROWSE_WRITES.has(request.action) ? undefined : kept_get(this._mem_key(request));
  }

  /* 0.3.23 KEPT, THEN FRESH (the webapp's svcAccounts pattern): the answer
     the integration kept is drawn at once; when it was older than a minute
     ("kept" = its age) the page asks again with fresh=true and draws that
     answer too - only when it differs, so nothing jumps for nothing. */
  _kept_then_fresh(request, handle) {
    return this._find(request).then((r) => {
      handle(r, false);
      if (r && Number(r.kept) > 0) {
        const was = JSON.stringify(r.answer);
        this._find(request, true).then((f) => { if (f && JSON.stringify(f.answer) !== was) handle(f, true); }, () => {});
      }
      return r;
    });
  }

  /* Which tabs: the service of the activity Browse is for, and what it holds
     (the webapp's accounts / heos_account / lists, asked once). */
  async _start() {
    this._el.view.innerHTML = '<p class="note is-searching">Loading<span class="dots inline"></span></p>';
    try {
      if (this._movies) {
        this._tabs = FIND_MOVIE_TABS.map(([id, label]) => ({ id, label }));
        this._service = "movies";
        const kept = this._mem({ action: "titles" });
        if (kept) {
          this._films = ((kept.answer || {}).titles || []);
          this._first_tab();
        }
        const r = await this._find({ action: "titles" });
        const was = JSON.stringify(this._films || null);
        this._films = ((r.answer || {}).titles || []);
        if (kept) {
          if (JSON.stringify(this._films) !== was && !this._detail) this._open_tab();
          return;
        }
      } else {
        /* the tabs from what this browser kept, drawn at once; the answers
           asked again below and the tabs redrawn only when they changed */
        const kept = this._mem({ action: "accounts" });
        if (kept && (FIND_TABS[kept.service] || this._mem({ action: "lists" }))) {
          this._service = kept.service;
          this._acc = find_accounts(kept.answer);
          this._tabs = await this._music_tabs(kept.service, true);
          this._first_tab();
        }
        const acc = await this._find({ action: "accounts" });
        this._service = acc.service;
        this._acc = find_accounts(acc.answer);
        const before = kept ? JSON.stringify((this._tabs || []).map((t) => t.id)) : null;
        this._tabs = await this._music_tabs(acc.service);
        if (before !== null) {
          if (JSON.stringify(this._tabs.map((t) => t.id)) !== before) {
            if (!this._tabs.some((t) => t.id === this._tab)) { this._first_tab(); } else this._paint_tabs();
          }
          this._fresh_accounts();
          return;
        }
        this._fresh_accounts();
      }
    } catch (e) {
      if (!this._tabs || !this._tabs.length) this._el.view.innerHTML = `<p class="note">${esc(error_text(e))}</p>`;
      return;
    }
    this._first_tab();
  }

  _first_tab() {
    this._tab = (this._tabs.find((t) => t.id === BROWSE_UI.tab[this._config.entity + this._config.kind]) || this._tabs[0] || {}).id;
    this._paint_tabs();
    this._open_tab();
  }

  /* then this room's player asked NOW (find-music.js svcAccounts: the kept
     answer first, fresh=1 after the first paint); a tab whose account is gone
     falls back to the first tab */
  _fresh_accounts() {
    this._find({ action: "accounts", fresh: "1" }).then((f) => {
      const fresh = find_accounts(f.answer);
      if (!fresh.known) return;
      this._acc = fresh;
      const tabs = this._tabs_now(this._service);
      if (JSON.stringify(tabs.map((t) => t.id)) === JSON.stringify(this._tabs.map((t) => t.id))) return;
      this._tabs = tabs;
      if (!tabs.some((t) => t.id === this._tab)) { this._tab = (tabs[0] || {}).id; this._paint_tabs(); this._open_tab(); return; }
      this._paint_tabs();
    }, () => { /* what was kept stands */ });
  }

  /* What the tabs are made from is asked once (the lists, HEOS's account);
     the tabs themselves are worked out again whenever the accounts change. */
  async _music_tabs(service, from_mem) {
    const get = (req) => (from_mem ? Promise.resolve(this._mem(req) || { answer: {} }) : this._find(req));
    if (!FIND_TABS[service]) {
      const lists = await get({ action: "lists" });
      this._lists = lists.answer || {};
      if (service === "heos") {
        try { this._hacc = heos_account(((await get({ action: "heos_account" })).answer)); } catch (e) { this._hacc = {}; }
      }
    }
    return this._tabs_now(service);
  }

  _tabs_now(service) {
    const acc = (this._acc || {}).accounts || {};
    if (FIND_TABS[service]) {
      const svc = (FIND_SVC[service] || []).filter(([what]) => ((acc.roon || {})[what]) === true)
        .map(([what, label]) => ({ id: `svc_${what}`, label, ask: () => ({ action: "library", what }), svc: true }));
      return FIND_TABS[service].concat(svc);
    }
    /* Sonos and HEOS: the kinds the favourites hold, then the service tabs */
    const favs = (this._lists || {}).favorites || [];
    const have = new Set(favs.map((f) => String(f.tab_base || f.type || "Other")));
    const kinds = FIND_KINDS.filter(([k]) => have.has(k)).map(([k, label]) => ({ id: `kind_${k}`, label, kind: k }));
    if (service === "heos") {
      const hacc = this._hacc || {};
      const out = [{ id: "heos_recent", label: "Recent", ask: () => ({ action: "library", what: "hrec" }) }].concat(kinds.filter((t) => t.kind !== "Track"));
      if (hacc.favorites === true) out.push({ id: "heos_hfav", label: "HEOS presets", ask: () => ({ action: "library", what: "hfav" }) });
      if (hacc.playlists === true) out.push({ id: "heos_hpl", label: kinds.length ? "HEOS Playlists" : "Playlists", ask: () => ({ action: "library", what: "hpl" }) });
      (Array.isArray(hacc.sources) ? hacc.sources : []).forEach((src) => {
        const sid = parseInt(src && src.sid, 10);
        if (sid > 0 && sid < 1000000) out.push({ id: `heos_hs${sid}`, label: String(src.name || "HEOS"), ask: () => ({ action: "library", what: `hs${sid}` }) });
      });
      return out;
    }
    /* find-music.js svcTabsPresent: Spotify also needs this Control's own
       Spotify set up (the answer's `spotify`) */
    const sonos = acc.sonos || {};
    const providers = favs.map((f) => String(f.provider || ""));
    const on = { spotify: sonos.spotify === true && (this._acc || {}).spotify === true,
                 tidal: acc.tidal === true && sonos.tidal === true, apple: acc.apple === true && sonos.apple === true,
                 deezer: providers.includes("Deezer"), tunein: providers.includes("TuneIn"), sonoslib: acc.sonoslib === true };
    return kinds.concat((FIND_SVC.sonos || []).filter(([what]) => on[what])
      .map(([what, label]) => ({ id: `svc_${what}`, label, ask: () => ({ action: "library", what }), svc: true })));
  }

  _paint_tabs() {
    this._el.tabs.innerHTML = (this._tabs || []).map((t) =>
      `<button type="button" class="ftab${t.id === this._tab ? " on" : ""}" data-t="${esc(t.id)}">${esc(t.label)}</button>`).join("");
    this._el.tabs.querySelectorAll(".ftab").forEach((b) => b.addEventListener("click", () => {
      this._tab = b.dataset.t;
      BROWSE_UI.tab[this._config.entity + this._config.kind] = this._tab;
      this._paint_tabs();
      this._open_tab();
    }));
  }

  /* A tab, from its top: the stack of opened pages emptied. */
  _open_tab() {
    this._stack = [];
    this._detail = null;
    this._el.browse.classList.remove("is-open");
    const t = (this._tabs || []).find((x) => x.id === this._tab);
    if (!t) { this._el.view.innerHTML = '<p class="note">Nothing here</p>'; return; }
    if (this._movies) { this._movie_tab(t.id); return; }
    if (t.kind || t.list) {
      const rows = t.kind
        ? (this._lists.favorites || []).filter((f) => String(f.tab_base || f.type || "Other") === t.kind)
        : ((this._lists_for(t)) || []);
      if (t.list && !this._lists_cache) {
        this._find(t.ask(0)).then((r) => { this._lists_cache = r.answer || {}; this._open_tab(); },
          (e) => { this._el.view.innerHTML = `<p class="note">${esc(error_text(e))}</p>`; });
        return;
      }
      this._page({ title: "", sections: [{ title: "", items: rows }] });
      return;
    }
    this._load({ title: "", ask: t.ask, paged: !!t.paged, svc: !!t.svc });
  }

  _lists_for(t) { return this._lists_cache ? this._lists_cache[t.list] : null; }

  /* One page of a tab or of something opened: asked, then drawn; "Loading
     more" asks the next part from where the Control said it stops. */
  async _load(page) {
    const gen = (this._gen = (this._gen || 0) + 1);
    const mem = this._mem(page.ask(0));
    let drawn = null;
    const draw = (r) => {
      const sig = JSON.stringify((r && r.answer) || {});
      if (gen !== this._gen || this._detail || sig === drawn) return;
      drawn = sig;
      this._page({ ...page, answer: (r && r.answer) || {} });
    };
    if (mem) draw(mem);
    else this._el.view.innerHTML = '<p class="note is-searching">Loading<span class="dots inline"></span></p>';
    try {
      await this._kept_then_fresh(page.ask(0), draw);
    } catch (e) {
      if (gen === this._gen) this._el.view.innerHTML = `<p class="note">${esc(error_text(e))}</p>`;
    }
  }

  /* An answer as the webapp draws it: sections (Recent, a search, a service
     tab), a list (albums, a browse level, a library), or the lists' rows. */
  _sections(ans) {
    if (Array.isArray(ans.sections)) return ans.sections.map((s) => ({ title: s.title || "", items: s.items || [], path: s.path || null }));
    if (Array.isArray(ans.items)) return [{ title: "", items: ans.items, count: ans.count, next: ans.next }];
    if (Array.isArray(ans.tracks)) return [{ title: "", items: ans.tracks }];
    return [];
  }

  _page(page) {
    const view = this._el.view;
    const secs = page.sections || this._sections(page.answer || {});
    const back = this._stack && this._stack.length
      ? `<button type="button" class="fback">‹ ${esc(this._stack[this._stack.length - 1].title || "Back")}</button>` : "";
    const head = page.title ? `<div class="fhead">${back}<div class="ftitle">${esc(page.title)}</div>${page.play
      ? '<button type="button" class="fplay">Play</button>' : ""}</div>` : back;
    view.innerHTML = head + (secs.length ? "" : '<p class="note">Nothing here</p>');
    const seen = new Set();
    secs.forEach((sec) => {
      const box = document.createElement("div");
      view.appendChild(box);
      const rows = (sec.items || []).filter((it) => {
        const k = String(it.content_id || "").replace(/^play:\d+:/, "") || `${it.title}|${it.subtitle || ""}|${JSON.stringify(it.path || it.uri || "")}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
      if (sec.path && !(sec.items || []).length) {
        /* a service tab's section names its Roon level: asked here, its first 12 (the webapp's fillSections) */
        this._find({ action: "browse", path: JSON.stringify(sec.path), offset: 0, count: 12 }).then((r) => {
          this._grid(box, (r.answer || {}).items || [], sec.title, { ask: (o) => ({ action: "browse", path: JSON.stringify(sec.path), offset: o }),
                                                                        count: (r.answer || {}).count, next: (r.answer || {}).next });
        }, () => { box.innerHTML = ""; });
        return;
      }
      this._grid(box, rows, sec.title, page.paged ? { ask: page.ask, count: sec.count, next: sec.next } : null);
    });
    const b = view.querySelector(".fback");
    if (b) b.addEventListener("click", () => this._back());
    const pl = view.querySelector(".fplay");
    if (pl) pl.addEventListener("click", () => this._play(page.play, pl));
  }

  /* One grid of tiles, a hundred at a time; more from the Control when it
     says there is more (the webapp's moreHtml). */
  _grid(box, rows, title, more) {
    const movies = this._movies;
    box.innerHTML = (title ? `<div class="section">${esc(title)}</div>` : "") + '<div class="tiles"></div>';
    const grid = box.querySelector(".tiles");
    let shown = 0;
    let list = this._movies ? rows.slice() : dedupe(rows);
    let next = more ? more.next : null;
    const total = () => Math.max(list.length, Number(more && more.count) || 0);
    const draw = () => {
      const part = list.slice(shown, shown + BROWSE_PAGE);
      grid.insertAdjacentHTML("beforeend", this._tiles(part, movies, shown));
      shown += part.length;
      this._bind_tiles(grid, list);
      this._lazy(grid);
      /* a playlist's four covers, a work's record cover - into its tile when they come */
      if (!movies) {
        this._fill_covers(part, () => part.forEach((it) => {
          const i = list.indexOf(it);
          const cover = grid.querySelector(`.tile[data-i="${i}"] .cover`);
          if (cover && !it.image && it.covers && it.covers.length && !cover.querySelector(".app-find__mosaic")) {
            const play = cover.querySelector(".tile-play");
            cover.innerHTML = mosaic_html(it.covers);
            if (play) cover.appendChild(play);
          }
        }));
      }
      const old = box.querySelector(".more");
      if (old) old.remove();
      const left = shown < list.length || (more && next !== null && next !== undefined && Number(next) < total() && list.length < total());
      if (left) {
        box.insertAdjacentHTML("beforeend", `<button type="button" class="more">Loading more… (${shown} of ${total()})</button>`);
        const btn = box.querySelector(".more");
        btn.addEventListener("click", step);
        if (window.IntersectionObserver) {
          const watch = new window.IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting)) { watch.disconnect(); step(); }
          }, { rootMargin: "0px 0px 600px 0px" });
          watch.observe(btn);
        }
      }
    };
    const step = async () => {
      if (shown < list.length) { draw(); return; }
      /* the next part, asked from where the Control said (its `next`) */
      try {
        const r = await this._find(more.ask(Number(next)));
        const ans = r.answer || {};
        /* one record, one tile - over everything loaded (two pressings can
           land either side of a page's edge) */
        list = movies ? list.concat(ans.items || []) : dedupe(list.concat(ans.items || []));
        next = ans.next;
      } catch (e) { return; }
      draw();
    };
    if (!rows.length) { grid.outerHTML = '<p class="note">Nothing here</p>'; return; }
    draw();
  }

  /* A tile as find-music.js tilesHtml draws it: the cover (its picture, else
     the four covers from inside, else the letter), its name and artist under
     it (Michel 2026-10-26: title and artist on every tile). A person without a
     picture is not drawn (pictureless). 0.3.21: a tile that both opens and
     plays carries a play button on its cover - one tap plays, the tile opens
     the page (Michel: "When I click an album now it doesn't play"). */
  _tiles(items, movies, from) {
    return items.map((it, n) => {
      const i = from + n;
      if (!movies && pictureless(it)) return "";
      const sub = movies ? String(it.year || it.subtitle || "") : tile_sub(it);
      const name = String(it.title || it.name || "");
      const label = name + (sub ? " — " + sub : "");
      const pic = String(it.image || it.portrait || "");
      const mosaic = !pic && Array.isArray(it.covers) && it.covers.length ? mosaic_html(it.covers) : "";
      const opens = this._openable(it);
      const play = opens && !movies && this._quick_playable(it)
        ? `<span class="tile-play" role="button" tabindex="0" data-play="${i}" aria-label="${esc("Play " + name)}" title="Play">${PLAY_SVG}</span>` : "";
      return `<button type="button" class="tile${opens || it.content_id ? "" : " dead"}" data-i="${i}" title="${esc(label)}" aria-label="${esc(label)}">` +
        `<span class="cover${movies && !it.portrait ? " poster" : ""}"${pic ? ` data-bg="${esc(pic)}"` : ""}>${pic ? "" : (mosaic || esc(name.trim().charAt(0).toUpperCase() || "?"))}${play}</span>` +
        `<span class="tile-name">${esc(name)}</span>` + (sub ? `<span class="tile-sub">${esc(sub)}</span>` : "") +
        (it.elsewhere ? `<span class="tile-sub elsewhere">on ${esc(it.elsewhere)}</span>` : "") + "</button>";
    }).join("");
  }

  _lazy(box) {
    const covers = Array.from(box.querySelectorAll("[data-bg]"));
    const show = (c) => {
      c.style.backgroundImage = `url("${String(c.getAttribute("data-bg")).replace(/"/g, "%22")}")`;
      c.removeAttribute("data-bg");
    };
    if (!window.IntersectionObserver) { covers.forEach(show); return; }
    if (!this._covers) {
      this._covers = new window.IntersectionObserver((entries) => entries.forEach((e) => {
        if (e.isIntersecting) { show(e.target); this._covers.unobserve(e.target); }
      }), { rootMargin: "600px 0px 600px 0px" });
    }
    covers.forEach((c) => this._covers.observe(c));
  }

  /* find-music.js fillCovers: four covers from inside a Roon playlist (Roon
     gives it none of its own), and - 0.3.21, Michel "compositions, no
     artwork" - the cover of a composition's record, which the Control's
     `covers` read already answers (music_answer.php: a composer's work names
     its album, found in Control's own list). Asked once per thing. */
  _fill_covers(list, redraw) {
    (list || []).forEach((it) => {
      if (!it || it.image || it.covers || !Array.isArray(it.path) || !it.path.length || !wants_covers(it)) return;
      it.covers = [];
      this._find({ action: "covers", path: JSON.stringify(it.path) }).then((r) => {
        it.covers = ((r.answer || {}).covers || []).filter(Boolean);
        if (it.covers.length && redraw) redraw();
      }, () => { /* no mosaic: the letter stays */ });
    });
  }

  /* An album, playlist, artist or composer opens its page (the webapp's
     openItem); a song or station plays. */
  _openable(it) {
    if (!it) return false;
    if (this._movies) return !!it._open;
    if (Array.isArray(it.path) && it.path.length && it.hint !== "action") return true;
    if (it.source && String(it.source).startsWith("sonos_") && it.key && it.type !== "Track" && it.type !== "Radio") return true;
    return !!(it.uri && /(^|:)(album|playlist|artist|station):|^A:ALBUM\/|^sonoslib:artist:|^heosp:/.test(String(it.uri)) &&
      !/^tunein:/.test(String(it.uri)));
  }

  _tap(it, node) {
    if (!it) return;
    if (this._openable(it)) { this._open(it); return; }
    if (it.content_id) this._play(it, node);
  }

  /* Opening one thing - find-music.js openItem: what Control keeps about it
     (about), what the player can do with it (Roon's actions, a service's
     Play / Shuffle), something to read (text) and what is INSIDE it, all
     under one generation so a slow answer never paints over a newer page. */
  _open(it) {
    this._stack.push(this._detail ? { detail: this._detail } : { tab: this._tab });
    if (this._movies) {
      this._detail = null;
      this._page({ title: String(it.title || it.name || ""), sections: [{ title: "", items: it._open() }] });
      return;
    }
    this._open_item(it);
  }

  _open_item(it) {
    const gen = (this._gen = (this._gen || 0) + 1);
    const d = this._detail = { it, gen, actions: null, about: null, inside: null, next: 0, total: 0, text: "", note: "", folded: {} };
    const live = () => this._detail === d && this._gen === gen;
    const roon = Array.isArray(it.path) && it.path.length > 0;
    const spot = spotify_identity(it);
    const fav = !roon && it.source && String(it.source).startsWith("sonos_") && it.key && !it.uri;
    this._paint_detail();
    /* each read kept-then-fresh: its handler runs again with the fresh answer */
    const ask = (req) => ({
      then: (ok, bad) => this._kept_then_fresh(req, (r) => ok((r && r.answer) || {})).catch(bad || (() => {})),
    });
    if (roon) {
      ask({ action: "about", id: it.id || "", path: JSON.stringify(it.path), title: it.title || "", artist: it.subtitle || "" })
        .then((a) => { if (live()) { d.about = a; this._paint_detail(); } }, () => {});
      ask({ action: "actions", path: JSON.stringify(it.path) }).then((a) => {
        if (!live()) return;
        d.actions = Array.isArray(a.actions) ? a.actions.map(String) : [];
        if (a.error) d.note = String(a.error);
        this._paint_detail();
      }, () => { if (live()) { d.actions = []; d.note = "Roon did not answer"; this._paint_detail(); } });
      const who = is_person(it) ? String(it.title || "") : String(it.subtitle || "");
      if (who) ask({ action: "text", artist: who }).then((a) => { if (live() && a.text) { d.text = String(a.text); this._paint_detail(); } }, () => {});
      this._inside_page(d, 0);
      return;
    }
    /* a service's album, playlist or artist by its address; a Sonos favourite
       by its key (sonos_about) - the webapp's readers */
    d.actions = spot === "track" ? ["Play"] : (fav ? ["Play"] : ["Play", "Shuffle"]);
    if (it.source === "heos" || it.source === "zidoo" || /^heosp:/.test(String(it.uri || ""))) d.actions = ["Play"];
    const kind = String(it.type || it.kind || "").toLowerCase();
    if (it.title && (kind === "album" || spot === "album")) {
      ask({ action: "text", artist: it.subtitle || "", album: it.title }).then((a) => { if (live() && a.text) { d.text = String(a.text); this._paint_detail(); } }, () => {});
    } else if (it.title && (kind === "artist" || spot === "artist")) {
      ask({ action: "text", artist: it.title }).then((a) => { if (live() && a.text) { d.text = String(a.text); this._paint_detail(); } }, () => {});
    }
    const req = fav ? { action: "sonos_about", key: it.key }
      : { action: "album", uri: spot === "playlist" ? (it.key || it.uri) : (it.uri || it.key), title: it.title || "", subtitle: it.subtitle || "" };
    ask(req).then((a) => {
      if (!live()) return;
      d.about = { year: a.year || "", label: a.label || "", total: Number(a.total) || 0, minutes: a.minutes || null, kind: a.kind || "" };
      if (a.subtitle && !it.subtitle) it.subtitle = String(a.subtitle);
      if (a.image && !it.image) it.image = String(a.image);
      d.inside = (a.tracks || []).map((t) => ({ title: t.title, subtitle: t.subtitle || "", duration: t.duration || "", image: t.image || "",
        uri: t.uri || "", source: it.source, type: t.kind === "album" ? "Album" : "", kind: t.kind || "", content_id: t.content_id, content_type: t.content_type }));
      d.total = Number(a.total) || d.inside.length;
      if (a.error) d.note = String(a.error);
      this._paint_detail();
    }, (e) => { if (live()) { d.inside = []; d.note = "The contents did not load"; this._paint_detail(); } });
  }

  /* What is inside it, one page: an album's tracks, an artist's records (one
     tile a record: find-music.js dedupe), a composer's works. */
  _inside_page(d, offset) {
    const it = d.it;
    const req = { action: "browse", path: JSON.stringify(it.path), offset, as: is_album(it) ? "album" : "" };
    (offset > 0 ? this._find(req).then((r) => { handle(r); return r; }) : this._kept_then_fresh(req, (r) => handle(r))).catch(() => {
      if (this._detail === d) { if (!d.inside) d.inside = []; this._paint_detail(); }
    });
    const handle = (r) => {
      if (this._detail !== d) return;
      const a = r.answer || {};
      const within = (a.items || []).filter((x) => !(x.kind === "action" || (/^Play /.test(String(x.title || "")) && !x.image)));
      const all = (offset > 0 ? (d.inside || []) : []).concat(within);
      d.inside = inside_label(it, all) === "Tracks" ? all : dedupe(all, d.folded);
      d.next = typeof a.next === "number" ? a.next : offset + within.length;
      d.total = Number(a.count) || d.inside.length;
      if (this._follow_one(d)) return;
      this._fill_covers(d.inside, () => { if (this._detail === d) this._paint_detail(); });
      this._paint_detail();
    };
  }

  /* find-music.js followOne: a search hit Roon cannot play that holds exactly
     one thing of its name is a wrapper - go into it. */
  _follow_one(d) {
    if (d.actions === null || d.inside === null || d.actions.length) return false;
    const same = d.inside.filter((x) => album_key(x.title) === album_key(d.it.title));
    if (same.length !== 1) return false;
    this._open_item(same[0]);
    return true;
  }

  _back() {
    const prev = this._stack.pop();
    if (!prev) return;
    if (prev.detail) { this._detail = prev.detail; this._gen = (this._gen || 0) + 1; this._detail.gen = this._gen; this._paint_detail(); return; }
    this._detail = null;
    this._el.browse.classList.remove("is-open");
    /* opened from a search: back to its results, as they were */
    if (this._el.search.value.trim()) { this._el.view.hidden = true; this._el.tabs.hidden = true; return; }
    if (prev.tab && prev.tab !== this._tab) this._tab = prev.tab;
    this._open_tab();
  }

  /* The page - find-music.js openHtml, markup and app.css rules as they are:
     the cover with the title, artist and info line beside it, the words, the
     play buttons in one row, then what is inside (a numbered track list in the
     Queue's rows, or covers). Back is the webapp's top-bar chevron: here the
     small button above the page. */
  _paint_detail() {
    const d = this._detail;
    if (!d) return;
    /* find-music-bind.js: no search box and no tab strip on the detail page -
       you are looking at one album, not searching */
    this._el.browse.classList.add("is-open");
    this._el.view.hidden = false;
    const it = d.it;
    const view = this._el.view;
    const url = String(it.image || "");
    const mosaic = !url && Array.isArray(it.covers) && it.covers.length ? mosaic_html(it.covers) : "";
    const letter = esc(String(it.title || "?").trim().charAt(0).toUpperCase());
    const ab = d.about || {};
    const bits = [];
    if (ab.year) bits.push(String(ab.year));
    if (ab.label) bits.push(String(ab.label));
    if (Number(ab.total) > 0) {
      const n = Number(ab.total);
      const noun = ab.kind === "artist" ? "album" : "track";
      bits.push(`${n} ${noun}${n === 1 ? "" : "s"}`);
    }
    if (ab.minutes) bits.push(`${ab.minutes} min`);
    const info = bits.join(" · ");
    const sub = tile_sub(it);
    const acts = this._play_options(d);
    const buttons = d.actions === null ? '<p class="app-find__note">Please wait<span class="dots inline"></span></p>'
      : (acts.length ? `<div class="app-find__plays">${acts.map((a) =>
        `<button type="button" class="app-find__play" data-act="${esc(a)}">${esc(a)}</button>`).join("")}</div>` : "");
    const label = d.inside && d.inside.length ? inside_label(it, d.inside) : "";
    const listed = is_listed(label) || (!label && d.inside && d.inside.length && d.inside.every((x) => !x.path && x.type !== "Album" && x.kind !== "album"));
    const back = this._stack.length ? '<button type="button" class="app-find__back fback">‹ Back</button>' : "";
    view.innerHTML = back +
      `<div class="app-find__open"><span class="app-find__open-cover"${url ? ` style="background-image: url(&quot;${esc(url)}&quot;)"` : ""}>${url ? "" : (mosaic || letter)}</span>` +
      `<div class="app-find__open-text"><div class="app-find__open-title">${esc(it.title || it.name || "")}</div>` +
      (sub ? `<div class="app-find__open-sub">${esc(sub)}</div>` : "") +
      (info ? `<div class="app-find__open-sub">${esc(info)}</div>` : "") + "</div></div>" +
      (d.text ? `<p class="app-find__text${d.text_open ? " is-open" : ""}">${esc(d.text)}</p>` : "") +
      buttons +
      (d.note ? `<p class="app-find__note">${esc(d.note)}${d.wait ? '<span class="dots inline"></span>' : ""}</p>` : "") +
      (d.inside === null ? (d.actions === null ? "" : '<p class="app-find__note">Please wait<span class="dots inline"></span></p>')
        : (d.inside.length
          ? `<h4 class="app-find__section">${esc((label || (listed ? "Tracks" : "")) + (label || listed ? " · " : "") + d.inside.length)}</h4>` +
            (listed ? this._rows_html(d) : `<div class="tiles">${this._tiles(d.inside, false, 0)}</div>`) +
            (d.total > d.inside.length && Array.isArray(it.path) ? `<button type="button" class="app-find__more">Loading more… (${d.inside.length} of ${d.total})</button>` : "")
          : ""));
    const q = (s) => view.querySelector(s);
    if (q(".fback")) q(".fback").addEventListener("click", () => this._back());
    if (q(".app-find__text")) q(".app-find__text").addEventListener("click", () => { d.text_open = !d.text_open; this._paint_detail(); });
    view.querySelectorAll(".app-find__play").forEach((b) => b.addEventListener("click", () => this._play_action(d, b.dataset.act, b)));
    view.querySelectorAll(".app-queue__open").forEach((b) => b.addEventListener("click", () => {
      const x = d.inside[Number(b.dataset.i)];
      if (this._openable(x) || (Array.isArray(x && x.path) && x.path.length)) this._open(x);
      else this._play_track(d, x, b);
    }));
    view.querySelectorAll(".app-queue__jump[data-i]").forEach((b) => b.addEventListener("click", () => this._play_track(d, d.inside[Number(b.dataset.i)], b)));
    this._bind_tiles(view.querySelector(".tiles"), d.inside || []);
    const more = q(".app-find__more");
    if (more) more.addEventListener("click", () => { more.disabled = true; this._inside_page(d, d.next); });
    this._lazy(view);
  }

  _bind_tiles(grid, list) {
    if (!grid) return;
    grid.querySelectorAll(".tile:not([data-bound])").forEach((t) => {
      t.setAttribute("data-bound", "1");
      t.addEventListener("click", () => this._tap(list[Number(t.dataset.i)], t));
    });
    grid.querySelectorAll(".tile-play").forEach((p) => {
      const go = (e) => { e.stopPropagation(); e.preventDefault(); this._quick_play(list[Number(p.dataset.play)], p); };
      p.addEventListener("click", go);
      p.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") go(e); });
    });
  }

  /* find-music.js rowsHtml - the Queue page's rows: the cover (0.3.21,
     Michel: "Tracks no artwork" - the track's own picture, else the record's
     or the playlist's cover; the webapp's number moves before the name), the
     name, the artist on a playlist (on an album it is the album's own and
     left off), and the play button. */
  _rows_html(d) {
    const it = d.it;
    const own = String(it.image || "") || (Array.isArray(it.covers) && it.covers[0]) || "";
    const with_sub = !is_album(it);
    return '<div class="app-queue__list">' + d.inside.map((x, i) => {
      const part = track_parts(x.title);
      const covers = Array.isArray(x.covers) && x.covers.length ? x.covers : null;
      const pic = String(x.image || "") || (covers ? covers[0] : "") || own;
      const playable = this._track_playable(d, x);
      return '<div class="app-queue__row">' +
        `<button type="button" class="app-queue__open" data-i="${i}">` +
        `<span class="app-queue__cover${pic ? "" : " app-queue__cover--number"}"${pic ? ` data-bg="${esc(pic)}"` : ""}>${pic ? "" : esc(part.number || part.name.trim().charAt(0).toUpperCase() || "?")}</span>` +
        `<span class="app-queue__text"><span class="app-queue__name">${part.number ? `<span class="app-queue__no">${esc(part.number)}</span>` : ""}${esc(part.name)}</span>` +
        (with_sub && x.subtitle ? `<span class="app-queue__sub">${esc(x.subtitle)}</span>` : "") + "</span></button>" +
        (x.duration ? `<span class="app-queue__time">${esc(clock(Number(x.duration)) || String(x.duration))}</span>` : "") +
        (playable ? `<button type="button" class="app-queue__jump" data-i="${i}" aria-label="Play now">${PLAY_SVG}</button>` : "") +
        "</div>";
    }).join("") + "</div>";
  }

  /* What the page offers: the player's own words (Roon's actions; a
     service's Play / Shuffle) - while nothing runs, every one of them first
     starts the room's music (the Control plays only for the running
     activity); a thing the Control hands a play for also plays through
     play_media, which does the same in one step. */
  _play_options(d) {
    if (d.actions === null) return [];
    if (d.actions.length) return d.actions;
    return d.it.content_id ? ["Play"] : [];
  }

  _track_playable(d, x) {
    if (!x || x.playable === false || x.type === "Album" || x.kind === "album") return false;
    if (x.content_id) return true;
    if (Array.isArray(x.path) && x.path.length) return true;
    if (d.it.source === "zidoo") return false;
    return !!x.uri;
  }

  _play_action(d, action, btn) {
    const it = d.it;
    const first = action === "Play" || action === "Play Now";
    /* nothing runs and the Control handed this a play: play_media starts the
       activity and plays it in one step */
    if (it.content_id && first && (this._idle !== false || !(d.actions && d.actions.length))) { this._play(it, btn); return; }
    if (Array.isArray(it.path) && it.path.length) { this._play_item(d, { path: it.path }, action, btn); return; }
    if (it.source && String(it.source).startsWith("sonos_") && it.key && !it.uri) {
      this._play_door(d, { action: "play", source: it.source, key: it.key, mode: action === "Shuffle" ? "shuffle" : "now" }, btn);
      return;
    }
    const sp = spotify_identity(it);
    const item = { kind: String(it.kind || "") || sp || (String(it.type || "") === "Album" ? "album" : "track"),
                   uri: sp === "playlist" ? (it.key || it.uri) : (it.uri || it.key) };
    if (/^apple:station:/.test(String(item.uri || ""))) item.title = it.title;
    this._play_item(d, item, action === "Shuffle" ? "shuffle" : "now", btn);
  }

  /* One song - find-music.js api.track: Roon by its path; a service's song
     with the songs after it (a Sonos room plays a list). */
  _play_track(d, x, btn) {
    if (!this._track_playable(d, x)) return;
    if (x.content_id && this._idle !== false) { this._play(x, btn); return; }
    if (Array.isArray(x.path) && x.path.length) { this._play_item(d, { path: x.path }, "Play Now", btn); return; }
    const uris = d.inside.map((t) => t.uri).filter(Boolean);
    const at = uris.indexOf(x.uri);
    const item = { kind: "track", uri: x.uri };
    if (at !== -1) item.uris = uris.slice(at, at + 50);
    const sp = spotify_identity(d.it);
    if (sp === "album" || sp === "playlist") {
      const addr = /^spotify:(album|playlist):/.test(String(d.it.uri || "")) ? d.it.uri : d.it.key;
      if (/^spotify:(album|playlist):[A-Za-z0-9]+$/.test(String(addr || ""))) item.context = addr;
    }
    this._play_item(d, item, "Play Now", btn);
  }

  /* The play button on a cover: the Control's own play (play_media) when it
     handed one; else a record or playlist by its path or address through the
     door (Play Now) - starting the room's music first when nothing runs. */
  _quick_playable(it) {
    if (!it || this._movies) return false;
    if (it.content_id) return true;
    if (Array.isArray(it.path) && it.path.length) return is_album(it) || (path_root(it) === "playlists" && it.path.length === 2);
    return /^[a-z]+:(album|playlist):|^A:ALBUM\//.test(String(it.uri || "")) && it.source !== "heos" && it.source !== "zidoo";
  }

  _quick_play(it, btn) {
    if (!it) return;
    if (it.content_id) { this._play(it, btn.closest(".tile") || btn); return; }
    if (Array.isArray(it.path) && it.path.length) { this._play_door(null, { action: "play_item", item: JSON.stringify({ path: it.path }), do: "Play Now" }, btn); return; }
    const sp = spotify_identity(it);
    this._play_door(null, { action: "play_item", item: JSON.stringify({ kind: sp || (/:playlist:/.test(String(it.uri)) ? "playlist" : "album"), uri: it.uri }), do: "now" }, btn);
  }

  _play_item(d, item, how, btn) {
    this._play_door(d, { action: "play_item", item: JSON.stringify(item), do: how }, btn);
  }

  /* The play itself, through the room's door. ⚠ The Control plays only for
     the RUNNING activity: with nothing running the room's music activity is
     started first (start_activity, the one Browse is for) and the play sent
     once it runs - the dots meanwhile; a refusal is said on the page. */
  async _play_door(d, req, btn) {
    /* on the page: its note; from a cover: only a failure, as a toast */
    const say = (text, wait) => {
      if (d && this._detail === d) { d.note = text; d.wait = !!wait; this._paint_detail(); }
      else if (!d && text && !wait) toast(this, { message: text });
    };
    if (btn) btn.classList.add("busy");
    const send = () => this._find(req).then((r) => {
      const a = r.answer || {};
      if (a.ok === false) throw new Error(String(a.error || a.message || "It did not start"));
      return a;
    });
    try {
      try {
        await send();
      } catch (e) {
        if (!e || e.code !== "not_running") throw e;
        const activity = Number(this._activity || 0);
        if (!activity) throw e;
        say("Starting", true);
        await this._hass.callService(DOMAIN, "start_activity", { entity_id: this._config.entity, activity });
        let done = false;
        for (let n = 0; n < BROWSE_START_TRIES && !done; n += 1) {
          await new Promise((r) => window.setTimeout(r, BROWSE_START_MS));
          try { await send(); done = true; } catch (e2) { if (!e2 || e2.code !== "not_running") throw e2; }
        }
        if (!done) throw new Error("The music did not start");
      }
      say("");
    } catch (e) {
      say(error_text(e));
    } finally {
      if (btn) btn.classList.remove("busy");
    }
  }

  /* Find Movies' tabs over its one titles list (find-movies.js), and its people */
  async _movie_tab(id) {
    const films = (this._films || []).filter((t) => !t.hidden);
    const as = (list) => list.map((t) => ({ ...t, title: t.name, subtitle: t.year ? String(t.year) : "" }));
    const by_name = (a, b) => String(a.name || "").localeCompare(String(b.name || ""), undefined, { sensitivity: "base" });
    if (id === "recent") {
      this._page({ sections: [{ title: "", items: as(films.slice().sort((a, b) => String(b.added || "").localeCompare(String(a.added || ""))).slice(0, 48)) }] });
    } else if (id === "loved") {
      this._page({ sections: [{ title: "", items: as(films.filter((t) => t.favorite).sort(by_name)) }] });
    } else if (id === "movies") {
      this._page({ sections: [{ title: "", items: as(films.slice().sort(by_name)) }] });
    } else if (id === "genres") {
      const genres = Array.from(new Set(films.flatMap((t) => String(t.genres || "").split(",").map((g) => g.trim()).filter(Boolean)))).sort();
      this._page({ sections: [{ title: "", items: genres.map((g) => ({ title: g, _open: () => as(films.filter((t) => String(t.genres || "").split(",").map((x) => x.trim()).includes(g)).sort(by_name)) })) }] });
    } else {
      const kind = id === "directors" ? "director" : "actor";
      let people = [];
      try { people = ((await this._find({ action: "people", kind })).answer || {}).people || []; } catch (e) { /* none */ }
      if (this._tab !== id) return;
      this._page({ sections: [{ title: "", items: people.filter((p) => p.portrait).map((p) => ({
        title: p.name, portrait: p.portrait, image: p.portrait,
        _open: () => as(films.filter((t) => (p.titles || []).includes(t.id)).sort(by_name)) })) }] });
    }
  }

  /* SEARCH stays the quick field across everything (Michel 2026-10-26): its
     results INSTEAD of the tab, never one album twice on one screen. */
  async _search(fresh_of) {
    const q = this._el.search.value.trim();
    this._el.view.hidden = !!q;
    this._el.tabs.hidden = !!q;
    if (!q) { this._el.results.innerHTML = ""; return; }
    /* fresh_of: the kept answer drawn already, asked again (kept, then fresh) */
    if (fresh_of && fresh_of.q !== q) return;
    if (!fresh_of) this._search_gen = (this._search_gen || 0) + 1;
    const ask = this._search_gen;
    if (!fresh_of) this._el.results.innerHTML = '<p class="note is-searching">Searching<span class="dots inline"></span></p>';
    let answer;
    try {
      answer = await this._hass.callWS({ type: "artiled_control/search", entity_id: this._config.entity, query: q,
                                         ...(this._movies ? { movies: true } : {}), ...(fresh_of ? { fresh: true } : {}) });
    } catch (e) {
      if (ask === this._search_gen && !fresh_of) this._el.results.innerHTML = `<p class="note">${esc(error_text(e))}</p>`;
      return;
    }
    if (ask !== this._search_gen) return;
    if (fresh_of && JSON.stringify(answer.result) === fresh_of.was) return;
    if (!fresh_of && answer && Number(answer.kept) > 0) {
      const was = JSON.stringify(answer.result);
      window.setTimeout(() => this._search({ q, was }), 0);
    }
    /* title and artist, as the webapp's tile; the activity ONLY when it is not
       the one running (Michel 2026-10-26: "I know I'm listening to Roon") */
    const running = Number(attrs(this._hass.states[this._config.entity]).activity_id || 0);
    const rows = ((answer && answer.result) || []).filter((it) => it && it.content_id && it.image)
      .map((it) => ({ title: it.title, subtitle: it.subtitle || "", image: it.image, content_id: it.content_id, content_type: it.content_type,
                      elsewhere: Number(it.activity) !== running ? this._activity_name(it.content_id) : "" }));
    const box = this._el.results;
    box.innerHTML = "";
    if (!rows.length) { box.innerHTML = `<div class="section">${esc(`Results for "${q}"`)}</div><p class="note">Nothing found</p>`; return; }
    this._grid(box, rows, `Results for "${q}"`, null);
  }

  _activity_name(content_id) {
    const m = String(content_id || "").match(/^play:(\d+):/);
    if (!m) return "";
    const row = (attrs(this._hass.states[this._config.entity]).activities || []).find((r) => Number(r.id) === Number(m[1]));
    return row ? String(row.name || "") : "";
  }

  /* 0.3.24: the tapped tile STAYS, with the starting look (a light frame and
     "Starting" with the moving dots) until the Control has taken the play -
     never a blank list (Michel 2026-10-26: "everything disappears"). */
  _play(it, node) {
    if (!it || !it.content_id) return;
    node.classList.remove("failed");
    const tile = node.classList && node.classList.contains("tile") ? node : (node.closest ? node.closest(".tile") : null);
    let note = null;
    if (tile && !tile.classList.contains("is-starting")) {
      tile.classList.add("is-starting");
      note = document.createElement("span");
      note.className = "tile-sub tile-starting";
      note.innerHTML = 'Starting<span class="dots inline"></span>';
      tile.appendChild(note);
    }
    const since = Date.now();
    const done = () => window.setTimeout(() => {
      if (tile) tile.classList.remove("is-starting");
      if (note && note.parentNode) note.parentNode.removeChild(note);
    }, Math.max(0, BROWSE_STARTING_MIN_MS - (Date.now() - since)));
    this._hass.callService("media_player", "play_media", {
      entity_id: this._config.entity, media_content_id: it.content_id, media_content_type: it.content_type || (this._movies ? "movie" : "music"),
    }).then(done, (err) => { done(); node.classList.add("failed"); toast(this, err); });
  }
}

/* ------------------------------------------------------------------ *
 * The room's Favorites (0.3.25, Michel 2026-10-26: "Favorites are the
 * same in all rooms ... but NOT in all services").
 * ------------------------------------------------------------------ */

/* ONE list - the Control's hearts, the same on every room's page and on the
   Voice page - grouped per SERVICE (the webapp's Music > Favorites shows where
   each plays). A tap plays it in THIS room through ITS service: on the
   running activity when that is the service's, else the room switches to its
   activity of that service (artiled_control/play_favorite). One this room has
   no activity for is shown dimmed; a tap says so and sends nothing.
   Drawn from what this browser kept, then kept-then-fresh (0.3.24); a tapped
   tile keeps its place with "Starting". The tiles are Browse's. */
const FAVCARD_STYLE = BROWSE_STYLE + `
.favhead { margin: 2px 0 0; font-size: 18px; font-weight: 700; }
.tile.away .cover { opacity: .4; }
.tile.away .tile-name, .tile.away .tile-sub { opacity: .5; }
`;

class ArtiControlFavoritesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("artiled-control-favorites-card needs an entity");
    this._config = config;
    this._built = false;
  }

  getCardSize() { return 6; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
      this._load();
    }
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>${FAVCARD_STYLE}</style>
      <div class="page"><div class="browse">
        <div class="favhead">Favorites</div>
        <div class="ftabs" role="tablist" aria-label="Service"></div>
        <div class="view"><p class="note is-searching">Loading<span class="dots inline"></span></p></div>
      </div></div>`;
    this._el = { tabs: this.shadowRoot.querySelector(".ftabs"), view: this.shadowRoot.querySelector(".view") };
    this._built = true;
  }

  async _load() {
    const key = `favroom|${this._config.entity}`;
    const ask = (fresh) => this._hass.callWS({ type: "artiled_control/favorites", entity_id: this._config.entity, ...(fresh ? { fresh: true } : {}) });
    const mem = kept_get(key);
    if (mem) this._take(mem);
    let r;
    try {
      r = await ask(false);
    } catch (e) {
      if (!mem) this._el.view.innerHTML = `<p class="note">${esc(error_text(e))}</p>`;
      return;
    }
    kept_put(key, r || {});
    this._take(r);
    if (r && Number(r.kept) > 0) {
      try { const f = await ask(true); kept_put(key, f || {}); this._take(f); } catch (e) { /* what was kept stands */ }
    }
  }

  _take(r) {
    const sig = JSON.stringify([(r && r.items) || [], (r && r.services) || []]);
    if (sig === this._drawn) return;
    this._drawn = sig;
    this._r = r || {};
    this._paint();
  }

  _paint() {
    const r = this._r || {};
    const items = (r.items || []).filter((it) => it && it.play);
    const services = fav_services(r);
    const svc = fav_service_shown(r, FAVCARD_UI.svc[this._config.entity]);
    this._el.tabs.innerHTML = services.map((x) =>
      `<button type="button" class="ftab${x.id === svc ? " on" : ""}" data-s="${esc(x.id)}">${esc(x.name)}</button>`).join("");
    this._el.tabs.hidden = services.length < 2;
    this._el.tabs.querySelectorAll(".ftab").forEach((b) => b.addEventListener("click", () => {
      FAVCARD_UI.svc[this._config.entity] = b.dataset.s;
      this._paint();
    }));
    const shown = items.filter((it) => !svc || it.service === svc);
    if (!shown.length) {
      this._el.view.innerHTML = `<p class="note">No favorites yet${(r.errors && r.errors.length) ? ` (${esc(r.errors.join("; "))})` : ""}</p>`;
      return;
    }
    this._list = shown;
    this._el.view.innerHTML = '<div class="tiles">' + shown.map((it, i) => {
      const name = String(it.title || "");
      const sub = String(it.subtitle || "");
      const pic = String(it.image || "");
      const label = name + (sub ? " — " + sub : "");
      return `<button type="button" class="tile${it.here === false ? " away" : ""}" data-i="${i}" title="${esc(label)}" aria-label="${esc(label)}">` +
        `<span class="cover"${pic ? ` style="background-image: url(&quot;${esc(pic)}&quot;)"` : ""}>${pic ? "" : esc(name.trim().charAt(0).toUpperCase() || "?")}</span>` +
        `<span class="tile-name">${esc(name)}</span>` + (sub ? `<span class="tile-sub">${esc(sub)}</span>` : "") + "</button>";
    }).join("") + "</div>";
    this._el.view.querySelectorAll(".tile").forEach((t) => t.addEventListener("click", () => this._play(this._list[Number(t.dataset.i)], t)));
  }

  /* In THIS room, by its own service (favorites.play_in_room): the tile keeps
     its place, "Starting" with the moving dots, until the Control took it. */
  _play(it, tile) {
    if (!it || tile.classList.contains("is-starting")) return;
    tile.classList.remove("failed");
    tile.classList.add("is-starting");
    const note = document.createElement("span");
    note.className = "tile-sub tile-starting";
    note.innerHTML = 'Starting<span class="dots inline"></span>';
    tile.appendChild(note);
    const since = Date.now();
    const done = () => window.setTimeout(() => {
      tile.classList.remove("is-starting");
      if (note.parentNode) note.parentNode.removeChild(note);
    }, Math.max(0, BROWSE_STARTING_MIN_MS - (Date.now() - since)));
    this._hass.callWS({ type: "artiled_control/play_favorite", entity_id: this._config.entity, play: it.play })
      .then((res) => {
        done();
        if (res && NOT_PLAYED_STATES.has(String(res.state || ""))) {
          tile.classList.add("failed");
          toast(this, { message: `${res.room}: the Control did not play ${it.title} (${res.state})` });
        }
      }, (err) => { done(); tile.classList.add("failed"); toast(this, err); });
  }
}

const FAVCARD_UI = { svc: {} };
/* media.NOT_PLAYED: what the Control says of a start's play that means it was NOT played */
const NOT_PLAYED_STATES = new Set(["failed", "discarded", "skipped", "unresolved", "refused"]);

/* ------------------------------------------------------------------ *
 * The Voice page - a copy of the Hub integration's ArtiLedVoiceCard.
 * ------------------------------------------------------------------ */

/* The same three assistants as const.ASSISTANTS, in the same order. */
const ASSISTANTS = [
  ["conversation", "Assist", "mdi:home-assistant"],
  ["cloud.alexa", "Alexa", "mdi:microphone"],
  ["cloud.google_assistant", "Google", "mdi:google"],
];

/* Domains an assistant can be asked to control: a name clash only matters
   between two things a person could mean by one sentence (the Hub's list). */
const VOICE_DOMAINS = new Set([
  "light", "switch", "cover", "media_player", "climate", "fan", "lock", "scene",
]);

/* The Hub's VOICE_STYLE, unchanged. */
const VOICE_STYLE = `
:host { display: block; }
.wrap { padding: 4px 12px 16px; }
.head {
  font-size: 13px; font-weight: 600; letter-spacing: .04em;
  text-transform: uppercase; margin: 22px 0 10px;
  color: var(--secondary-text-color, #aaa);
}
.head:first-child { margin-top: 4px; }
.tabs {
  display: flex; margin: 0 0 4px; background: transparent;
  border-bottom: 2px solid var(--divider-color, rgba(255,255,255,.5));
}
.tab {
  position: relative; flex: 1 1 0; min-width: 0;
  display: flex; align-items: center; justify-content: center;
  min-height: 48px; padding: 14px 2px; font-size: 14px;
  color: var(--secondary-text-color, rgba(255,255,255,.5));
  background: none; border: 0; cursor: pointer; font-family: inherit;
  -webkit-tap-highlight-color: transparent;
}
.tab.on { color: var(--primary-text-color, #fff); }
.tab.on::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -2px;
  height: 2px; background: var(--primary-text-color, #fff);
}
.panel { display: none; }
.panel.on { display: block; }
.item {
  padding: 12px 0;
  border-bottom: 1px solid var(--divider-color, rgba(255,255,255,.12));
}
.name {
  font-size: 16px; font-weight: 600; margin-bottom: 8px;
  color: var(--primary-text-color, #fff);
}
.clash {
  display: flex; gap: 6px; align-items: flex-start;
  margin: 0 0 8px; padding: 8px 10px;
  border-radius: 4px; font-size: 13px; line-height: 1.35;
  color: var(--primary-text-color, #fff);
  background: var(--warning-color, #ffa726);
  background: color-mix(in srgb, var(--warning-color, #ffa726) 18%, transparent);
  border-left: 3px solid var(--warning-color, #ffa726);
}
.clash ha-icon {
  --mdc-icon-size: 18px; flex: 0 0 auto; margin-top: 1px;
  color: var(--warning-color, #ffa726);
}
details { border-bottom: 1px solid var(--divider-color, rgba(255,255,255,.12)); }
details > summary {
  cursor: pointer; margin: 0; padding: 12px 0; text-transform: none; letter-spacing: 0;
  font-size: 16px; font-weight: 600; color: var(--primary-text-color, #fff);
  display: flex; align-items: center; gap: 8px;
}
details > summary::-webkit-details-marker { display: none; }
details > summary ha-icon {
  --mdc-icon-size: 18px; flex: 0 0 auto;
  color: var(--secondary-text-color, #aaa);
}
.opts { padding: 0 0 10px 4px; }
.opt { display: flex; align-items: center; gap: 10px; padding: 5px 0; }
.opt__name {
  flex: 0 0 34%; min-width: 0; font-size: 14px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--primary-text-color, #fff);
}
.opt input {
  flex: 1 1 auto; min-width: 0; padding: 6px 8px; font-size: 13px;
  color: var(--primary-text-color, #fff); font-family: inherit;
  background: var(--card-background-color, rgba(255,255,255,.06));
  border: 1px solid var(--divider-color, rgba(255,255,255,.25)); border-radius: 4px;
}
.pill.tick { padding: 4px 8px; min-width: 0; }
.opt__warn {
  flex: 0 0 16px; text-align: center; font-size: 15px; cursor: help;
  color: var(--warning-color, #ffa726);
}
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pill {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 16px; cursor: pointer;
  border: 1px solid var(--divider-color, rgba(255,255,255,.25));
  background: none; color: var(--secondary-text-color, #aaa);
  font: inherit; font-size: 14px;
  -webkit-tap-highlight-color: transparent;
}
.pill.on { background: #7a51a1; border-color: #7a51a1; color: #fff; }
.pill[disabled] { opacity: .5; cursor: default; }
.pill ha-icon { --mdc-icon-size: 18px; }
.aliases { margin-top: 8px; }
.aliases input {
  width: 100%; box-sizing: border-box; padding: 8px 10px; font: inherit;
  font-size: 13px; border-radius: 8px;
  color: var(--primary-text-color, #fff);
  background: var(--card-background-color, #444);
  border: 1px solid var(--divider-color, rgba(255,255,255,.25));
}
.aliases input:disabled { opacity: .5; }
.note {
  margin-top: 10px; font-size: 12px; line-height: 1.4;
  color: var(--secondary-text-color, #aaa);
}
.stabs { display: flex; margin: 8px 0 4px; border-bottom: 2px solid var(--divider-color, rgba(255,255,255,.5)); }
.stab {
  position: relative; flex: 1 1 0; min-width: 0; display: flex; align-items: center; justify-content: center;
  min-height: 40px; padding: 10px 2px; font-size: 14px; color: var(--secondary-text-color, rgba(255,255,255,.5));
  background: none; border: 0; cursor: pointer; font-family: inherit; -webkit-tap-highlight-color: transparent;
}
.stab.on { color: var(--primary-text-color, #fff); }
.stab.on::after { content: ''; position: absolute; left: 0; right: 0; bottom: -2px; height: 2px; background: var(--primary-text-color, #fff); }
.allfav[hidden] { display: none; }
.allfav { display: flex; align-items: center; gap: 10px; padding: 12px 0 4px; font-size: 14px; color: var(--primary-text-color, #fff); }
.thumb { flex: 0 0 36px; width: 36px; height: 36px; border-radius: 4px; object-fit: cover; --mdc-icon-size: 22px;
  display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color, #aaa); }
.fav .opt__name small { display: block; font-size: 12px; color: var(--secondary-text-color, #aaa); overflow: hidden; text-overflow: ellipsis; }
.fav .auto { flex: 0 0 auto; font-size: 11px; color: var(--secondary-text-color, #aaa); }
`;

/* What the person was doing on the Voice page. Adding or renaming a voice
   activity reloads the integration, the strategy hands the card a NEW config
   (often a new card element), and the page is drawn again: without this it
   jumped back to the first tab, closed the room, and showed the old name until
   the reload finished (Michel 2026-10-26). Page state only, kept per browser tab. */
const VOICE_UI = { tab: null, open: new Set(), typed: new Map(), scroll: null, focus: null, favtab: {}, favsvc: {} };

/* 0.3.25 (Michel 2026-10-26: "Favorites are the same in all rooms ... but NOT
   in all services"): the services of a favourites answer, in its order, and
   the one shown - chosen before, else the first. */
function fav_services(r) {
  return Array.isArray(r && r.services) && r.services.length
    ? r.services.map((x) => ({ id: String(x.id), name: String(x.name || x.id) }))
    : Array.from(new Set(((r && r.items) || []).map((it) => String(it.service || "")))).map((id) => ({ id, name: id }));
}

function fav_service_shown(r, wanted) {
  const list = fav_services(r);
  return (list.find((x) => x.id === wanted) || list[0] || { id: "" }).id;
}
const FAV_TABS = [["albums", "Albums"], ["playlists", "Playlists"], ["artists", "Artists"], ["movies", "Movies"]];
const typed_key = (unit, activity) => `${unit}|${Number(activity)}`;
/* a favourite's identity on the page: voice.favorite_key's */
const fav_key_of = (play) => (play && play.kind === "movie"
  ? `movie|${Number(play.title_id || 0)}|${Number(play.copy || 0)}`
  : `music|${(play || {}).source}|${(play || {}).key}`);

class ArtiControlVoiceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._alias_raw = new Map();
  }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._alias_raw = new Map();
  }

  getCardSize() { return 14; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
  }

  /* ⚠ A VOICE ASSISTANT RESOLVES BY NAME, so a name that is not unique is a
     coin flip - compared across the whole instance, as Home Assistant composes
     names (friendly_name). The Hub's rule, unchanged. */
  _clashes() {
    const seen = new Map();
    const states = (this._hass && this._hass.states) || {};
    Object.keys(states).forEach((id) => {
      if (!VOICE_DOMAINS.has(id.split(".")[0])) return;
      const n = String(((states[id].attributes || {}).friendly_name) || "").trim().toLowerCase();
      if (!n) return;
      if (!seen.has(n)) seen.set(n, []);
      seen.get(n).push(id);
    });
    return seen;
  }

  _display_name(it) {
    const st = ((this._hass || {}).states || {})[it.entity];
    return String(((st || {}).attributes || {}).friendly_name || it.name || "");
  }

  _rows(items) {
    const clashes = this._clashes();
    return items.map((it) => {
      const shown = this._display_name(it);
      const others = (clashes.get(shown.trim().toLowerCase()) || []).filter((id) => id !== it.entity);
      /* Only a warning, never a block - the alias field below is the fix. */
      const warn = others.length ? `
        <div class="clash" title="${esc(others.join(", "))}">
          <ha-icon icon="mdi:alert-outline"></ha-icon>
          <span>Also called this: ${others.length === 1 ? esc(others[0]) : others.length + " other entities"}. Voice may pick the wrong one -
            give this one an alias below.</span>
        </div>` : "";
      return `
      <div class="item" data-e="${esc(it.entity)}">
        <div class="name">${esc(it.name)}</div>
        ${warn}
        <div class="row">
          ${ASSISTANTS.map(([key, label, icon]) => `
            <button class="pill" data-a="${key}" data-e="${esc(it.entity)}">
              <ha-icon icon="${icon}"></ha-icon>${label}
            </button>`).join("")}
        </div>
        <div class="aliases">
          <input type="text" data-e="${esc(it.entity)}" placeholder="also called - comma separated">
        </div>
      </div>`;
    }).join("");
  }

  /* One unit's activities as tick-rows, built LAZILY when its <details> opens
     (the Hub's reason: hundreds of rows nobody opened). Start-only: ticking one
     gives that activity in that room a spoken name, and an entity to answer to
     it (scene.py); there is no "turn off Netflix". */
  _unit_rows(unit) {
    const by_activity = new Map((unit.chosen || []).map((c) => [Number(c.activity), c]));
    return (unit.activities || []).map((act) => {
      const hit = by_activity.get(Number(act.id));
      const suggest = suggest_name(act.name, unit.name, (n) => this._in_use(n, hit && hit.entity, act.id, unit.entity));
      /* A name typed here wins until the reloaded config carries it. */
      const k = typed_key(unit.entity, act.id);
      if (VOICE_UI.typed.has(k) && hit && hit.name === VOICE_UI.typed.get(k)) VOICE_UI.typed.delete(k);
      const own = VOICE_UI.typed.has(k) ? VOICE_UI.typed.get(k) : (hit ? hit.name : "");
      const shown = own || suggest;
      return `
        <div class="opt" data-unit="${esc(unit.entity)}" data-activity="${Number(act.id)}">
          <button class="pill tick${hit ? " on" : ""}">
            <ha-icon icon="${hit ? "mdi:check" : "mdi:plus"}"></ha-icon>
          </button>
          <span class="opt__name">${esc(act.name)}</span>
          <input type="text" placeholder="${esc(suggest)}" value="${esc(own)}">
          <span class="opt__warn" title="">${this._name_taken(shown, hit && hit.entity) ? "⚠" : ""}</span>
        </div>${hit && hit.entity ? `
        <div class="aliases">
          <input type="text" data-e="${esc(hit.entity)}" placeholder="also called - comma separated">
        </div>` : ""}`;
    }).join("");
  }

  /* Whether a suggested name is already used: any entity Home Assistant has
     under that name (exposed or not - the suggestion must be free), or another
     voice activity on this page. Never the row's own scene. */
  _in_use(name, self_entity, activity_id, unit_entity) {
    const key = String(name || "").trim().toLowerCase();
    if (!key) return false;
    const states = (this._hass && this._hass.states) || {};
    const named = Object.keys(states).some((id) => id !== self_entity &&
      String(((states[id] || {}).attributes || {}).friendly_name || "").trim().toLowerCase() === key);
    if (named) return true;
    return (this._config.units || []).some((u) => (u.chosen || []).some((c) =>
      String(c.name || "").trim().toLowerCase() === key &&
      !(u.entity === unit_entity && Number(c.activity) === Number(activity_id))));
  }

  /* The same name check as the exposure tabs, ignoring the row's own entity. */
  _name_taken(name, self_entity) {
    const key = String(name || "").trim().toLowerCase();
    if (!key) return false;
    const owners = (this._clashes().get(key) || []).filter((id) => id !== self_entity);
    return owners.length > 0;
  }

  _units_panel() {
    const units = (this._config.units || []).filter((u) => (u.activities || []).length);
    if (!units.length) return "";
    /* Rooms apart from zones, as the Hub's tabs do. */
    const section = (kind, title) => {
      const rows = units.filter((u) => u.kind === kind);
      if (!rows.length) return "";
      return `<div class="head">${title} (${rows.length})</div>` + rows.map((u) => `
        <details data-unit="${esc(u.entity)}">
          <summary>
            <ha-icon icon="${esc(u.icon || KIND_ICON[u.kind] || "articontrol:logo")}"></ha-icon>
            <span>${esc(u.name)}</span>
          </summary>
          <div class="opts"></div>
        </details>`).join("");
    };
    return section("room", "Rooms") + section("zone", "Zones");
  }

  async _build() {
    this._built = true;
    const c = this._config;
    const groups = [
      ["rooms", "Rooms", c.rooms || []],
      ["zones", "Zones", c.zones || []],
    ].filter(([, , items]) => items.length);
    if (!groups.length) return;
    const n_chosen = (c.units || []).reduce((n, u) => n + (u.chosen || []).length, 0);
    const extra = this._units_panel() ? [["activities", "Activities", n_chosen]] : [];
    const fav_units = (c.units || []).filter((u) => u.player);

    const tabs = groups.map(([k]) => k).concat(extra.map(([k]) => k)).concat((c.units || []).some((u) => u.player) ? ["favourites"] : []);
    this._tab = tabs.includes(VOICE_UI.tab) ? VOICE_UI.tab : groups[0][0];
    this.shadowRoot.innerHTML = `
      <style>${VOICE_STYLE}</style>
      <div class="wrap">
        <div class="tabs" role="tablist">
          ${groups.map(([k, label, items]) => `<button class="tab" role="tab" data-t="${k}">${label} (${items.length})</button>`).join("")}
          ${extra.map(([k, label, n]) => `<button class="tab" role="tab" data-t="${k}">${label} (${n})</button>`).join("")}
          ${fav_units.length ? '<button class="tab" role="tab" data-t="favourites">Favourites</button>' : ""}
        </div>
        ${groups.map(([k, , items]) => `<div class="panel" data-p="${k}">${this._rows(items)}</div>`).join("")}
        ${extra.map(([k]) => `<div class="panel" data-p="${k}">${this._units_panel()}
             <p class="note">Only what you add here becomes a voice target, for example
               "Activate Netflix in the Living Room". Activities only start: to turn a
               room off, say "Turn off" and the room's name.</p>
           </div>`).join("")}
        ${fav_units.length ? `<div class="panel" data-p="favourites">${this._fav_panel(fav_units)}</div>` : ""}
        <p class="note">A voice assistant can only control what is switched on
          here. "Also called" adds the other names it answers to - the room's own
          name always works.</p>
      </div>`;
    this.shadowRoot.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => this._select(t.dataset.t)));
    this._select(this._tab);
    await this._load();
    this.shadowRoot.querySelectorAll(".item .pill").forEach((b) => b.addEventListener("click", () => this._toggle(b)));
    this.shadowRoot.querySelectorAll(".item input").forEach((i) => {
      i.addEventListener("change", () => this._save_aliases(i));
      i.addEventListener("blur", () => this._save_aliases(i));
    });
    this.shadowRoot.querySelectorAll(".allfav .pill").forEach((b) => b.addEventListener("click", () => this._fav_all(b)));
    if (this._tab === "favourites") this._fav_load();
    /* Every write here makes the integration reload, which hands this page a
       NEW card (Michel 2026-10-26: "when I click a favourite, immediately it
       closes up"; the same on a rename or an alias). What the person was
       doing - the tab, the open rooms, the typed text, the field in focus and
       the scroll - is kept in VOICE_UI and put back, on every tab. */
    this.shadowRoot.addEventListener("focusin", (e) => { this._remember(e.composedPath()[0]); });
    this.shadowRoot.querySelectorAll("details").forEach((d) => {
      d.addEventListener("toggle", () => {
        if (d.open) VOICE_UI.open.add(d.dataset.unit); else VOICE_UI.open.delete(d.dataset.unit);
        if (d.open) this._fill(d);
      });
      /* A room left open before the reload opens again, filled now - not left
         to a toggle event that may come later or not at all. */
      if (VOICE_UI.open.has(d.dataset.unit)) {
        d.open = true;
        this._fill(d);
      }
    });
    this._restore();
  }

  /* A field's address that survives a rebuild (none: not one of ours). */
  _address(node) {
    if (!node || node.tagName !== "INPUT") return null;
    if (node.dataset && node.dataset.e) return `input[data-e="${node.dataset.e}"]`;
    const fav = node.closest(".fav");
    const list = node.closest(".favlist");
    if (fav && list) return `.favlist[data-ident="${list.dataset.ident}"] .fav[data-key="${fav.dataset.key}"] input`;
    const opt = node.closest(".opt");
    if (opt) return `.opt[data-unit="${opt.dataset.unit}"][data-activity="${opt.dataset.activity}"] input`;
    return node.classList.contains("search") ? ".search" : null;
  }

  _remember(node) {
    const at = this._address(node || (this.shadowRoot && this.shadowRoot.activeElement));
    if (at) VOICE_UI.focus = at;
    try { VOICE_UI.scroll = window.scrollY || window.pageYOffset || 0; } catch (e) { /* no window */ }
  }

  /* Put back the scroll and the focus, once the rows they were in exist. */
  _restore() {
    if (VOICE_UI.focus) {
      const el = this.shadowRoot.querySelector(VOICE_UI.focus);
      if (el && this.shadowRoot.activeElement !== el) {
        el.focus();
        try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* not a text field */ }
      }
    }
    if (typeof VOICE_UI.scroll === "number") {
      try { window.scrollTo(0, VOICE_UI.scroll); } catch (e) { /* not available */ }
    }
  }

  /* One room's activity rows, built the first time it is opened. */
  _fill(d) {
    if (d.dataset.filled) return;
    const unit = (this._config.units || []).find((u) => u.entity === d.dataset.unit);
    if (!unit) return;
    d.dataset.filled = "1";
    const box = d.querySelector(".opts");
    box.innerHTML = this._unit_rows(unit);
    box.querySelectorAll(".tick").forEach((b) => b.addEventListener("click", () => this._toggle_activity(b)));
    box.querySelectorAll(".opt input").forEach((i) => {
      i.addEventListener("change", () => this._rename_activity(i));
      i.addEventListener("input", () => this._recheck_name(i));
    });
    /* An added activity's aliases - the Rooms tab's "also called" field, read
       from and saved to the scene's registry entry (Michel 2026-10-26). */
    box.querySelectorAll(".aliases input").forEach((i) => {
      i.addEventListener("change", () => this._save_aliases(i));
      i.addEventListener("blur", () => this._save_aliases(i));
      this._load_aliases(i);
    });
  }

  async _load_aliases(input) {
    try {
      const entry = await this._hass.callWS({ type: "config/entity_registry/get", entity_id: input.dataset.e });
      this._alias_raw.set(input.dataset.e, (entry || {}).aliases || []);
      if (document.activeElement !== input) input.value = aliases_shown((entry || {}).aliases);
    } catch (e) { /* the field stays empty; nothing is written unless typed */ }
  }

  /* Add or remove one voice activity. The integration persists the WHOLE list
     and reloads, which is what makes the entity appear or vanish. By the unit's
     entity_id, so it reaches the right Control when there are several. */
  async _toggle_activity(btn) {
    this._remember(null);
    const row = btn.closest(".opt");
    const on = !btn.classList.contains("on");
    const input = row.querySelector("input");
    const data = { entity_id: row.dataset.unit, activity: Number(row.dataset.activity) };
    if (on) data.name = (input.value || input.placeholder).trim();
    const k = typed_key(row.dataset.unit, row.dataset.activity);
    if (on && input.value.trim()) VOICE_UI.typed.set(k, input.value.trim());
    if (!on) VOICE_UI.typed.delete(k);
    btn.classList.toggle("on", on);
    btn.querySelector("ha-icon").setAttribute("icon", on ? "mdi:check" : "mdi:plus");
    try {
      await this._hass.callService(DOMAIN, `${on ? "add" : "remove"}_voice_activity`, data);
    } catch (e) {
      btn.classList.toggle("on", !on);   /* put it back; the write failed */
      btn.querySelector("ha-icon").setAttribute("icon", !on ? "mdi:check" : "mdi:plus");
      VOICE_UI.typed.delete(k);          /* nothing was stored, nothing to hold */
      toast(this, e);
    }
  }

  _recheck_name(input) {
    const row = input.closest(".opt");
    const warn = row.querySelector(".opt__warn");
    if (!warn) return;
    const unit = (this._config.units || []).find((u) => u.entity === row.dataset.unit);
    const chosen = ((unit && unit.chosen) || []).find((c) => Number(c.activity) === Number(row.dataset.activity));
    const taken = this._name_taken(input.value || input.placeholder, chosen && chosen.entity);
    warn.textContent = taken ? "⚠" : "";
    warn.title = taken ? "Another entity already answers to this name" : "";
  }

  /* Renaming an added one: add again with the new name - the service replaces
     the same unit + activity rather than duplicating it. */
  async _rename_activity(input) {
    this._remember(input);
    const row = input.closest(".opt");
    if (!row.querySelector(".tick").classList.contains("on")) return;
    const k = typed_key(row.dataset.unit, row.dataset.activity);
    const name = (input.value || input.placeholder).trim();
    VOICE_UI.typed.set(k, name);
    try {
      await this._hass.callService(DOMAIN, "add_voice_activity", {
        entity_id: row.dataset.unit, activity: Number(row.dataset.activity), name,
      });
    } catch (e) {
      VOICE_UI.typed.delete(k);   /* refused: the stored name stands; the field keeps what was typed, to retry */
      toast(this, e);
    }
  }

  /* ---- Favourites (Michel 2026-10-26: "favourites are favourites in ANY
     room"): ONE list per Control, one row per favourite, a voice command for
     the whole house ("Thriller") that plays in the right room by itself. ---- */

  _fav_panel(units) {
    const controls = [];
    units.forEach((u) => { if (!controls.some((c) => c.ident === u.ident)) controls.push({ ident: u.ident, entity: u.entity, name: u.control || "" }); });
    const many = controls.length > 1;
    return controls.map((c) => `
      ${many ? `<div class="head">${esc(c.name || c.ident)}</div>` : ""}
      <div class="favctl" data-ident="${esc(c.ident)}">
        <div class="stabs svtabs" role="tablist" aria-label="Service"></div>
        <div class="stabs kindtabs" role="tablist"></div>
        <div class="allfav" hidden>
          <button class="pill" data-ident="${esc(c.ident)}"><ha-icon icon="mdi:heart-multiple"></ha-icon></button>
          <span></span>
        </div>
        <div class="favlist opts" data-ident="${esc(c.ident)}" data-unit="${esc(c.entity)}"></div>
      </div>`).join("") + `
      <p class="note">Each favourite you add becomes one voice command for the whole house, for example
        "Thriller": it plays in the room where something is playing now, through its own service. Said
        while nothing or several rooms play, it asks you to say the room. With the switch on, every heart
        on the Control becomes one by itself.</p>`;
  }

  async _fav_all(btn) {
    this._remember(null);
    const on = !btn.classList.contains("on");
    btn.classList.toggle("on", on);
    try {
      await this._hass.callService(DOMAIN, "set_voice_favorites_all", { control: btn.dataset.ident, on, kind: this._fav_tab(btn.dataset.ident) });
    } catch (e) {
      btn.classList.toggle("on", !on);
      toast(this, e);
      return;
    }
    /* show what the integration STORED, not what was clicked (live 0.3.13:
       the switch said off, the entry still said on) - and the rows with it */
    const box = this.shadowRoot.querySelector(`.favlist[data-ident="${btn.dataset.ident}"]`);
    if (box) await this._fav_fill(box);
  }

  /* Read the lists the first time the tab shows (they ask the Control). */
  _fav_load() {
    this.shadowRoot.querySelectorAll(".favlist").forEach((box) => { if (!box.dataset.filled) this._fav_fill(box); });
  }

  /* The sub-tab shown for a Control: what was chosen, kept across a reload. */
  _fav_tab(ident) {
    const r = (this._fav_resp && this._fav_resp.get(ident)) || {};
    const tabs = r.tabs || FAV_TABS.map(([k]) => k);
    const want = VOICE_UI.favtab[ident];
    return tabs.includes(want) ? want : tabs[0];
  }

  /* 0.3.24 kept, then fresh: the list this browser drew last is drawn AT
     ONCE (also on the card Home Assistant rebuilds after a voice favourite is
     added, and after a reload); then the integration's answer, and - when
     that was kept - a fresh one; each redrawn only when it differs. */
  async _fav_fill(box) {
    box.dataset.filled = "1";
    const key = `fav|${box.dataset.unit}`;
    let drawn = null;
    const take = (r) => {
      const sig = JSON.stringify([(r && r.items) || [], (r && r.tabs) || [], (r && r.kinds_on) || []]);
      if (sig === drawn) return;
      drawn = sig;
      this._fav_resp = this._fav_resp || new Map();
      this._fav_resp.set(box.dataset.ident, r || {});
      this._fav_items = this._fav_items || new Map();
      this._fav_items.set(box.dataset.ident, (r && r.items) || []);
      this._fav_render(box);
    };
    const mem = kept_get(key);
    if (mem) take(mem);
    let r;
    try {
      r = await this._hass.callWS({ type: "artiled_control/favorites", entity_id: box.dataset.unit });
    } catch (e) {
      if (!mem) box.innerHTML = `<p class="note">${esc(error_text(e))}</p>`;
      return;
    }
    kept_put(key, r || {});
    take(r);
    if (r && Number(r.kept) > 0) {
      try {
        const f = await this._hass.callWS({ type: "artiled_control/favorites", entity_id: box.dataset.unit, fresh: true });
        kept_put(key, f || {});
        take(f);
      } catch (e) { /* what was kept stands */ }
    }
  }

  /* Albums, Playlists, Artists, Movies (Michel 2026-10-26; the webapp's
     Music > Favorites order), each with its count and its own switch. */
  _fav_render(box) {
    const ident = box.dataset.ident;
    const r = (this._fav_resp && this._fav_resp.get(ident)) || {};
    const items = (this._fav_items && this._fav_items.get(ident)) || [];
    const tab = this._fav_tab(ident);
    const ctl = box.closest(".favctl");
    /* first the service (Roon, Sonos, ...), then the kind within it */
    const svc = fav_service_shown(r, VOICE_UI.favsvc[ident]);
    const svstrip = ctl.querySelector(".svtabs");
    svstrip.innerHTML = fav_services(r).map((x) =>
      `<button class="stab${x.id === svc ? " on" : ""}" role="tab" data-s="${esc(x.id)}">${esc(x.name)} (${items.filter((it) => it.service === x.id).length})</button>`).join("");
    svstrip.querySelectorAll(".stab").forEach((b) => b.addEventListener("click", () => {
      VOICE_UI.favsvc[ident] = b.dataset.s;
      this._fav_render(box);
    }));
    const in_svc = (it) => !svc || it.service === svc;
    const tabs = (r.tabs || []).map((k) => FAV_TABS.find(([key]) => key === k)).filter(Boolean);
    const strip = ctl.querySelector(".kindtabs");
    strip.innerHTML = tabs.map(([k, label]) =>
      `<button class="stab${k === tab ? " on" : ""}" role="tab" data-k="${k}">${label} (${items.filter((it) => it.tab === k && in_svc(it)).length})</button>`).join("");
    strip.querySelectorAll(".stab").forEach((b) => b.addEventListener("click", () => {
      VOICE_UI.favtab[ident] = b.dataset.k;
      this._fav_render(box);
    }));
    const sw = ctl.querySelector(".allfav");
    const label = (FAV_TABS.find(([k]) => k === tab) || [tab, tab])[1].toLowerCase();
    sw.hidden = !tabs.length;
    sw.querySelector("span").textContent = `Make all ${label} available to voice`;
    sw.querySelector(".pill").classList.toggle("on", (r.kinds_on || []).includes(tab));
    const shown = items.map((it, i) => [it, i]).filter(([it]) => it.tab === tab && in_svc(it));
    if (!shown.length) {
      box.innerHTML = `<p class="note">No ${esc(label)} among the Control's favourites${(r.errors && r.errors.length) ? ` (${esc(r.errors.join("; "))})` : ""}.</p>`;
      return;
    }
    box.innerHTML = shown.map(([it, i]) => `<div class="favrow" data-i="${i}">${this._fav_row(box, it, i)}</div>`).join("");
    box.querySelectorAll(".favrow").forEach((row) => this._fav_bind(box, row));
    this._restore();
  }

  /* One favourite's row (and its aliases once it is a voice command). */
  _fav_row(box, it, i) {
    const hit = it.chosen;
    const key = fav_key_of(it.play);
    /* a name typed here wins until the list carries it (as Activities) */
    const tk = `fav|${box.dataset.ident}|${key}`;
    if (VOICE_UI.typed.has(tk) && hit && hit.name === VOICE_UI.typed.get(tk)) VOICE_UI.typed.delete(tk);
    const own = VOICE_UI.typed.has(tk) ? VOICE_UI.typed.get(tk) : (hit ? hit.name : "");
    const thumb = it.image
      ? `<img class="thumb" src="${esc(it.image)}" alt="">`
      : `<ha-icon class="thumb" icon="${it.type === "Movie" ? "mdi:filmstrip" : "mdi:music"}"></ha-icon>`;
    return `
        <div class="opt fav" data-i="${i}" data-key="${esc(key)}">
          ${thumb}
          <button class="pill tick${hit ? " on" : ""}"><ha-icon icon="${hit ? "mdi:check" : "mdi:plus"}"></ha-icon></button>
          <span class="opt__name">${esc(it.title)}<small>${esc(it.subtitle || "")}</small></span>
          <input type="text" placeholder="${esc(it.suggest || "")}" value="${esc(own)}">
          ${hit && hit.auto ? '<span class="auto">automatic</span>' : ""}
        </div>${hit && hit.entity_id ? `
        <div class="aliases">
          <input type="text" data-e="${esc(hit.entity_id)}" placeholder="also called - comma separated">
        </div>` : ""}`;
  }

  _fav_bind(box, r) {
    r.querySelectorAll(".fav .tick").forEach((b) => b.addEventListener("click", () => this._fav_toggle(box, b)));
    r.querySelectorAll(".fav input").forEach((i) => {
      i.addEventListener("input", () => {
        VOICE_UI.typed.set(`fav|${box.dataset.ident}|${i.closest(".fav").dataset.key}`, i.value);
      });
      i.addEventListener("change", () => this._fav_rename(box, i));
    });
    r.querySelectorAll(".aliases input").forEach((i) => {
      i.addEventListener("change", () => this._save_aliases(i));
      i.addEventListener("blur", () => this._save_aliases(i));
      this._load_aliases(i);
    });
  }

  /* After a write: read the list again and redraw ONLY that favourite's row,
     in place - the rest of the page is not touched (Michel 2026-10-26: no
     flash, no collapse). */
  async _fav_patch(box, key) {
    let r;
    try {
      r = await this._hass.callWS({ type: "artiled_control/favorites", entity_id: box.dataset.unit });
    } catch (e) { return; }
    kept_put(`fav|${box.dataset.unit}`, r || {});
    const items = (r && r.items) || [];
    const at = items.findIndex((it) => fav_key_of(it.play) === key);
    const row = Array.from(box.querySelectorAll(".fav")).find((n) => n.dataset.key === key);
    const wrap = row && row.closest(".favrow");
    if (at < 0 || !wrap) return;
    const mine = (this._fav_items && this._fav_items.get(box.dataset.ident)) || [];
    const i = Number(wrap.dataset.i);
    mine[i] = items[at];
    const focus = this.shadowRoot.activeElement && wrap.contains(this.shadowRoot.activeElement) ? this._address(this.shadowRoot.activeElement) : null;
    wrap.innerHTML = this._fav_row(box, items[at], i);
    this._fav_bind(box, wrap);
    if (focus) { const el = this.shadowRoot.querySelector(focus); if (el) el.focus(); }
  }

  _fav_item(box, node) {
    const row = node.closest(".fav");
    return [row, ((this._fav_items && this._fav_items.get(box.dataset.ident)) || [])[Number(row.dataset.i)]];
  }

  async _fav_toggle(box, btn) {
    const [row, it] = this._fav_item(box, btn);
    if (!it) return;
    this._remember(null);
    const on = !btn.classList.contains("on");
    const input = row.querySelector("input");
    btn.classList.toggle("on", on);
    btn.querySelector("ha-icon").setAttribute("icon", on ? "mdi:check" : "mdi:plus");
    try {
      if (on) {
        await this._hass.callService(DOMAIN, "add_voice_favorite", {
          control: box.dataset.ident, play: it.play, title: it.title, type: it.type,
          name: (input.value || input.placeholder).trim() });
      } else {
        await this._hass.callService(DOMAIN, "remove_voice_favorite", { control: box.dataset.ident, play: it.play });
      }
    } catch (e) {
      btn.classList.toggle("on", !on);
      btn.querySelector("ha-icon").setAttribute("icon", !on ? "mdi:check" : "mdi:plus");
      toast(this, e);
      return;
    }
    await this._fav_patch(box, row.dataset.key);
  }

  async _fav_rename(box, input) {
    const [row, it] = this._fav_item(box, input);
    if (!it || !row.querySelector(".tick").classList.contains("on")) return;
    this._remember(input);
    try {
      await this._hass.callService(DOMAIN, "add_voice_favorite", {
        control: box.dataset.ident, play: it.play, title: it.title, type: it.type,
        name: (input.value || input.placeholder).trim() });
    } catch (e) { toast(this, e); return; }
    await this._fav_patch(box, row.dataset.key);
  }

  _select(which) {
    this._tab = which;
    VOICE_UI.tab = which;
    if (which === "favourites" && this._built) this._fav_load();
    this.shadowRoot.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.t === which));
    this.shadowRoot.querySelectorAll(".panel").forEach((p) => p.classList.toggle("on", p.dataset.p === which));
  }

  /* ⚠ One registry read per entity: the registry entry is unambiguous about the
     exposure and carries the aliases (the Hub's reasoning). */
  async _load() {
    const all = Array.from(this.shadowRoot.querySelectorAll(".item")).map((el) => el.dataset.e);
    await Promise.all(all.map(async (id) => {
      let entry = null;
      try {
        entry = await this._hass.callWS({ type: "config/entity_registry/get", entity_id: id });
      } catch (e) { return; }
      const options = (entry || {}).options || {};
      ASSISTANTS.forEach(([key]) => {
        const set = options[key];
        /* No explicit setting -> Home Assistant's own default, which exposes a
           media_player to Assist only. Rarely reached: the integration writes
           an explicit setting for everything it creates. */
        const on = set && typeof set.should_expose === "boolean"
          ? set.should_expose
          : (key === "conversation" && id.startsWith("media_player."));
        const pill = this.shadowRoot.querySelector(`.pill[data-a="${key}"][data-e="${id}"]`);
        if (pill) pill.classList.toggle("on", !!on);
      });
      const input = this.shadowRoot.querySelector(`input[data-e="${id}"]`);
      /* The null in aliases is HA's computed own name: not shown, kept on save
         (aliases_to_save). An empty field keeps its placeholder, never written. */
      this._alias_raw.set(id, (entry || {}).aliases || []);
      if (input) input.value = aliases_shown((entry || {}).aliases);
    }));
    await this._prune_clashes();
  }

  /* A clash only matters with something an assistant can actually be asked for
     (Michel 2026-10-26: all three rooms warned about ArtiLED Hub lights that no
     assistant has). One list of every exposure; when it cannot be read, every
     warning stays - better a needless warning than a missing one. */
  async _prune_clashes() {
    let list = null;
    try {
      list = await this._hass.callWS({ type: "homeassistant/expose_entity/list" });
    } catch (e) { return; }
    const exposed = (list || {}).exposed_entities;
    if (!exposed || typeof exposed !== "object") return;
    const voiced = (id) => Object.values(exposed[id] || {}).some((v) => v === true);
    this.shadowRoot.querySelectorAll(".clash").forEach((el) => {
      const others = String(el.getAttribute("title") || "").split(", ").filter(Boolean).filter(voiced);
      if (!others.length) { el.remove(); return; }
      el.setAttribute("title", others.join(", "));
      const span = el.querySelector("span");
      if (span) {
        span.textContent = `Also called this: ${others.length === 1 ? others[0] : others.length + " other entities"}. ` +
          "Voice may pick the wrong one - give this one an alias below.";
      }
    });
  }

  async _toggle(pill) {
    const on = !pill.classList.contains("on");
    pill.classList.toggle("on", on);
    try {
      await this._hass.callWS({
        type: "homeassistant/expose_entity",
        assistants: [pill.dataset.a],
        entity_ids: [pill.dataset.e],
        should_expose: on,
      });
    } catch (e) {
      pill.classList.toggle("on", !on);   /* put it back; the write failed */
      toast(this, e);
    }
  }

  async _save_aliases(input) {
    this._remember(input);
    const aliases = aliases_to_save(input.value, this._alias_raw.get(input.dataset.e));
    const key = input.dataset.e + "|" + aliases.join("|");
    if (this._saved === key) return;   /* change AND blur both fire */
    this._saved = key;
    try {
      await this._hass.callWS({ type: "config/entity_registry/update", entity_id: input.dataset.e, aliases });
    } catch (e) { toast(this, e); /* the field stays as typed, to retry */ }
  }
}

/* ------------------------------------------------------------------ *
 * The dashboard strategy - the Hub's ArtiLedDashboardStrategy, for units.
 * ------------------------------------------------------------------ */

const VOICE_UID = /^(.+)_voice_activity_(room|zone)_(\d+)_(\d+)$/;

/* The units, grouped per Control, from the registries and the states. Pure
   over its inputs (no hass calls), so tests/test_frontend.js feeds it fixtures.
   ⚠ Matched by DEVICE IDENTIFIER, never by entity_id or name. Only units with a
   media_player appear: the integration creates one only for an OFFERED unit
   (Michel's empty-room rule, N2), so this list is the offered units. */
function collect(entities, devices, states) {
  const ours = (entities || []).filter((e) => e.platform === DOMAIN && !e.disabled_by);
  /* Voice activities by the unit they start: their scenes are on NO device
     (Home Assistant 2026.10 would prefix a device's name to theirs), so they
     are matched by unique_id - voice_unique_id() in entity_ids.py,
     "<ident>_voice_activity_<kind>_<unit id>_<activity id>". */
  const voice_by_unit = new Map();
  ours.forEach((e) => {
    if (!e.entity_id.startsWith("scene.")) return;
    const m = VOICE_UID.exec(String(e.unique_id || ""));
    if (!m) return;
    const key = `${m[1]}|${m[2]}|${Number(m[3])}`;
    if (!voice_by_unit.has(key)) voice_by_unit.set(key, []);
    voice_by_unit.get(key).push({ entity: e, activity: Number(m[4]) });
  });
  const by_device = new Map();
  ours.forEach((e) => {
    if (!e.device_id) return;
    if (!by_device.has(e.device_id)) by_device.set(e.device_id, []);
    by_device.get(e.device_id).push(e);
  });
  const controls = new Map();   /* ident -> {ident, name, units: []} */
  const control_of = (ident) => {
    if (!controls.has(ident)) controls.set(ident, { ident, name: "", units: [] });
    return controls.get(ident);
  };
  (devices || []).forEach((d) => {
    const what = classify(d);
    if (!what) return;
    if (what.kind === "control") {
      control_of(what.ident).name = d.name_by_user || d.name || "ArtiCONTROL";
      return;
    }
    const mine = by_device.get(d.id) || [];
    const player = mine.find((e) => e.entity_id.startsWith("media_player."));
    if (!player) return;
    const st = (states || {})[player.entity_id];
    const a = attrs(st);
    control_of(what.ident).units.push({
      ...what,
      device_id: d.id,
      entity: player.entity_id,
      name: d.name_by_user || d.name || a.friendly_name || player.entity_id,
      order: typeof a.unit_order === "number" ? a.unit_order : 1e6,
      unit_icon: String(a.unit_icon || ""),
      /* S3a: the room plays something a play can name (it offers search) -
         what the Voice page's Favourites tab lists */
      player: (Number(a.supported_features || 0) & 4194304) !== 0,
      plays_music: !!a.plays_music,
      plays_movies: !!a.plays_movies,
      activities: (a.activities || []).map((r) => ({ id: Number(r.id), name: String(r.name || ""), icon: String(r.icon || "") })),
      /* Voice activities already chosen for this unit, by unique_id. The name
         is what Home Assistant shows (the spoken name); never parsed back out
         of the user-named entity_id. */
      chosen: (voice_by_unit.get(`${what.ident}|${what.kind}|${what.id}`) || [])
        .map(({ entity: e, activity }) => {
          const sa = attrs((states || {})[e.entity_id]);
          return { activity, name: String(sa.friendly_name || e.name || e.original_name || ""), entity: e.entity_id };
        })
        .filter((c) => c.activity > 0),
    });
  });
  const list = Array.from(controls.values()).filter((c) => c.units.length);
  list.forEach((c) => {
    c.units.sort((x, y) => (x.order - y.order) || String(x.name).localeCompare(String(y.name)));
    if (!c.name) c.name = "ArtiCONTROL";
  });
  list.sort((x, y) => String(x.name).localeCompare(String(y.name)) || x.ident.localeCompare(y.ident));
  return list;
}

const OVERVIEW_COLUMNS = 3;

function build_views(controls, opts) {
  const path = opts.dashboard_path;
  const admin = !!opts.admin;
  const have = opts.have || {};
  const many = controls.length > 1;
  const views = [];

  /* Home: the rooms and zones in Control order, the webapp's Home
     (app/index.php has no section headings); one heading per Control only when
     there is more than one (plan 3.3). Since 0.3.19 ONE home card holding the
     lists, in the webapp's layouts (Grid by default), and NO activity buttons:
     those are on the room's own page (Michel 2026-10-26). `layout:` in the
     strategy config fixes the layout for this dashboard (no switch). */
  const fixed = LAYOUTS.includes(opts.layout) ? opts.layout : null;
  const groups = controls.map((c) => ({
    heading: many ? c.name : "",
    tiles: c.units.map((u) => ({
      entity: u.entity,
      navigate: `/${path}/${unit_path(u, many)}`,
      activities: false,
    })),
  }));
  views.push({
    title: "Home", path: "home", icon: "mdi:home", type: "sections",
    max_columns: OVERVIEW_COLUMNS,
    sections: [{
      type: "grid",
      column_span: OVERVIEW_COLUMNS,
      cards: [{
        type: "custom:artiled-control-home-card",
        groups,
        ...(fixed ? { layout: fixed } : {}),
        grid_options: { columns: "full" },
      }],
    }],
  });

  /* A page per unit - the webapp's unit page (app/unit/index.php): the unit's
     own card (app_render_tiles([$unit]): switch and volume, no activity
     buttons - the player below lists them as rows), then the player. */
  controls.forEach((c) => c.units.forEach((u) => {
    const page = unit_path(u, many);
    const browse = {};
    if (u.plays_music) browse.music = `/${path}/${page}-music`;
    if (u.plays_movies) browse.movies = `/${path}/${page}-movies`;
    views.push({
      title: u.name,
      path: unit_path(u, many),
      icon: KIND_ICON[u.kind],
      type: "sections",
      max_columns: 2,
      subview: true,
      back_path: `/${path}/home`,
      sections: [{ type: "grid", cards: [
        { type: "custom:artiled-control-unit-card", entity: u.entity, activities: false },
        { type: "custom:artiled-control-player-card", entity: u.entity, browse },
        /* 0.3.25: the house's favourites, per service, played in THIS room */
        ...(u.plays_music || u.plays_movies ? [{ type: "custom:artiled-control-favorites-card", entity: u.entity }] : []),
      ] }],
    });
    /* Browse Music / Browse Movies: a page of their own under the unit's, with
       Home Assistant's back arrow (Michel 2026-10-26) */
    [["music", "Browse Music", "mdi:music"], ["movies", "Browse Movies", "mdi:filmstrip"]].forEach(([kind, title, icon]) => {
      if (!browse[kind]) return;
      views.push({
        title: `${title} - ${u.name}`, path: `${page}-${kind}`, icon, type: "panel", subview: true,
        back_path: `/${path}/${page}`,
        cards: [{ type: "custom:artiled-control-browse-card", entity: u.entity, kind }],
      });
    });
  }));

  /* The Voice page. ⚠ ADMIN ONLY, and omitted rather than shown disabled: the
     websocket commands behind it are @require_admin (the Hub's rule). */
  if (admin) {
    const all = controls.flatMap((c) => c.units.map((u) => ({ ...u, control: c.name })));
    const label = (u) => (many ? `${u.name} (${u.control})` : u.name);
    /* The Control's own icon for the unit, as the Hub's voice page uses the
       Hub's; the kind's MDI icon when the Control's was not served. */
    const unit_icon = (u) => icon_name(u.unit_icon, have, KIND_ICON[u.kind]);
    const voice_card = {
      type: "custom:artiled-control-voice-card",
      rooms: all.filter((u) => u.kind === "room").map((u) => ({ entity: u.entity, name: label(u) })),
      zones: all.filter((u) => u.kind === "zone").map((u) => ({ entity: u.entity, name: label(u) })),
      units: all.map((u) => ({
        kind: u.kind, entity: u.entity, name: u.name, icon: unit_icon(u), ident: u.ident, player: !!u.player,
        activities: u.activities.map((r) => ({ id: r.id, name: r.name })),
        chosen: u.chosen,
      })),
    };
    if (voice_card.rooms.length || voice_card.zones.length) {
      views.push({
        title: "Voice", path: "voice", icon: "mdi:microphone-settings", type: "sections",
        max_columns: 2, sections: [{ type: "grid", cards: [voice_card] }],
      });
    }
  }
  return views;
}

function no_units_view(any_entity) {
  return {
    views: [{
      title: "ArtiCONTROL",
      cards: [{
        type: "markdown",
        content: any_entity
          ? "No room or zone of your Control has an activity yet. Add an activity to a room " +
            "on the Control and it appears here by itself within a minute."
          : "No ArtiCONTROL rooms found. Add the integration under " +
            "**Settings → Devices & services → Add integration → ArtiCONTROL**.",
      }],
    }],
  };
}

class ArtiControlDashboardStrategy {
  static async generate(config, hass) {
    const [entities, devices] = await Promise.all([
      hass.callWS({ type: "config/entity_registry/list" }),
      hass.callWS({ type: "config/device_registry/list" }),
    ]);
    const controls = collect(entities, devices, hass.states);
    if (!controls.length) {
      return no_units_view((entities || []).some((e) => e.platform === DOMAIN));
    }
    /* Subview links need the dashboard's own url_path, which a strategy is not
       told: from the URL being rendered; `dashboard_path` overrides. */
    const dashboard_path = (config && config.dashboard_path) ||
      window.location.pathname.split("/").filter(Boolean)[0] || "artiled-control";
    const have = await fa_icons();
    return {
      views: build_views(controls, {
        dashboard_path, admin: !!(hass.user && hass.user.is_admin), have,
        layout: config && config.layout,
      }),
    };
  }
}

/* ------------------------------------------------------------------ *
 * Registration - the Hub's define_once + both-registries watcher.
 * ------------------------------------------------------------------ */

/* ⚠ DO NOT rely on a define() at module-evaluation time alone: this module can
 * load before Home Assistant installs scoped-custom-element-registry, which
 * REPLACES window.customElements; elements defined before that sit in the
 * native registry nobody looks at, and the dashboard only says "Timeout waiting
 * for strategy element ll-strategy-dashboard-artiled-control". So define on the
 * native registry now AND on the live one once it is swapped (the Hub's fix). */
const NATIVE_REGISTRY = customElements;
const REGISTRY_WATCH_MS = 30000;
const REGISTRY_POLL_MS = 50;

function define_once(registry, tag, ctor) {
  try {
    if (!registry.get(tag)) registry.define(tag, ctor);
  } catch (e) {
    /* Already defined by a second copy of this module - not a problem. */
  }
}

const ELEMENTS = [
  ["artiled-control-unit-card", ArtiControlUnitCard],
  ["artiled-control-home-card", ArtiControlHomeCard],
  ["artiled-control-player-card", ArtiControlPlayerCard],
  ["artiled-control-browse-card", ArtiControlBrowseCard],
  ["artiled-control-favorites-card", ArtiControlFavoritesCard],
  ["artiled-control-voice-card", ArtiControlVoiceCard],
  ["ll-strategy-dashboard-artiled-control", ArtiControlDashboardStrategy],
];

function define_all(registry) {
  ELEMENTS.forEach(([tag, ctor]) => define_once(registry, tag, ctor));
}

register_iconset();
define_all(NATIVE_REGISTRY);

const watch_started = Date.now();
const watcher = setInterval(() => {
  if (customElements !== NATIVE_REGISTRY) {
    define_all(customElements);
    clearInterval(watcher);
  } else if (Date.now() - watch_started > REGISTRY_WATCH_MS) {
    clearInterval(watcher);
  }
}, REGISTRY_POLL_MS);

window.customCards = window.customCards || [];
[
  ["artiled-control-unit-card", "ArtiCONTROL room", "A Control room or zone in the webapp's own styling, with its activities"],
  ["artiled-control-player-card", "ArtiCONTROL player", "Now Playing, activities, transport and volume of a Control room or zone"],
  ["artiled-control-browse-card", "ArtiCONTROL browse", "Browse Music or Browse Movies of a Control room or zone: favourites and search"],
].forEach(([type, name, description]) => {
  if (!window.customCards.some((c) => c.type === type)) {
    window.customCards.push({ type, name, description });
  }
});
