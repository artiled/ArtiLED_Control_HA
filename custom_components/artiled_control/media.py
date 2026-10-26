# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Search and play (S3a, #426): the pure rules. No Home Assistant here.

What can be played is what /activity start {"play": {...}} accepts - the play
objects /activity_favorites hands out (ArtiControlAPI.txt, #107):

- music: {"kind":"music","source","key","title"}, source one of music_album
  (Roon: Control's album id), sonos_favorite (the My Sonos favourite's name),
  spotify_album (spotify:album:<id>), heos_favorite (its position, title =
  its name, required), zidoo_album (zidoo:<device>:<sha1>);
- movie: {"kind":"movie","title_id"} with an optional "copy".

⚠ A play belongs to the ACTIVITY'S OWN player - its music_service /
music_where / movie_player exactly as /activities answers them. Ports of
api/includes/activity_play.php (activity_play_shape, activity_play_player),
so nothing is sent that the Control would refuse for its shape; the Control
checks the item's identity again itself.

The search rules are the webapp's: app_music_album_matches (Find Music's
album list) and movie_player/films.js matches/fold (Find Movies).
"""
from __future__ import annotations

import json
import re
import unicodedata

# The play's music sources -> the service that plays them
# (activity_play_sources, the firmware's artiled_play.MUSIC_SOURCES).
PLAY_SOURCES = {
    "music_album": "roon",
    "sonos_favorite": "sonos",
    "spotify_album": "spotify",
    "heos_favorite": "heos",
    "zidoo_album": "zidoo",
    # API 2.01 / 2.02 (the Control's activity_play_sources): playlists, and
    # what a player's Find Music plays from its catalogue. Seen only when the
    # Control hands them out - feature detected by their being there.
    "roon_playlist": "roon",
    "spotify_playlist": "spotify",
    "tidal_playlist": "sonos",
    "deezer_playlist": "sonos",
    "heos_playlist": "heos",
    "roon_item": "roon",
    "spotify_item": "spotify",
    "sonos_item": "sonos",
    "heos_item": "heos",
}
MUSIC_SERVICES = frozenset(PLAY_SOURCES.values())
_ITEMS = ("roon_item", "spotify_item", "sonos_item", "heos_item")

# Services whose WHOLE album list a search reaches through /unit_find (the
# webapp's own requests). Sonos and HEOS can only be played as favourites;
# a Zidoo search answers its own addresses, not the zidoo_album key - so for
# those three the hearts are what can be played.
FIND_SERVICES = frozenset({"roon", "spotify"})

# What the API says happened to a start's play that means it was NOT played
# (activity_play_answer). Everything else is a submission, never "played".
NOT_PLAYED = frozenset({"failed", "discarded", "skipped", "unresolved", "refused"})

# /activity_favorites "type" -> Home Assistant media class.
CLASS_OF_TYPE = {
    "Album": "album",
    "Playlist": "playlist",
    "Radio": "channel",
    "Track": "track",
    "Artist": "artist",
    "Movie": "movie",
}

PREFIX_PLAY = "play:"
PREFIX_FAVORITES = "favorites:"
# A search's results at most: the dashboard's Browse page shows them in pages
# of 100 (Find Music's PAGE_TILES); the LLM tool takes its own first 35.
MAX_RESULTS = 300

_NUMBER = re.compile(r"^[1-9][0-9]{0,9}$")
_SPOTIFY = re.compile(r"^spotify:album:[A-Za-z0-9]{1,64}$")
_ZIDOO = re.compile(r"^zidoo:[1-9][0-9]{0,9}:[0-9a-f]{40}$")
_SQUEEZE = re.compile(r"[\s\-‐-―_]+")


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---- the activity's own player ---------------------------------------------


def player_of(row: dict | None) -> dict:
    """Port of activity_play_player: {"service", "where", "movie_player"}.
    A service without a target, or one a play cannot name, is no player."""
    row = row or {}
    service = str(row.get("music_service") or "")
    where = str(row.get("music_where") or "")
    if not service or not where or service not in MUSIC_SERVICES:
        service, where = "", ""
    return {"service": service, "where": where, "movie_player": max(0, _int(row.get("movie_player")))}


def has_player(row: dict | None) -> bool:
    """The activity plays music or films that a play can name. A Control
    older than #107 sends neither field: nothing is offered."""
    player = player_of(row)
    return bool(player["service"]) or player["movie_player"] > 0


