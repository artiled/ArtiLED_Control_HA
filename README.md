# ArtiCONTROL — Home Assistant integration

Native local integration for the ArtiLED Control. Home Assistant starts the
Control's **activities per room**, turns rooms off, and shows what each room is
doing, over the Control's local HTTP API. The Control remains the single source of
truth — this integration never invents state.

Proprietary ArtiLED source — published so it can be **installed and updated**,
not to invite forks. See Copyright below.

## Naming

| | |
|---|---|
| shown to the user | **ArtiCONTROL** — Integrations page, HACS listing, service picker |
| domain | **`artiled_control`** |
| repository | `ArtiLED_Control_HA` |

The display name and the domain differ on purpose, exactly as the Hub
integration does (**ArtiLED** shown, `artiled_hub` underneath).

⚠ **Never rename the domain.** Home Assistant cannot migrate a config entry
across domains — renaming one means delete and re-add, and every entity loses
its id, area, aliases and voice exposure.

## Why this is a separate integration from the Hub one

A single `artiled` domain would rename every existing Hub entity_id and discard
the areas, aliases, voice exposure and history already established there. Two
integrations means the dashboard frontend must be namespaced (`artiled-control-*`
cards, `ll-strategy-dashboard-artiled-control`, `/artiled_control_frontend`,
its own `articontrol` iconset) — a second `define()` of a tag fails silently.

## Features

- **Config flow** — Settings → Devices & services → Add integration →
  ArtiCONTROL. Enter the host and the **username and password you use for the
  Control's web app**; the integration fetches the API token itself via
  `/api/login` and stores only that — the password is never persisted.
  - The Control's **ident** is the unique id. A Control without one (or with
    the all-zeros stand-in) is refused: "This Control has no identity yet.
    Finish its setup first." The address is **never** used as a stand-in.
  - A Control too old to report `on_activity`, `off_activity` and
    `starting_activity` is refused: "Update the Control first."
  - **Let Assist control all rooms** — one tick, off by default. See Voice.
  - **Create the ArtiCONTROL dashboard** — one tick, on by default. See
    Dashboard.
  - **Re-auth** when the Control rejects the token, **Reconfigure** for a new
    address (same Control only), and **Configure** to let Assist control all
    rooms later, or to create the dashboard again.
- **One `media_player` per unit** — every zone, plus every room without zones,
  exactly the tiles of the Control's web app.
  - **On** starts the unit's On activity, **Off** starts its **Off activity** —
    always, whatever runs. Never `/activity stop`: that skips the Off
    activity's own steps (TV and amplifier off). A unit without an On or Off
    activity refuses with the web app's own words ("No Off activity for …").
  - **Source** = the activity. The source list is the unit's activities minus
    its Off activity; two activities with one name are told apart with " (2)".
  - **"Starting…"**: while an activity starts, the player is on, shows that
    activity as its source, and carries `starting: true`. The Off activity
    starting shows off. The old activity's title and picture are not shown over
    a start.
  - **Volume, mute, transport** through the running activity's routing, as the
    web app's slider and remote. Only the transport keys the activity uses are
    offered. ⚠ A volume level the Control does not know is shown as unknown,
    never as 50 %.
  - **Now Playing**: title, artist, album, duration, position and the cover.
    The cover is fetched by Home Assistant, server-side; the browser only sees
    Home Assistant's own proxy, never the Control's token.
  - Names: a zone's own name when it is unique on that Control, otherwise
    "<Room> <Zone>" ("Living Room Main"). Anything else: Home Assistant aliases.
  - Entity ids are `media_player.<name>`, set **once** when a unit first
    appears; rename one yourself and your choice stands.
- **Voice activities** — `scene` entities you create for what you actually say:
  "activate Netflix in the Living Room". ⚠ **Start only**: an activity is
  started; a room is turned off. There is no "turn off Netflix".
  - Created on the dashboard's Voice page, or with the `add_voice_activity`
    service. Never generated in bulk.
  - Bound to the room **and** the activity id: renaming the activity on the
    Control keeps it working; moving, disabling or deleting it makes the voice
    activity unavailable — it is never silently re-bound to something else.
  - Spoken names are unique across every Control in the house.
