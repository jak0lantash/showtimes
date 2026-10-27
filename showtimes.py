#!/usr/bin/env python3
"""
showtimes.py - consolidated cinema showtimes for Cork, Waterford, Dublin and Blanchardstown.

Usage:
    python3 showtimes.py                  # Cork, today, PG highlighted
    python3 showtimes.py waterford        # another location (or: w)
    python3 showtimes.py d                # all of Dublin
    python3 showtimes.py b                # just Blanchardstown
    python3 showtimes.py --day 1          # tomorrow
    python3 showtimes.py --date 2026-10-24
    python3 showtimes.py --upcoming       # hide sessions that already started
    python3 showtimes.py --only PG,G      # filter by rating
    python3 showtimes.py --sort movie     # sort by movie (default: time)
    python3 showtimes.py --details        # add format column (PURE, MAXX, Hypersense...)
    python3 showtimes.py --cinema reel    # only cinemas whose name contains "reel"
    python3 showtimes.py --urls           # print booking links as a column
    python3 showtimes.py --premium        # only premium screens (★)
    python3 showtimes.py --no-cache       # ignore pages cached in the last 15 minutes

In terminals that support hyperlinks (Windows Terminal, GNOME Terminal, iTerm2...)
the time opens the booking page and the movie title opens the film page
(Ctrl+click in Windows Terminal).

Requires: pip install requests beautifulsoup4
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlencode, urlsplit
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Dublin")
except Exception:          # no tz database: fall back to the machine's local time
    TZ = None

# --------------------------------------------------------------------------
# Configuration - add or remove cinemas here.
#   kind "admitone": The Reel Picture / Arc Cinema sites (same platform)
#   kind "omniplex": https://www.omniplex.ie/cinema/showtimes/<site>
#                    (<site> is the lowercase cinema name, e.g. cork, limerick)
#   kind "cinematimes": a cinema page on cinematimes.com, used for Odeon
#                    because odeoncinemas.ie blocks automated access
#   kind "unsupported": listed so you know it exists, but not scraped
#   "premium": session formats to flag with a star (case-insensitive,
#              matched against the label the site shows, e.g. "PURE 14:30")
# --------------------------------------------------------------------------
CT = "https://cinematimes.com/ie/"
ODEON_PREMIUM = ["IMAX", "ISENSE", "RECLINER"]
IMC_PREMIUM = ["GALACTIC", "LUXX"]


def ct(name: str, path: str, premium: list[str] | None = None) -> dict:
    """A cinema read from its cinematimes.com page (path after /ie/)."""
    return {"name": name, "kind": "cinematimes", "url": CT + path, "premium": premium or []}


def unsupported(name: str) -> dict:
    return {"name": name, "kind": "unsupported"}


ODEON_BLANCH = ct("Odeon Blanchardstown",
                  "dublin/cinemas/odeon-cinema-blanchardstown-dublin-15/", ODEON_PREMIUM)
DRAIOCHT = ct("Draíocht Blanchardstown", "dublin/cinemas/draiocht-blanchardstown/")

LOCATIONS = {
    "cork": [
        {"name": "Reel Ballincollig", "kind": "admitone",
         "url": "https://ballincollig.thereelpicture.com/",
         "premium": ["RECLINE", "PURE"]},
        {"name": "Reel Blackpool", "kind": "admitone",
         "url": "https://blackpool.thereelpicture.com/",
         "premium": ["RECLINE", "PURE"]},
        {"name": "Omniplex Cork", "kind": "omniplex",
         "url": "https://www.omniplex.ie/cinema/showtimes/cork",
         "premium": ["MAXX"]},
        {"name": "Arc Cork", "kind": "admitone",
         "url": "https://cork.arccinema.ie/",
         "premium": ["GATE", "HYPERSENSE"]},
    ],
    "waterford": [
        {"name": "Omniplex Waterford", "kind": "omniplex",
         "url": "https://www.omniplex.ie/cinema/showtimes/waterford",
         "premium": ["MAXX"]},
        ct("Odeon Waterford", "waterford/cinemas/odeon-cinema-waterford-city/", ODEON_PREMIUM),
    ],
    "dublin": [
        ct("Cineworld Dublin", "dublin/cinemas/cineworld-cinemas-dublin-1/", ["IMAX", "4DX"]),
        ct("IMC Savoy", "dublin/cinemas/savoy-cinema-dublin-1/", IMC_PREMIUM),
        ct("IMC Santry", "dublin/cinemas/imc-santry-cinema-dublin-9/", IMC_PREMIUM),
        ct("IMC Dun Laoghaire", "dublin/cinemas/imc-dun-laoghaire/", IMC_PREMIUM),
        ct("Odeon Point Square", "dublin/cinemas/odeon-cinema-point-village-dublin-1/", ODEON_PREMIUM),
        ct("Odeon Charlestown", "dublin/cinemas/odeon-cinemas-dublin/", ODEON_PREMIUM),
        ct("Odeon Coolock", "dublin/cinemas/odeon-cinema-coolock-dublin-5/", ODEON_PREMIUM),
        ct("Odeon Stillorgan", "dublin/cinemas/odeon-cinema-stillorgan/", ODEON_PREMIUM),
        ODEON_BLANCH,
        ct("Omniplex Rathmines", "dublin/cinemas/omniplex-rathmines-dublin-6/", ["MAXX"]),
        ct("Omniplex Nutgrove", "dublin/cinemas/omniplex-nutgrove-dublin/", ["MAXX"]),
        ct("Omniplex Balbriggan", "dublin/cinemas/omniplex-balbriggan/", ["MAXX"]),
        ct("Vue Liffey Valley", "dublin/cinemas/dublin-liffey-valley/"),
        ct("Light House", "dublin/cinemas/light-house-cinema-dublin-7/"),
        DRAIOCHT,
        # No listings on cinematimes.com for these yet:
        unsupported("IFI"),
        unsupported("Stella Rathmines"),
        unsupported("Movies@ Dundrum"),
        unsupported("Movies@ Swords"),
        unsupported("Movies@ The Square"),
    ],
    "blanchardstown": [ODEON_BLANCH, DRAIOCHT],
}
DEFAULT_LOCATION = "cork"
# Short forms and other names for a location (case-insensitive).
ALIASES = {"c": "cork", "w": "waterford", "d": "dublin", "b": "blanchardstown",
           "blanch": "blanchardstown"}


def resolve_location(name: str) -> str:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key not in LOCATIONS:
        names = ", ".join(LOCATIONS) + "; short forms: " + ", ".join(sorted(ALIASES))
        raise argparse.ArgumentTypeError(f"unknown location '{name}' (choose from {names})")
    return key


IGNORE_TITLES = re.compile(r"test event|do not book", re.I)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept-Language": "en-IE,en;q=0.9",
}
TIMEOUT = 20
PER_HOST_CONCURRENCY = 2      # simultaneous requests to any one site
PER_HOST_GAP = 0.7            # seconds between request starts to the same site
MAX_RETRIES = 4               # on 429 Too Many Requests / 503
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "showtimes"
CACHE_TTL = 15 * 60           # seconds; --no-cache to bypass
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


@dataclass
class Showing:
    movie: str
    rating: str
    cinema: str
    time: str                 # "HH:MM"
    fmt: str = ""             # PURE, RECLINE, MAXX, Hypersense, ...
    url: str = ""             # booking page for this session
    film_url: str = ""        # film details page
    premium: bool = False
    extra: dict = field(default_factory=dict)   # room for IMDb rating later


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def today_local() -> dt.date:
    return dt.datetime.now(TZ).date() if TZ else dt.date.today()


_session = requests.Session()
_session.headers.update(HEADERS)
_host_locks: dict[str, threading.Semaphore] = {}
_host_last: dict[str, float] = {}
_meta_lock = threading.Lock()
USE_CACHE = True


def _polite_get(url: str, params: dict | None) -> requests.Response:
    """GET with per-site throttling and retries on 429/503."""
    host = urlsplit(url).netloc
    with _meta_lock:
        sem = _host_locks.setdefault(host, threading.Semaphore(PER_HOST_CONCURRENCY))
    with sem:
        for attempt in range(MAX_RETRIES + 1):
            with _meta_lock:     # space out request starts to the same site
                wait = _host_last.get(host, 0) + PER_HOST_GAP - _time.monotonic()
                _host_last[host] = _time.monotonic() + max(wait, 0)
            if wait > 0:
                _time.sleep(wait)
            r = _session.get(url, params=params, timeout=TIMEOUT)
            if r.status_code not in (429, 503) or attempt == MAX_RETRIES:
                r.raise_for_status()
                return r
            retry_after = r.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
            with _meta_lock:     # make other threads for this site back off too
                _host_last[host] = _time.monotonic() + min(delay, 60)
            _time.sleep(min(delay, 60))
    raise RuntimeError("unreachable")


def fetch(url: str, params: dict | None = None) -> BeautifulSoup:
    full = url + ("?" + urlencode(params) if params else "")
    path = CACHE_DIR / (hashlib.sha1(full.encode()).hexdigest() + ".html")
    if USE_CACHE and path.exists() and _time.time() - path.stat().st_mtime < CACHE_TTL:
        return BeautifulSoup(path.read_bytes().decode("utf-8", "replace"), "html.parser")

    r = _polite_get(url, params)
    # Sites declare ISO-8859-1 but actually serve UTF-8.
    try:
        text = r.content.decode("utf-8")
    except UnicodeDecodeError:
        text = r.content.decode("latin-1")
    if USE_CACHE:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return BeautifulSoup(text, "html.parser")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def rating_from_img(img: Tag | None) -> str:
    """Rating from alt text, or from file names like PGsml.png / IE_12A.png."""
    if img is None:
        return "?"
    alt = re.sub(r"^(IFCO|BBFC)\s*-\s*", "", clean(img.get("alt", "")), flags=re.I)
    if alt:
        return alt
    name = img.get("src", "").split("?")[0].rsplit("/", 1)[-1]
    name = re.sub(r"\.(png|gif|jpe?g|svg|webp)$", "", name, flags=re.I)
    name = re.sub(r"sml$", "", name, flags=re.I)
    name = re.sub(r"^(IE|UK)_", "", name, flags=re.I)
    return name.upper() or "?"


def is_rating_img(el) -> bool:
    return (isinstance(el, Tag) and el.name == "img"
            and "/ratings/" in el.get("src", "").lower())


def first_time(text: str) -> str | None:
    m = TIME_RE.search(text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def label_to_date(label: str, today: dt.date) -> dt.date | None:
    """'Today', 'Tomorrow', 'Fri 23 Oct' -> date."""
    l = clean(label).lower()
    if l == "today":
        return today
    if l == "tomorrow":
        return today + dt.timedelta(days=1)
    m = re.match(r"^[a-z]+\s+(\d{1,2})\s+([a-z]{3})", l)
    if not m:
        return None
    try:
        d = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {today.year}",
                                 "%d %b %Y").date()
    except ValueError:
        return None
    return d.replace(year=today.year + 1) if d < today - dt.timedelta(days=60) else d


# --------------------------------------------------------------------------
# Reel Picture / Arc (admit-one platform)
#   Each day is a <div id="panel_<unix midnight>"> (Arc) or <div id="panelN">
#   with a <button class="tab" data-target="N">Fri 23 Oct</button> (Reel).
#   Inside: <h2><a href=".../event/123">Title</a></h2>, a rating <img> from
#   /ratings/IE/PGsml.png, then <a href="...?perfCode=...">PURE 14:30</a>.
# --------------------------------------------------------------------------
def panel_date(panel: Tag, labels: dict[str, dt.date]) -> dt.date | None:
    m = re.fullmatch(r"panel_?(\d+)", panel.get("id", ""))
    if not m:
        return None
    n = m.group(1)
    if len(n) >= 9:                                  # unix timestamp
        ts = int(n)
        return (dt.datetime.fromtimestamp(ts, TZ).date() if TZ
                else dt.datetime.utcfromtimestamp(ts + 7200).date())
    return labels.get(n)


# Film links are "/event/123", "event/123" (Reel Picture) or absolute URLs.
EVENT_HREF = re.compile(r"(^|/)event/\d+")


def scrape_admitone(cinema: dict, target: dt.date, today: dt.date) -> list[Showing]:
    soup = fetch(cinema["url"])
    labels = {}
    for b in soup.select("[data-target]"):
        d = label_to_date(b.get_text(), today)
        if d:
            labels[b["data-target"]] = d

    panels = [p for p in soup.find_all(id=re.compile(r"^panel_?\d+$"))
              if p.find("a", href=re.compile(r"perfCode=", re.I))]
    if not panels:
        raise RuntimeError("no day panels found (site layout changed?)")
    day_panels = [p for p in panels if panel_date(p, labels) == target]

    out = []
    for panel in day_panels:
        for link in panel.find_all("a", href=re.compile(r"perfCode=", re.I)):
            text = clean(link.get_text(" "))
            time = first_time(text)
            heading = link.find_previous(
                lambda t: t.name in ("h2", "h3") and t.find("a", href=EVENT_HREF))
            if not time or heading is None:
                continue
            fmt = clean(text[:TIME_RE.search(text).start()])
            if not fmt and link.find("img"):
                fmt = re.sub(r"\s*icon$", "", clean(link.find("img").get("alt", "")), flags=re.I)
            rating = rating_from_img(link.find_previous(is_rating_img))
            film_a = heading.find("a", href=EVENT_HREF)
            out.append(Showing(clean(heading.get_text()), rating, cinema["name"], time, fmt,
                               url=urljoin(cinema["url"], link["href"]),
                               film_url=urljoin(cinema["url"], film_a["href"])))
    return out


# --------------------------------------------------------------------------
# Omniplex
#   One day per page: ?action=processFilters&filterDate=YYYY-MM-DD
#   Each film is a child <div> of div.main-container, with
#   <a href="/cinema/movie/cork/slug">Title</a>, <img src=".../ratings/IE_12A.png">
#   and showtime links <a href="/booking/seats/cork/12345"><h4>14:00</h4>...
# --------------------------------------------------------------------------
def scrape_omniplex(cinema: dict, target: dt.date, today: dt.date) -> list[Showing]:
    soup = fetch(cinema["url"], {"action": "processFilters",
                                 "filterDate": target.isoformat()})
    active = soup.select_one("button.date-nav-item.active")
    if active and active.get("data-date") and active["data-date"] != target.isoformat():
        raise RuntimeError(f"site returned {active['data-date']} instead of {target}")

    seat = re.compile(r"/booking/seats/")
    blocks = soup.select("div.main-container > div")
    if not blocks:   # fallback: nearest ancestor of each link holding a movie link
        blocks = []
        for a in soup.find_all("a", href=seat):
            b = a.find_parent(lambda t: t.find("a", href=re.compile(r"/cinema/movie/")))
            if b is not None and b not in blocks:
                blocks.append(b)

    out = []
    for block in blocks:
        links = block.find_all("a", href=seat)
        title_a = next((a for a in block.find_all("a", href=re.compile(r"/cinema/movie/"))
                        if clean(a.get_text())), None)
        if not links or title_a is None:
            continue
        title = clean(title_a.get_text())
        rating = rating_from_img(block.find(is_rating_img))
        for link in links:
            time = first_time(link.get_text(" "))
            if not time:
                continue
            fmt = ""
            icon = link.find("img")
            if icon is not None:   # ..._svgs_maxx.svg -> MAXX
                stem = icon.get("src", "").split("?")[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                fmt = stem.rsplit("_", 1)[-1].upper()
            out.append(Showing(title, rating, cinema["name"], time, fmt,
                               url=urljoin(cinema["url"], link["href"]),
                               film_url=urljoin(cinema["url"], title_a["href"])))
    return out


# --------------------------------------------------------------------------
# cinematimes.com (listings site; used for Odeon)
#   All days on one page: <div class="date-section" data-section-date="2026-10-21">
#   -> <div class="movie-card"> with <a class="cp-film-title">Title</a>,
#   <span class="cp-cert">PG</span> and
#   <a class="time-button" data-time="13:50" data-format="imax"
#      href="https://www.odeoncinemas.ie/ticketing/seat-picker/...">13:50 IMAX Recliner</a>
# --------------------------------------------------------------------------
def scrape_cinematimes(cinema: dict, target: dt.date, today: dt.date) -> list[Showing]:
    soup = fetch(cinema["url"])
    sections = soup.select("div.date-section[data-section-date]")
    if not sections:
        raise RuntimeError("no date sections found (site layout changed?)")
    out = []
    for section in sections:
        if section["data-section-date"] != target.isoformat():
            continue
        for card in section.select("div.movie-card"):
            title_a = card.select_one("a.cp-film-title")
            if title_a is None:
                continue
            cert = card.select_one(".cp-cert")
            rating = clean(cert.get_text()) if cert else "?"
            for link in card.select("a.time-button"):
                text = clean(link.get_text(" "))
                time = link.get("data-time") or first_time(text)
                if not time:
                    continue
                fmt = clean(TIME_RE.sub("", text))
                if not fmt and link.get("data-format", "standard") != "standard":
                    fmt = link["data-format"].upper()
                out.append(Showing(clean(title_a.get_text()), rating or "?", cinema["name"],
                                   time, fmt, url=urljoin(cinema["url"], link.get("href", "")),
                                   film_url=urljoin(cinema["url"], title_a.get("href", ""))))
    return out


SCRAPERS = {"admitone": scrape_admitone, "omniplex": scrape_omniplex,
            "cinematimes": scrape_cinematimes}


def scrape(cinema: dict, target: dt.date, today: dt.date):
    if cinema["kind"] not in SCRAPERS:
        return [], None
    try:
        shows = SCRAPERS[cinema["kind"]](cinema, target, today)
        prem = [x.upper() for x in cinema.get("premium", [])]
        for sh in shows:
            sh.premium = any(x in sh.fmt.upper() for x in prem)
        # drop test events and duplicates (sites sometimes repeat blocks)
        seen, uniq = set(), []
        for s in shows:
            key = (s.movie.lower(), s.time, s.fmt)
            if key not in seen and not IGNORE_TITLES.search(s.movie):
                seen.add(key)
                uniq.append(s)
        return uniq, None
    except Exception as e:      # keep going if one cinema fails
        return [], f"{cinema['name']}: {e}"


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
class C:
    on = sys.stdout.isatty()

    @classmethod
    def wrap(cls, s, code):
        return f"\033[{code}m{s}\033[0m" if cls.on else s


def rating_matches(rating: str, wanted: set[str]) -> bool:
    """'PG', 'PG AS LIVE' match PG; '12A (TBC)' matches 12A."""
    return re.split(r"[\s(]", rating.upper().strip(), maxsplit=1)[0] in wanted


def hyperlink(text: str, url: str) -> str:
    """OSC 8 terminal hyperlink; plain text when colours are off."""
    if not (C.on and url):
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def premium_label(fmt: str) -> str:
    """'The Gate' -> 'Gate Screen'; others as the site shows them."""
    return "Gate Screen" if "gate" in fmt.lower() else fmt


def print_table(rows: list[Showing], highlight: set[str], details: bool, urls: bool):
    headers = ["Movie", "Rating", "Cinema", "Time", "Premium"]
    headers += (["Format"] if details else []) + (["Booking link"] if urls else [])
    data = [[s.movie, s.rating, s.cinema, s.time,
             f"\u2605 {premium_label(s.fmt)}" if s.premium else ""]
            + ([s.fmt] if details else []) + ([s.url] if urls else [])
            for s in rows]
    widths = [max([len(h)] + [len(r[i]) for r in data]) for i, h in enumerate(headers)]

    def line(cells, links=None):
        out = []
        for i, (c, w) in enumerate(zip(cells, widths)):
            pad = " " * (w - len(c))
            url = (links or {}).get(i, "")
            out.append(hyperlink(c, url) + pad)   # pad outside the link
        return "  ".join(out).rstrip()

    print(C.wrap(line(headers), "1"))
    print("  ".join("-" * w for w in widths))
    for s, r in zip(rows, data):
        txt = line(r, {0: s.film_url, 3: s.url})
        if rating_matches(s.rating, highlight):
            txt = C.wrap(txt, "1;32") if C.on else txt + "   <--"
        print(txt)


SORT_KEYS = {
    "time":   lambda s: (s.time, s.movie.lower(), s.cinema),
    "movie":  lambda s: (s.movie.lower(), s.time, s.cinema),
    "cinema": lambda s: (s.cinema, s.time, s.movie.lower()),
}


def collect(location: str, target: dt.date, cinema_filter: str | None = None):
    """Fetch one location's showings for a day.

    Returns (rows, errors, skipped): all showings unfiltered, error messages for
    cinemas that failed, and names of cinemas with no listings source.
    """
    today = today_local()
    cinemas = [c for c in LOCATIONS[location]
               if not cinema_filter or cinema_filter.lower() in c["name"].lower()]
    if not cinemas:
        return [], [], []
    with ThreadPoolExecutor(max_workers=len(cinemas)) as ex:
        results = list(ex.map(lambda c: scrape(c, target, today), cinemas))
    rows = [s for shows, _ in results for s in shows]
    errors = [e for _, e in results if e]
    skipped = [c["name"] for c in cinemas if c["kind"] not in SCRAPERS]
    return rows, errors, skipped


def filter_rows(rows: list[Showing], target: dt.date, only: set[str] | None = None,
                premium: bool = False, upcoming: bool = False,
                sort: str = "time") -> list[Showing]:
    if only:
        rows = [s for s in rows if rating_matches(s.rating, only)]
    if premium:
        rows = [s for s in rows if s.premium]
    if upcoming and target == today_local():
        now = dt.datetime.now(TZ).strftime("%H:%M")
        rows = [s for s in rows if s.time >= now]
    return sorted(rows, key=SORT_KEYS[sort])


def main():
    p = argparse.ArgumentParser(description="Consolidated cinema showtimes")
    p.add_argument("location", nargs="?", default=DEFAULT_LOCATION, type=resolve_location,
                   help="cork, waterford, dublin or blanchardstown (or c, w, d, b). "
                        "Default: cork")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--date", help="YYYY-MM-DD (default: today)")
    g.add_argument("--day", type=int, default=0, help="days from today (0 = today)")
    p.add_argument("--cinema", help="only cinemas whose name contains this text")
    p.add_argument("--only", help="only these ratings, e.g. PG,G")
    p.add_argument("--highlight", default="PG", help="ratings to highlight (default: PG)")
    p.add_argument("--upcoming", action="store_true", help="hide sessions already started")
    p.add_argument("--sort", choices=list(SORT_KEYS), default="time")
    p.add_argument("--details", action="store_true", help="show the format column")
    p.add_argument("--premium", action="store_true", help="only premium screens")
    p.add_argument("--urls", action="store_true", help="print booking links as a column")
    p.add_argument("--no-color", action="store_true", help="no colours or clickable links")
    p.add_argument("--no-cache", action="store_true",
                   help=f"always fetch fresh pages (default: reuse pages up to "
                        f"{CACHE_TTL // 60} min old)")
    args = p.parse_args()

    if args.no_color:
        C.on = False
    global USE_CACHE
    USE_CACHE = not args.no_cache
    today = today_local()
    target = (dt.date.fromisoformat(args.date) if args.date
              else today + dt.timedelta(days=args.day))

    rows, errors, skipped = collect(args.location, target, args.cinema)
    if not rows and not errors and not skipped:
        sys.exit("no cinema matches --cinema")
    only = {x.strip().upper() for x in args.only.split(",")} if args.only else None
    rows = filter_rows(rows, target, only, args.premium, args.upcoming, args.sort)

    print(f"Showtimes in {args.location.title()} for {target:%A %d %B %Y}\n")
    if rows:
        print_table(rows, {x.strip().upper() for x in args.highlight.split(",")},
                    args.details, args.urls)
    else:
        print("No showtimes found.")
    if errors or skipped:
        print(file=sys.stderr)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if skipped:
        print(f"not included (no listings source yet): {', '.join(skipped)}", file=sys.stderr)


if __name__ == "__main__":
    main()