def player_activities(offered: list[dict] | None) -> list[dict]:
    """The unit's offered activities that have a player, in Control order."""
    return [row for row in offered or [] if has_player(row)]


# ---- the play object --------------------------------------------------------


def play_shape(play) -> dict | None:
    """Port of activity_play_shape: the canonical play, or None."""
    if not isinstance(play, dict) or not play:
        return None
    title = play.get("title", "")
    if not isinstance(title, str) or len(title) > 512:
        return None
    title = title.strip()
    kind = play.get("kind")
    if kind == "music":
        if set(play) - {"kind", "source", "key", "title", "tracks"}:
            return None
        source, key = play.get("source"), play.get("key")
        if not isinstance(source, str) or source not in PLAY_SOURCES:
            return None
        tracks = play.get("tracks", False)
        if not isinstance(tracks, bool) or (tracks and (source != "heos_playlist" or not str(key).startswith("1025:"))):
            return None
        if isinstance(key, int) and not isinstance(key, bool):
            key = str(key)
        if not isinstance(key, str) or not key.strip() or len(key) > 4096:
            return None
        key = key.strip()
        if source in ("music_album", "heos_favorite") and not _NUMBER.match(key):
            return None
        if source == "heos_favorite" and not title:
            return None
        if source == "spotify_album" and not _SPOTIFY.match(key):
            return None
        if source == "zidoo_album" and not _ZIDOO.match(key):
            return None
        if source == "spotify_playlist" and not re.match(r"^spotify:playlist:[A-Za-z0-9]{1,64}$", key):
            return None
        if source == "tidal_playlist" and not re.match(r"^tidal:playlist:[0-9a-f-]{1,40}$", key):
            return None
        if source == "deezer_playlist" and not re.match(r"^deezer:playlist:[0-9]{1,20}$", key):
            return None
        if source == "heos_item" and not title:
            return None   # it plays by its name (the Control's rule); the Control checks the rest itself
        out = {"kind": "music", "source": source, "key": key, "title": title}
        if tracks:
            out["tracks"] = True
        return out
    if kind == "movie":
        if set(play) - {"kind", "title_id", "copy", "title"}:
            return None
        tid, copy = play.get("title_id"), play.get("copy", 0)
        if not isinstance(tid, int) or isinstance(tid, bool) or tid <= 0:
            return None
        if copy is None:
            copy = 0
        if not isinstance(copy, int) or isinstance(copy, bool) or copy < 0:
            return None
        out: dict = {"kind": "movie", "title_id": tid}
        if copy > 0:
            out["copy"] = copy
        if title:
            out["title"] = title
        return out
    return None


def play_fits(row: dict | None, play: dict) -> str | None:
    """Why this play does not belong to this activity's own player, or None.
    The words are the Control's own (activity_play_check)."""
    player = player_of(row)
    if play.get("kind") == "movie":
        return None if player["movie_player"] > 0 else "This activity plays no films"
    service = PLAY_SOURCES.get(str(play.get("source")))
    if player["service"] != service:
        if not player["service"]:
            return "This activity plays no music here"
        return f"This activity plays {player['service']}, not {service}"
    return None


def play_key(play: dict) -> tuple:
    """One identity per playable thing, for merging duplicates."""
    if play.get("kind") == "movie":
        return ("movie", play.get("title_id"), play.get("copy", 0))
    return ("music", play.get("source"), play.get("key"))


def content_id(activity_id: int, play: dict) -> str:
    """The media_content_id Home Assistant hands back to async_play_media.
    It names the activity and the play - nothing else, no address or token."""
    return f"{PREFIX_PLAY}{int(activity_id)}:" + json.dumps(play, separators=(",", ":"), ensure_ascii=False)


def parse_content_id(value) -> tuple[int, dict] | None:
    """(activity id, play) or None for anything that is not ours."""
    text = str(value or "")
    if not text.startswith(PREFIX_PLAY):
        return None
    head, sep, body = text[len(PREFIX_PLAY):].partition(":")
    if not sep or not _NUMBER.match(head):
        return None
    try:
        play = play_shape(json.loads(body))
    except ValueError:
        return None
    return (int(head), play) if play else None


def favorites_id(activity_id: int) -> str:
    return f"{PREFIX_FAVORITES}{int(activity_id)}"