- **Only rooms with something to start** — a room or zone whose only activity
  is its Off activity is not created and not offered anywhere. Give it an
  activity on the Control and it appears in Home Assistant within about a
  minute, on its own.
- **Follows the Control's inventory** — a unit that loses its last activity is
  unavailable at once, and removed after an hour (un-exposed from every voice
  assistant first). Back within the hour, it returns complete: name, aliases,
  voice activities. A failed read is never taken for an empty Control.
- **Services** — `artiled_control.start_activity` (a room and an activity, by
  name or id), `artiled_control.add_voice_activity`,
  `artiled_control.remove_voice_activity`. With more than one Control, give
  `control`; a call that does not say which is refused, never guessed.
- **Diagnostics** — Control version sensor, and a diagnostics download with the
  token redacted everywhere.

## Dashboard

Setup adds an **ArtiCONTROL** dashboard to the sidebar (`/artiled-control`),
once. Delete it and it stays deleted; Configure → *Create the ArtiCONTROL
dashboard again* puts it back. One dashboard covers every Control in the house,
and it is left in place when the integration is removed.

- **Home** — every room and zone that has something to start, in the Control's
  own order, and nothing else: the web app's tile (name, status, "Starting…"
  with moving dots, the cover behind it while something plays, the switch).
  The activities are on the room's own page. Tiles come in the web app's
  layouts — **Grid** (the default: 2 across on a phone, 4 on a tablet, 6 in
  landscape), **Square**, **List** and **Rows** (with the volume slider) —
  chosen with the switch at the top of Home and remembered per browser, as in
  the web app — apart from the ArtiLED (Hub) dashboard's choice: each
  dashboard keeps its own. `layout: grid|square|list|rows` in the dashboard's strategy
  config fixes one for every browser and hides the switch.
- **A page per room** (tap the tile) — the room's tile, then the web app's remote, in its order: only
  the transport keys the running activity uses, mute, the volume row, then Now
  Playing with the cover and progress; below it the activities as rows (the
  running one lit, a starting one "Starting…") and Off. The number over the
  volume knob is the device's own ("-30.0 dB", "42"), never a percentage; while
  the level is not known the slider is greyed and the speakers do nothing, as
  in the web app.
- **Voice** (administrators only) — per room: expose it to Assist, Alexa or
  Google, give it aliases, and give its activities spoken names ("Netflix in
  the Living Room"), each one a voice activity. Name clashes with anything else
  in Home Assistant are flagged.

Everything is served by the integration itself: no HACS frontend, no manual
resource, no third-party card. It lives beside the ArtiLED (Hub) dashboard
without touching it: its own cards (`artiled-control-*`), strategy, static
paths (`/artiled_control_frontend`, `/artiled_control_icons`) and iconset
(`articontrol`).

**Icons** — the Control stores Font Awesome **Pro** icons. Their paths are read
from the customer's own Control at runtime (its `/fontawesome/` web root, no
token) and cached in the Home Assistant config directory
(`artiled_control_icons/`). They are never part of this package: the Pro licence
does not allow shipping them. An icon the Control cannot serve shows the ArtiLED
mark, or Home Assistant's own icon on the player's keys.

## Voice

- **Nothing is exposed to any voice assistant until you choose it** — the Hub
  rule. The one exception is the setup tick **Let Assist control all rooms**:
  it exposes every room to Home Assistant's own Assist, once. Alexa and Google
  are always chosen per room. Un-expose a room later and it stays un-exposed.
- **A voice activity is always heard** — adding one is asking for it, so it
  is exposed to Assist and to every cloud assistant switched on in Home
  Assistant Cloud (Alexa, Google), whatever the room's own exposure. Once:
  change it afterwards and your choice stands. Its name is
  exactly the spoken name ("Netflix in the Living Room",
  `scene.netflix_in_the_living_room`): it belongs to no device, because Home
  Assistant would put the room's name in front of it. Removed and added again,
  it comes back clean under that name.