def parse_favorites_id(value) -> int | None:
    text = str(value or "")
    if not text.startswith(PREFIX_FAVORITES):
        return None
    head = text[len(PREFIX_FAVORITES):]
    return int(head) if _NUMBER.match(head) else None


# ---- matching, as the webapp matches ---------------------------------------


def fold(text) -> str:
    """films.js fold: case and accents folded ("amelie" finds Amélie)."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _squeeze(text) -> str:
    return _SQUEEZE.sub("", str(text or "").lower())


_WORD_SPLIT = re.compile(r"[^0-9a-z]+")
_POSSESSIVE = re.compile(r"['\u2019]s\b")


def words(query) -> list[str]:
    """What was said, as words: case and accents folded, a possessive 's
    dropped ("Jackson's", "Jacksons" -> jackson). Michel 2026-10-26: "Michael
    Jackson Thriller" must find Thriller - every word, wherever it is."""
    text = _POSSESSIVE.sub("", fold(query))
    out = []
    for w in _WORD_SPLIT.split(text):
        if not w:
            continue
        if len(w) > 3 and w.endswith("s"):
            w = w[:-1]   # matched as a part of a word, so "beatle" still finds Beatles
        out.append(w)
    return out


def all_words_in(text, query) -> bool:
    """Every word of the query occurs in the text (as a part of a word)."""
    hay = fold(text)
    tight = _squeeze(hay)
    return all(w in hay or w in tight for w in words(query))


def search_word(query) -> str:
    """The one word the Control's own album search is asked for - it finds a
    whole query only inside ONE field - the longest; every word is then
    checked here (all_words_in)."""
    ws = words(query)
    return max(ws, key=len) if ws else str(query or "").strip()


def album_matches(item: dict, query: str) -> bool:
    """EVERY word of the query in the title and the artist together (Michel
    2026-10-26: "Michael Jackson Thriller"); and, as
    app_music_album_matches, the whole query with spaces and hyphens squeezed
    out ("synthpop" finds Synth Pop)."""
    q = str(query or "").strip()
    if not q:
        return True
    title, sub = str(item.get("title") or ""), str(item.get("subtitle") or "")
    if all_words_in(f"{title} {sub}", q):
        return True
    tight = _squeeze(q)
    return bool(tight) and (tight in _squeeze(title) or tight in _squeeze(sub))


def film_matches(title: dict, query: str) -> bool:
    """films.js matches - title, director, cast, genres - and the year, with
    every word of the query somewhere in them."""
    q = str(query or "").strip()
    hay = " ".join(str(title.get(k) or "") for k in ("name", "director", "cast", "genres", "year"))
    return not q or all_words_in(hay, q)


def rank(title, query) -> int:
    """HassMediaSearchAndPlay plays the FIRST result, so: 0 the exact title;
    1 a title made only of words that were said ("Michael Jackson Thriller"
    -> Thriller, not Thriller 25); 2 the title starts with what was said;
    3 every word in the title; 4 matched with the artist / director."""
    t, q = fold(title).strip(), fold(query).strip()
    if not q:
        return 4
    tw, qw = words(t), words(q)
    if t == q or (tw and tw == qw):
        return 0
    if tw and set(tw) <= set(qw):
        return 1
    if t.startswith(q):
        return 2
    if all_words_in(t, q):
        return 3
    return 4


# ---- answers -> results -----------------------------------------------------


def _result(activity_id, play, title, subtitle, image, media_class) -> dict | None:
    play = play_shape(play)
    if play is None or not str(title or "").strip() or not str(image or ""):
        # Never shown without artwork (Michel).
        return None
    return {
        "activity": int(activity_id),
        "play": play,
        "title": str(title),
        "subtitle": str(subtitle or ""),
        "image": str(image),
        "media_class": media_class,
        "content_type": "movie" if play["kind"] == "movie" else "music",
    }


# The services a My Sonos / HEOS favourite names on its second line, and the
# kind words Sonos files a favourite under: never shown as if they were the
# artist (Michel 2026-10-22 / 2026-10-26: strip the Sonos kind / service).
_NOT_ARTISTS = {
    "spotify", "tidal", "apple music", "apple", "deezer", "tunein", "amazon music", "amazon", "qobuz", "soundcloud",
    "youtube music", "sonos radio", "sonos", "heos", "napster", "pandora", "iheartradio", "radio paradise", "plex",
    "local", "music library", "album", "playlist", "station", "track", "song", "artist", "radio", "other",
}


def artist_line(item: dict) -> str:
    """The artist a row's second line may show: its subtitle - unless that is
    the service or the kind the favourite was filed under, then nothing."""
    sub = str((item or {}).get("subtitle") or "").strip()
    provider = str((item or {}).get("provider") or "").strip().lower()
    if not sub or sub.lower() in _NOT_ARTISTS or (provider and sub.lower() == provider):
        return ""
    return sub


def favorite_results(items, activity_id: int, query: str | None = None) -> list[dict]:
    """/activity_favorites items -> results; their own play passes through.
    With a query, filtered as the webapp filters a list it already has."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if query is not None and not album_matches(it, query):
            continue
        r = _result(activity_id, it.get("play"), it.get("title"), artist_line(it), it.get("image"),
                    CLASS_OF_TYPE.get(str(it.get("type") or ""), "playlist"))
        if r:
            out.append(r)
    return out


def album_results(items, activity_id: int, service: str, query: str | None = None) -> list[dict]:
    """Control's own album list (Roon: /unit_find albums; Spotify: library
    what=albums) -> music_album / spotify_album plays. Hidden rows never
    appear, as everywhere in the app."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("hidden"):
            continue
        if query is not None and not album_matches(it, query):
            continue
        if service == "roon":
            if not it.get("path"):
                continue
            play = {"kind": "music", "source": "music_album", "key": str(_int(it.get("id"))), "title": str(it.get("title") or "")}
        else:
            play = {"kind": "music", "source": "spotify_album", "key": str(it.get("uri") or ""), "title": str(it.get("title") or "")}
        r = _result(activity_id, play, it.get("title"), it.get("subtitle"), it.get("image"), "album")
        if r:
            out.append(r)
    return out


def film_results(titles, activity_id: int, movie_player: int, query: str) -> list[dict]:
    """Find Movies' titles -> movie plays: the ones that match, and that play
    on THIS activity's player ("on", movie_titles_answer)."""
    out = []
    for t in titles or []:
        if not isinstance(t, dict) or t.get("hidden"):
            continue
        on = t.get("on")
        if isinstance(on, list) and int(movie_player) not in [_int(p) for p in on]:
            continue
        if not film_matches(t, query):
            continue
        year = _int(t.get("year"))
        play = {"kind": "movie", "title_id": _int(t.get("id")), "title": str(t.get("name") or "")}
        r = _result(activity_id, play, t.get("name"), str(year) if year > 0 else "", t.get("image"), "movie")
        if r:
            out.append(r)
    return out


def current_activity(raw: dict | None) -> int:
    """The activity a play would land on NOW without another start: the one
    starting (the Control attaches a play to a start in progress), else the
    running one. 0 when nothing runs or the room is going / gone Off."""
    raw = raw or {}
    off_id = _int(raw.get("off_activity"))
    s = _int(raw.get("starting_activity"))
    current = s if s > 0 else _int(raw.get("active_activity"))
    return 0 if current <= 0 or current == off_id else current


def runs_here(running: dict | None, owner: dict | None, play: dict) -> bool:
    """Can the RUNNING activity play this item on its own player, so nothing
    else is started (Michel 2026-10-26)? Only where the Control would accept
    it for that activity as surely as for the owner: the same service (a Roon
    album, a Sonos or HEOS favourite, a Spotify album are the service's), a
    Zidoo album only on the same Zidoo, a film only on the same movie player
    (a copy is per player)."""
    if not running or play_fits(running, play) is not None:
        return False
    mine, theirs = player_of(running), player_of(owner)
    if play.get("kind") == "movie":
        return mine["movie_player"] == theirs["movie_player"] > 0
    if play.get("source") == "zidoo_album":
        parts = str(play.get("key", "")).split(":")
        return len(parts) == 3 and mine["where"] == parts[1]
    return True


def retarget(results, running: dict | None, rows: list[dict] | None) -> list[dict]:
    """Every result the running activity can play is played THERE (no new
    start of another source); the rest stay with the activity that owns
    them. ⚠ Only ever to the running activity, never between idle ones."""
    if not running:
        return list(results)
    rid = _int(running.get("id"))
    by_id = {_int(r.get("id")): r for r in rows or []}
    out = []
    for r in results:
        if r["activity"] != rid and runs_here(running, by_id.get(r["activity"]), r["play"]):
            r = {**r, "activity": rid}
        out.append(r)
    return out