- **The long form is an alias** — "Sonos in the Kitchen" also answers to
  "Listen to Sonos in the Kitchen" (shown and editable under it on the Voice
  page). Assist and Google use aliases; Alexa does not. When two activities
  would share a short name, the second keeps its verb.
- Assist out of the box: "turn on / off the living room", "pause", "next",
  "set the living room volume to 30 %", "mute the living room".
- Starting an activity by voice: a voice activity —
  "activate Netflix in the Living Room" (Assist, Google),
  "Alexa, turn on Netflix in the Living Room".
- **Favourites by voice** (the Voice page's **Favourites** tab, one list for
  the whole Control): tick a heart of the Control — an album, a playlist, a
  movie — and it becomes ONE voice command for the whole house: "Thriller"
  ("Alexa, turn on Thriller"). It plays in the room where something is
  playing when you say it — only when exactly one room is playing. When no
  room or several rooms are playing it does nothing and says to name the
  room (say "play Thriller in the Kitchen" to the room, or tap it on the
  room's page). Exposed like a voice activity; rename it and give it other
  names there. Two favourites with one title are told apart by their kind
  ("Thriller playlist"), then their service ("Thriller on Roon").
- **The favourites are the same in every room, but not in every service:**
  the list is grouped per service — Roon, Sonos, Spotify, HEOS, Zidoo,
  Movies — and a record that is a favourite on Roon and on Sonos is in both,
  each played through its own service (one service's album is never looked
  up on another). Every room's page shows the list under its player; a tap
  plays it in THAT room: on what is running when that is the favourite's
  service, otherwise the room switches to its activity for that service. A
  favourite the room has no activity for is shown dimmed; a tap says so and
  sends nothing.
- Within a service the tab is split like the web app's Music favourites: **Albums,
  Playlists** (a Sonos playlist, radio or song favourite sits here)**,
  Artists, Movies**, each with its count; Playlists and Artists show when
  there are any, or when the Control can play them (API 2.02).
- **Make all … available to voice** (one switch at the top of each of those
  tabs; Configure switches them all; off by default — an earlier "on" means
  Albums and Movies): every heart of that kind on the Control becomes one
  by itself and keeps following the Control — a new heart appears within a
  minute, a heart removed takes its command with it. Those you added by hand
  are never removed by it, one you remove is not added back, and a command you
  renamed or took away from an assistant keeps your change. At most 200 per
  Control (voice assistants handle long lists badly); Repairs says how many
  were left out. Switching it off removes the automatic ones.
- **One heart = one record:** editions of one album are one favourite —
  "Antarctica", "Antarctica (Remastered)" and "Antarctica [Original Motion
  Picture Soundtrack]" are "Antarctica" (the Control's own edition rule); the
  scene is named by the plain title, and scenes made per edition by 0.3.13
  are merged into it (their names become its other names). Adding, renaming
  or removing one changes only that one — the Voice page does not empty and
  refill.
- Favourites made per room by 0.3.9-0.3.11 become house-wide by themselves:
  the first room's keeps its entity, aliases and assistants, any other room's
  copy is removed.

## Search and play music and films

A room whose activities play music (Roon, Sonos, Spotify, HEOS, Zidoo) or
films (Kaleidescape, Zidoo) can be searched and played from Home Assistant —
by voice, from the media window's **Browse media**, and by an AI assistant
(Home Assistant's own media search and play tools).

- **What to say (Assist):** "play Thriller in the Kitchen",
  "play Alien in the Cinema", "play Michael Jackson Thriller in the Kitchen".
  Every word you say must be in the title and artist together (a film: title,
  director, cast, genre, year), in any order; "Jackson's" and "Jacksons" find
  Jackson. The best match plays: the exact title first, then a title made
  only of words you said.
- **What plays it:** when the room already runs an activity whose player can
  play it, it plays **there** — no other source is started. Only when the
  running activity cannot, the activity whose player owns it is started with
  it (the first one in the Control's order). Assist answers with the title
  only ("Playing Thriller"); which activity played it is in the room's `play`
  attribute, and Browse media groups the favourites per activity. The
  Control checks again that the item belongs to that player; if not, nothing
  is started and you hear why.
- **Started is not playing.** The Control answers what its player said
  ("sent", "plays when the start finishes" ...). That word is shown as the
  room's `play` attribute; a play the Control says it did not play is an
  error, never a silent success.
- **What can be found:**
  - Roon and Spotify: the Control's own album list (the web app's Albums
    search) while that activity runs or the room is idle or off; otherwise
    your favourites (hearts). Searching a room that is off needs a Control
    that supports it — ArtiCONTROL asks once per Control version and uses the
    favourites when it does not.
  - Sonos and HEOS: your favourites — the only things a start can name.
  - Zidoo music: your hearted albums only. A Zidoo's own search answers in
    its own addresses, which a start cannot play, so heart an album on the
    Control to reach it by voice.
  - Films: the film library (title, director, cast, genre — as the web app's
    Find Movies) for the films that play on that activity's player while it
    runs or the room is idle or off (as above); otherwise your hearted films.
  - Only items with artwork are shown.
- **On the ArtiCONTROL dashboard:** a room's player card has the web app
  remote's round shortcut icons: **Browse** (folder) opens the room's Browse
  page for what runs (a film activity's: Browse Movies), **Search**
  (magnifying glass) the same page with its search field ready. The page is
  a page of its own with a back arrow. **Browse is the web app's Find Music
  (Find Movies) for the running activity's player**: its tabs in its order
  and names (Roon: Recent, Favorites, Playlists, Albums, Artists, Composers,
  and TIDAL / Qobuz when Roon has them; Spotify: Recent, Albums, Playlists,
  Songs; Sonos and HEOS: the kinds your favourites hold and the services the
  web app shows; the Zidoo: Recent, Favorites, Albums, Artists; movies:
  Recent, Favorites, Movies, Directors, Actors, Genres), each paged as the
  web app pages it. An album, playlist, artist or composer opens the web
  app's own page with a Back step: the cover with the title, the artist and
  the info line (year · label · tracks · minutes), something to read, the
  player's own play options in one row (Roon: Play Now, Add Next, Queue,
  Start Radio; a service: Play, Shuffle), then what is inside it — the
  tracks as a numbered list with the record's cover and a play button each,
  an artist's records one tile per record (a remaster folded into its
  album), a composer's works with the cover of their record. A record's or
  playlist's tile has a round play button on its cover: one tap plays it,
  a tap on the tile opens its page. With nothing running, a play first
  starts the room's music activity ("Starting…") and then plays. A person
  without a picture is not shown; anything else without a picture shows
  four covers from inside it or its letter. The search field searches
  everything. (Covers the web app's phone size, as
  many columns as the screen holds; one entry per album, a search shows its
  results instead of the favourites): the favourites as cover tiles (the
  running activity's first, other sources below), a search 0.4 s after you
  stop typing, a hundred tiles at a time with more as you scroll, covers
  loaded as they come into view; each tile says which activity plays it and
  a tap plays it. The same browse and search as Home Assistant's media window,
  through Home Assistant — the browser never talks to the Control.
- **Speed:** the favourites, a Spotify album list and the film library are
  read from the Control once a minute (and again when the running activity
  changes), not on every look or search; a cover is checked once a week (a
  HEAD, not a download), also across restarts of the integration.
  The dashboard's Browse pages work **kept, then fresh**, as the web app does:
  what was read in the last minute is shown at once without asking the
  Control; something older (up to 6 hours) is shown at once too, and the page
  asks again in the background and redraws only what changed. A search is kept
  2 minutes (30 minutes kept-then-fresh) — a Roon or Sonos search takes the
  Control several seconds. A play is never kept and makes Recent read again.
  Covers on tiles and rows are made 320 px once (a Control cover is ~140 KB at
  full size, ~27 KB at 320 px) and served from memory for a day; the browser
  keeps them an hour. A room's page reads Browse Music's first answers in the
  background, so Browse opens on them. The Voice page's Favourites list is
  kept the same way (each player's favourites read side by side, kept per
  Control so adding a voice favourite does not make the next look slow), and
  read once in the background when the integration starts. The browser keeps
  the last answers too (for the browser session), so a rebuilt or reloaded
  dashboard draws Browse and Favourites at once and then updates what changed.
  A tapped tile keeps its place with "Starting…" while the Control takes the play.
- **Browse media** follows what the room is doing. While an activity runs (or
  starts), the top level IS its content — its favourites and every other
  favourite its player can play — and the room's other sources sit in one
  **Other sources** folder. While the room is off, it lists the activities
  that play something, each opening its favourites (straight away when only
  one has any). Every album or film shows once: the running activity's copy,
  else the first activity's; another source's copy of it (Thriller on the
  Zidoo as well as on Roon) only under Other sources, never as an extra search
  result. A cover that no longer loads (a dead Deezer address in a Sonos or
  HEOS favourite) is never shown as an empty tile — that item is left out. Favourites are read
  from the Control every time you look: a heart added or removed there shows
  on the next look, without a reload.
- **Alexa and Google:** "Alexa, play …" goes to Alexa's own music services,
  not to the Control. For a favourite by Alexa or Google, make a voice
  activity whose activity plays it ("Alexa, turn on Jazz in the Kitchen").

## All off

Each Control gets one **All off** button on the Control's own device —
`button.<control name>_all_off`, shown as "<Control> All off", never with
the device's area in front (the Control's version sensor likewise). It does
what the web app's house switch does when you turn it off: it reads the rooms
again and starts the **Off activity** of every room and zone that has one —
never a plain stop, so TVs and amplifiers really go off. Hidden rooms are left
out. If a room does not switch off, the others still do and the message names
it.