# ---- one heart = one record (feedback_one_heart_is_one_record) --------------
#
# A port of the Control's own edition rule, music_albums_lookup_title
# (api/includes/music_albums_lookup.php): a version ending - "(Remastered)",
# "[Original Motion Picture Soundtrack]", "(Deluxe Edition)", " - 2011 Remaster"
# - is not part of the record's name; "live", "remix", "acoustic" ... are.

_NEVER = re.compile(r"\blive\b|remix|acoustic|instrumental|karaoke|\bdemo\b", re.I)
_ENDING = re.compile(
    r"remaster|deluxe|edition|expanded|anniversary|bonus|version|\bmono\b|stereo|reissue|special|collector|legacy|hi-?res|24[- ]?bit|khz|\bmfsl\b|explicit|\bmix\b"
    r"|\bsacd\b|\bdsd\d*\b|\bmqa\b|\batmos\b|binaural|\b[57]\.1\b|\b(44\.1|48|88\.2|96|176\.4|192|352\.8)\s*(khz)?\s*[-/]\s*(16|24|32)\b|\bseries\b"
    r"|\bint[\u2019']?l\b|\breg\.?\s*tracks\b|complete sessions"
    r"|\bshm\b|hybrid|\bjapan(ese)?\b|box ?set|gold standard|\b(disc|cd)\s*\d+\b"
    r"|soundtrack|motion picture|music from|\bscore\b|\bost\b|bande originale|muzyka z filmu", re.I)
_INNER = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]](\s*[)\]])$")
_LAST_BRACKET = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]\s*$")
_LAST_DASH = re.compile(r"\s+[-\u2013\u2014/]\s+([^-\u2013\u2014/]+)$")
_OST = re.compile(r"\s+([-\u2013\u2014/]\s+)?(the\s+)?original\s+(((motion\s+picture|television|film)\s+)?(soundtrack|score)|motion\s+picture)$", re.I)
_MUSIC_FROM = re.compile(r"\s+([-\u2013\u2014/]\s+)?music\s+from\s+the\s+(original\s+)?motion\s+picture$", re.I)


def record_title(title) -> str:
    """The record's own name, its edition ending gone (music_albums_lookup_title)."""
    s = str(title or "").strip()
    s = _INNER.sub(lambda m: m.group(2) if (not _NEVER.search(m.group(1)) and _ENDING.search(m.group(1))) else m.group(0), s)
    s = _OST.sub("", s)
    s = _MUSIC_FROM.sub("", s)
    s = re.sub(r"\s+[57]\.1$", "", s)
    while True:
        m = _LAST_BRACKET.search(s) or _LAST_DASH.search(s)
        if not m:
            break
        inner = m.group(1)
        if _NEVER.search(inner) or not (_ENDING.search(inner) or re.match(r"^aka\b", inner, re.I)
                                        or re.match(r"^\s*(ep|single|maxi[- ]single)\s*$", inner, re.I)):
            break
        s = s[:m.start()].rstrip()
    return s or str(title or "").strip()


def record_key(title) -> str:
    """The record's name as compared (music_albums_lookup_key): folded,
    apostrophes gone, other punctuation a space."""
    text = fold(record_title(title)).replace("'", "").replace("`", "").replace("\u2019", "")
    return " ".join(w for w in _WORD_SPLIT.split(text) if w)


def work_key(r: dict) -> tuple:
    """The same album or film from another source (Michel 2026-10-26: Thriller
    on Roon and twice on the Zidoo): its folded title and artist (a film's
    year), and whether it is music or a film."""
    # its kind too: an album and a playlist of one name are two things
    kind = str(r.get("media_class") or CLASS_OF_TYPE.get(str(r.get("type") or ""), str(r.get("type") or ""))).lower()
    return (record_key(r.get("title")), " ".join(words(r.get("subtitle"))), r.get("content_type"), kind)


def dedupe(results, prefer: int = 0, order: list[int] | None = None) -> list[dict]:
    """One result per playable thing AND per album / film: the running
    activity's copy, else the first owning activity in Control order; the
    first one seen keeps its place."""
    order = list(order or [])

    def activity_rank(r):
        if prefer and r["activity"] == prefer:
            return -1
        return order.index(r["activity"]) if r["activity"] in order else len(order)

    best: dict[tuple, dict] = {}
    place: dict[tuple, int] = {}
    alias: dict[tuple, tuple] = {}
    for n, r in enumerate(results):
        keys = (("p",) + play_key(r["play"]), ("w",) + work_key(r))
        group = next((alias[k] for k in keys if k in alias), keys[0])
        for k in keys:
            alias.setdefault(k, group)
        place.setdefault(group, n)
        if group not in best or activity_rank(r) < activity_rank(best[group]):
            best[group] = r
    return [best[g] for g in sorted(best, key=lambda g: place[g])]


def merge(results, query: str, prefer: int = 0, order: list[int] | None = None,
          classes=None, limit: int = MAX_RESULTS) -> list[dict]:
    """One list for a search: one result per playable thing and per album /
    film (dedupe), best match first."""
    wanted = {str(getattr(c, "value", c)) for c in classes} if classes else None
    kept = [r for r in results if wanted is None or r["media_class"] in wanted]
    unique = dedupe(kept, prefer, order)
    first = {id(r): n for n, r in enumerate(unique)}
    return sorted(unique, key=lambda r: (rank(r["title"], query), first[id(r)]))[:limit]


def browse_tree(per_activity, running: dict | None, rows: list[dict] | None) -> dict:
    """The media browser's tree (Michel 2026-10-26, live: "it asks me first
    service. It is NOT service aware" and "I see doubles").

    per_activity: [(activity row, its results)] in Control order (rows that
    could not be read left out). running: the activity running or starting
    now when it has a player, else None.

    Running: the ROOT is that activity's own content - its favourites and
    every other heart its player can play (retarget), one per item - and the
    other activities' remaining items go in "Other sources", one folder each.
    Otherwise: one folder per activity with anything of its own (an item a
    folder before it already holds is not repeated), or its items straight
    away when there is only one. -> {"root": [results], "folders":
    [(row, [results])], "others": bool}
    """
    order = [_int(r.get("id")) for r in rows or []]
    if running is not None:
        rid = _int(running.get("id"))
        mine = [res for row, res in per_activity if _int(row.get("id")) == rid]
        rest = [(row, res) for row, res in per_activity if _int(row.get("id")) != rid]
        pooled = retarget([r for res in mine for r in res] + [r for _, res in rest for r in res], running, rows)
        root = dedupe([r for r in pooled if r["activity"] == rid], rid, order)
        # ⚠ ONE entry per item in the WHOLE tree (Michel 2026-10-26, live:
        # Thriller on Roon and twice on the Zidoo): an item, or another copy or
        # edition of the same record, already shown is not shown again.
        held = {play_key(r["play"]) for r in root}
        held_w = {work_key(r) for r in root}
        folders = []
        for row, res in rest:
            own = dedupe([r for r in res if play_key(r["play"]) not in held and work_key(r) not in held_w
                          and not runs_here(running, row, r["play"])])
            held |= {play_key(r["play"]) for r in own}
            held_w |= {work_key(r) for r in own}
            if own:
                folders.append((row, own))
        return {"root": root, "folders": folders, "others": True}
    held: set = set()
    held_w: set = set()
    folders = []
    for row, res in per_activity:
        own = dedupe([r for r in res if play_key(r["play"]) not in held and work_key(r) not in held_w])
        held |= {play_key(r["play"]) for r in own}
        held_w |= {work_key(r) for r in own}
        if own:
            folders.append((row, own))
    if len(folders) == 1:
        return {"root": folders[0][1], "folders": [], "others": False}
    return {"root": [], "folders": folders, "others": False}