Like everything ArtiCONTROL creates, it is **not exposed to any voice
assistant** until you expose it (Settings → Voice assistants). Exposed to
Assist: "press <Control> All off" (or give it an alias such as "everything
off"). Alexa and Google show a button as a scene: "Alexa, turn on <Control>
All off".

## Seek, shuffle and repeat

While a room's running activity plays a music service (Roon, Sonos, Spotify,
HEOS, Zidoo), Home Assistant's media window and the ArtiCONTROL player card
offer what the web app's remote offers: **seek** on the progress bar, and
**shuffle** and **repeat** between its times (off dim, on bright; repeat one
shows a small 1). They show what the **player** says — read when the activity
starts and every 15 seconds, and again after every change — never what was
asked. When the player has not said (or cannot: a radio stream, HEOS and the
Zidoo cannot seek), the control is simply not offered.

## Repairs: a device that stops answering

When the Control finds that one of its network devices (a projector, a
receiver ...) no longer answers, Home Assistant shows it under **Settings →
Repairs**: "Projector in the Living Room is not reachable", with what to do —
check that it is switched on and on the network, or check it with Edit on
the Control's Devices page (linked). The Control decides, exactly as its own
dashboard does: three missed checks in a row (it checks every 5 minutes), the
device enabled, and not set to Deep Standby (those leave the network while
off, which is normal). The message goes away by itself when the Control
reaches the device again; Home Assistant reads this once a minute.

## Devices as remotes

Each device the Control shows on an offered room's page (the room's or
zone's own devices, plus the shared ones; disabled devices left out) gets one
**remote** in Home Assistant, on its own device named after it —
`remote.projector`, never with the area in front. Its commands are the
Control's own for that device (the `commands` attribute: id and label):

```yaml
action: remote.send_command
target: { entity_id: remote.projector }
data: { command: ["menu", "HDMI 1"] }
```

A command is its id or its label; one the device does not have is refused
and **nothing** is sent. On / off shows the Control's power reading; turning
it on or off uses the device's power on / power off commands when it has
them. Remotes are not exposed to any voice assistant. A device added on the
Control appears within a minute; one removed (or no longer in an offered
room) is removed — never because a read failed. The Control's API does not
say which devices an activity uses, so "a room's devices" is what its page
lists.

## Automations: triggers per room

Every room's device offers five triggers in the automation editor
(*When ... Device ... the room*):

| Trigger | Fires when |
|---|---|
| **Activity starting** | the Control has taken a start and is running its steps (seen at the next read, within about 5 seconds) — before the TV, amplifier and player are on. A start that came and went between two reads is reported starting, then started. |
| **Activity started** | the Control shows an activity (other than Off) as running in that room — after its start, not when it was asked for. Switching from one activity to another is the new one starting. |
| **Activity stopped (room off)** | the room went off (its Off activity, or nothing). Names the activity that stopped. |
| **Playback paused** | Now Playing went from playing to paused, in the same activity. |
| **Playback resumed** | ... and from paused back to playing. |

Each can be narrowed to one of the room's activities ("Activity: Kaleidescape")
— "film starts in the Cinema → curtains closed, lights dim; paused → lights
30 %". The automation gets `trigger.event.data.activity_id`,
`activity_name`, `unit` and `entity_id`. The same event,
`artiled_control_event`, can be used in an Event trigger. The rooms are read
every 5 seconds, so a trigger comes up to about 5 seconds after the change; a
change that comes and goes between two reads is not seen.

## ⚠ Installer notes

- **AV only through the Control.** Customers and resellers control their AV
  devices through the Control — in Home Assistant that means through
  ArtiCONTROL, never also through Home Assistant's own TV, Sonos or receiver
  integrations. Two controllers for one amplifier fight each other.
- **Area wording.** "Turn off the living room" said to Assist, with no device
  named, turns off **every exposed entity in that area** — lights and Hub rooms
  included. That is Home Assistant's behaviour. Name the device, or keep the
  room's lights and AV in different areas, if that is not wanted.
- **Different network segments.** Home Assistant follows a Control that gets a
  new address by listening to the Control's own announcement (UDP 50081) — on
  the same network segment only. Across segments, use **Reconfigure**.
- **A room's first zone** replaces the room unit with zone units (that is how
  the Control's web app works too). The room's old entity is removed after the
  hour, with its history.
- **Loops.** The Control can call Home Assistant services from its activities.
  An automation on an ArtiCONTROL player that starts an activity whose steps
  call Home Assistant again can loop. Keep such automations one-directional.
- Only an IP address is ever followed automatically; a host **name** follows
  itself through DNS.

## State semantics

- The Control says off → Home Assistant shows off. The Control unreachable for
  three polls in a row → entities go *unavailable*. Nothing is ever assumed
  from our own commands: after each command the state is read again at +1 s and
  +3 s.
- The only local memory: the last cover of the running activity (so a gap in
  playback keeps it) and the last published position (so a steady play is not
  written to the recorder every five seconds).

## Install

1. HACS → ⋮ → **Custom repositories** → `https://github.com/artiled/ArtiLED_Control_HA`,
   type **Integration** → install **ArtiCONTROL**.
2. Restart Home Assistant.
3. Settings → Devices & services → **Add integration** → ArtiCONTROL.
4. Host = Control IP, then your Control username and password.

## Tests

Pure Python, no Home Assistant install needed — each file is a script:

```
for t in tests/test_*.py; do python3 "$t" || break; done
```

`tests/ha_stubs.py` is an inert Home Assistant and a fake Control (TEST-NET
addresses only); the integration is loaded as shipped. `tests/test_units.py`
also runs the Control web app's own `app/includes/units.php` on the same
fixtures when `php` and a Control API checkout (`CONTROL_API`) are present.

## Requirements

- Control API with `on_activity`, `off_activity` and `starting_activity` in
  `/rooms` and `/zones`. API 2.00 or later is used fully (one `/activities`
  call for every room, mute state, unknown volume); older Controls are read one
  room at a time.
- No extra Python dependencies (uses HA's shared aiohttp session).

## Copyright

Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.

This is proprietary software. Not open source. Not for redistribution, and not
open to derivative works. It is published in a public repository for one reason:
so that ArtiLED customers and resellers can install and update it through HACS,
which reads from GitHub only.

Being readable here grants no licence to reuse it. Third-party components, when
any are bundled, keep their own licences and are listed in
[THIRD-PARTY.md](THIRD-PARTY.md) with their full texts in `LICENSES/`.