def search_jobs(raw: dict, offered: list[dict] | None, off_named: bool | None = False) -> list[tuple[str, dict, bool, bool]]:
    """What to ask for one search: (job, activity row, idle, fallback).

    ⚠ The token doors (/unit_find, /unit_find_movies) answer for the unit's
    RUNNING activity - its active_activity - and, while NOTHING runs (0), for
    an activity the request names (idle). A unit where another activity runs
    cannot be searched there, so its other player activities are searched by
    their hearts, which /activity_favorites reads whether or not they run.

    While the unit's OFF activity is the active one, a Control with the API
    extension (Michel 2026-10-26) also searches a named activity. off_named:
    True = this Control does, False = it does not, None = not known yet -
    then it is asked (idle, fallback=True) and a refusal means the hearts.

    job: "albums" (Control's album list via /unit_find), "films" (Find
    Movies' titles via /unit_find_movies), "favorites" (/activity_favorites).
    """
    raw = raw or {}
    running = _int(raw.get("active_activity"))
    off_id = _int(raw.get("off_activity"))
    off_now = running > 0 and running == off_id and off_named is not False
    jobs: list[tuple[str, dict, bool, bool]] = []
    for row in player_activities(offered):
        aid = _int(row.get("id"))
        player = player_of(row)
        reachable = running == 0 or running == aid or off_now
        idle = running == 0 or off_now
        fallback = off_now and off_named is None
        hearts = False
        if player["service"]:
            if player["service"] in FIND_SERVICES and reachable:
                jobs.append(("albums", row, idle, fallback))
            else:
                hearts = True
        if player["movie_player"] > 0:
            if reachable:
                jobs.append(("films", row, idle, fallback))
            elif not player["service"]:
                # A films-only activity's hearts are its films.
                hearts = True
        if hearts:
            jobs.append(("favorites", row, False, False))
    return jobs


# ---- the room a favourite plays in (Michel 2026-10-26) ----------------------


def owns(row: dict | None, play: dict) -> bool:
    """This activity's own player can play it: its service (a Zidoo album only
    on its own Zidoo), or for a film its movie player."""
    if not row or play_fits(row, play) is not None:
        return False
    if play.get("source") == "zidoo_album":
        parts = str(play.get("key", "")).split(":")
        return len(parts) == 3 and player_of(row)["where"] == parts[1]
    return True


def activity_in_room(room: dict, play) -> int:
    """The activity of ONE room that plays this favourite (Michel 2026-10-26:
    "Favorites are the same in all rooms ... but NOT in all services"): its
    running activity when that activity's own player plays it, else the
    room's first activity whose player does (the room is switched to it). 0:
    no activity of that room plays it. Never another service's copy, never
    another room."""
    current = next((r for r in room.get("rows") or [] if _int(r.get("id")) == _int(room.get("current"))), None)
    if current is not None and owns(current, play):
        return _int(current.get("id"))
    first = next((r for r in room.get("rows") or [] if owns(r, play)), None)
    return _int(first.get("id")) if first is not None else 0


SERVICE_NAMES = {"roon": "Roon", "sonos": "Sonos", "spotify": "Spotify", "heos": "HEOS", "zidoo": "Zidoo", "movies": "Movies"}


def service_of_play(play) -> str:
    """The service a play belongs to (its source's player), "movies" for a film."""
    if not isinstance(play, dict):
        return ""
    if play.get("kind") == "movie":
        return "movies"
    return PLAY_SOURCES.get(str(play.get("source") or ""), "")


def voice_room(units, play) -> tuple[str, int, str]:
    """Where a favourite said WITHOUT a room plays (Michel 2026-10-26): the
    room where something is playing now - only when exactly ONE room plays.
    None or several playing: refused with the reason, nothing guessed (no
    "last started", no default, no "first that can"). In that room: its
    running activity when it plays the favourite's service, else its activity
    that does; none: refused. -> (unit key, activity id, "") or ("", 0, why)"""
    playing = [u for u in units or [] if u.get("playing")]
    if not playing:
        return "", 0, "nothing is playing in any room - say the room, or play it from the room's page"
    if len(playing) > 1:
        names = ", ".join(str(u.get("name") or u.get("key")) for u in playing)
        return "", 0, f"several rooms are playing ({names}) - say the room, or play it from the room's page"
    room = playing[0]
    activity = activity_in_room(room, play)
    if not activity:
        service = SERVICE_NAMES.get(service_of_play(play), service_of_play(play) or "its service")
        return "", 0, f"{room.get('name') or room.get('key')} has no activity for {service}"
    return room["key"], activity, ""


def dedupe_per_service(results, prefer: int = 0, order: list[int] | None = None) -> list[dict]:
    """The house's favourites, one per record WITHIN each service (Michel
    2026-10-26: the same in every room, NOT in every service): a record on
    Roon and on Sonos stays twice - each its own service's play; nothing is
    translated from one service to another. Services in the order they come."""
    groups: dict[str, list] = {}
    for r in results or []:
        groups.setdefault(str(r.get("service") or ""), []).append(r)
    out = []
    for service in groups:
        out.extend(dedupe(groups[service], prefer, order))
    return out
