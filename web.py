#!/usr/bin/env python3
"""
web.py - the showtimes table as a web page.

    python3 web.py                # serves http://0.0.0.0:8080
    PORT=9000 python3 web.py

Query parameters (all optional, so links can be bookmarked):
    loc=cork|waterford|dublin|blanchardstown (or c, w, d, b)
    day=0..6        days from today
    all=1           include sessions that already started (today only)
    rating=family   only G and PG
    premium=1       only premium screens
    sort=time|movie|cinema
"""
from __future__ import annotations

import datetime as dt
import html
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import showtimes as S

HIGHLIGHT = {"PG"}
FAMILY = {"G", "PG"}
DAYS_AHEAD = 7
LOCATION_ORDER = ["cork", "waterford", "dublin", "blanchardstown"]

# One fetch per location+day at a time; other visitors wait for it and then
# hit the cache, rather than all scraping the cinema sites at once.
_fetch_locks: dict[tuple, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def day_label(d: dt.date, today: dt.date) -> str:
    if d == today:
        return "Today"
    if d == today + dt.timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a %-d")


def parse_params(qs: str) -> dict:
    q = {k: v[-1] for k, v in parse_qs(qs).items()}
    try:
        loc = S.resolve_location(q.get("loc", S.DEFAULT_LOCATION))
    except Exception:
        loc = S.DEFAULT_LOCATION
    try:
        day = min(max(int(q.get("day", 0)), 0), DAYS_AHEAD - 1)
    except ValueError:
        day = 0
    sort = q.get("sort", "time")
    return {
        "loc": loc,
        "day": day,
        "all": q.get("all") == "1",
        "rating": "family" if q.get("rating") == "family" else "",
        "premium": q.get("premium") == "1",
        "sort": sort if sort in S.SORT_KEYS else "time",
    }


def link(p: dict, **changes) -> str:
    """URL for the current view with some parameters changed."""
    merged = {**p, **changes}
    out = {"loc": merged["loc"]}
    if merged["day"]:
        out["day"] = merged["day"]
    if merged["all"]:
        out["all"] = 1
    if merged["rating"]:
        out["rating"] = merged["rating"]
    if merged["premium"]:
        out["premium"] = 1
    if merged["sort"] != "time":
        out["sort"] = merged["sort"]
    return "/?" + urlencode(out)


def get_rows(loc: str, target: dt.date):
    key = (loc, target)
    with _fetch_locks_guard:
        lock = _fetch_locks.setdefault(key, threading.Lock())
    with lock:
        return S.collect(loc, target)


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e4e2dc;
--accent:#b4232c;--pg:#e6f4ea;--pg-ink:#1e6b34;--pg-line:#3f9a5a;--star:#b7791f;--chip:#efede7}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--ink:#ececee;--mute:#9a9aa2;
--line:#2e2e33;--accent:#ff6b6b;--pg:#16301f;--pg-ink:#8fe0a8;--pg-line:#3f9a5a;--star:#f2c14e;--chip:#26262b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit}
.wrap{max-width:880px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 2px;letter-spacing:-.01em}
.sub{color:var(--mute);margin:0 0 14px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
nav a{padding:6px 12px;border-radius:999px;background:var(--chip);text-decoration:none;
font-weight:500;white-space:nowrap}
nav a.on{background:var(--ink);color:var(--bg)}
nav.days a{font-weight:400;font-size:14px;padding:5px 10px}
form{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:10px 0 14px;
color:var(--mute);font-size:14px}
form label{display:flex;gap:6px;align-items:center;cursor:pointer}
select{font:inherit;padding:3px 6px;border-radius:6px;border:1px solid var(--line);
background:var(--card);color:var(--ink)}
.list{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.row{display:grid;grid-template-columns:62px 1fr auto;gap:4px 12px;align-items:center;
padding:10px 14px;border-top:1px solid var(--line)}
.row:first-child{border-top:0}
.row.pg{background:var(--pg);box-shadow:inset 4px 0 0 var(--pg-line)}
.time{font-variant-numeric:tabular-nums;font-weight:600;font-size:16px;text-decoration:none}
.time:hover,.title:hover{text-decoration:underline}
.title{font-weight:600;text-decoration:none}
.meta{color:var(--mute);font-size:13px;margin-top:1px}
.prem{color:var(--star);font-weight:600}
.rating{font-size:12px;font-weight:700;padding:2px 7px;border-radius:6px;
border:1px solid var(--line);min-width:38px;text-align:center}
.row.pg .rating{border-color:var(--pg-line);color:var(--pg-ink)}
.empty{padding:28px;text-align:center;color:var(--mute)}
.notes{color:var(--mute);font-size:13px;margin-top:12px}
.notes p{margin:4px 0}
.err{color:var(--accent)}
#loading{display:none;position:fixed;inset:0;background:rgba(0,0,0,.25);
align-items:center;justify-content:center;font-weight:600;color:#fff}
body.busy #loading{display:flex}
@media (max-width:520px){.row{grid-template-columns:52px 1fr auto;padding:9px 12px}}
"""

JS = """
document.addEventListener('click',e=>{const a=e.target.closest('nav a');
if(a&&!e.ctrlKey&&!e.metaKey)document.body.classList.add('busy')});
document.querySelectorAll('form [data-auto]').forEach(el=>el.addEventListener('change',()=>{
document.body.classList.add('busy');el.form.submit()}));
window.addEventListener('pageshow',()=>document.body.classList.remove('busy'));
"""


def render(p: dict) -> str:
    today = S.today_local()
    target = today + dt.timedelta(days=p["day"])
    rows, errors, skipped = get_rows(p["loc"], target)
    shown = S.filter_rows(rows, target,
                          only=FAMILY if p["rating"] == "family" else None,
                          premium=p["premium"], upcoming=not p["all"],
                          sort=p["sort"])

    locs = "".join(
        f'<a href="{esc(link(p, loc=l))}" class="{"on" if l == p["loc"] else ""}">'
        f'{esc(l.title())}</a>' for l in LOCATION_ORDER)
    days = "".join(
        f'<a href="{esc(link(p, day=i))}" class="{"on" if i == p["day"] else ""}">'
        f'{esc(day_label(today + dt.timedelta(days=i), today))}</a>'
        for i in range(DAYS_AHEAD))

    hidden_started = ""
    if p["day"] == 0:
        hidden_started = (
            f'<label><input type="checkbox" name="all" value="1" data-auto '
            f'{"checked" if p["all"] else ""}> Include started</label>')
    sorts = "".join(f'<option value="{k}" {"selected" if k == p["sort"] else ""}>'
                    f'{k.title()}</option>' for k in S.SORT_KEYS)
    form = f"""<form method="get" action="/">
<input type="hidden" name="loc" value="{esc(p['loc'])}">
<input type="hidden" name="day" value="{p['day']}">
{hidden_started}
<label><input type="checkbox" name="rating" value="family" data-auto
 {"checked" if p["rating"] else ""}> G &amp; PG only</label>
<label><input type="checkbox" name="premium" value="1" data-auto
 {"checked" if p["premium"] else ""}> Premium only</label>
<label>Sort <select name="sort" data-auto>{sorts}</select></label>
<noscript><button>Apply</button></noscript>
</form>"""

    items = []
    for s in shown:
        pg = S.rating_matches(s.rating, HIGHLIGHT)
        meta = [esc(s.cinema)]
        if s.premium:
            meta.append(f'<span class="prem">★ {esc(S.premium_label(s.fmt))}</span>')
        elif s.fmt:
            meta.append(esc(s.fmt))
        time_html = (f'<a class="time" href="{esc(s.url)}" target="_blank" rel="noopener" '
                     f'title="Book">{esc(s.time)}</a>' if s.url
                     else f'<span class="time">{esc(s.time)}</span>')
        title_html = (f'<a class="title" href="{esc(s.film_url)}" target="_blank" '
                      f'rel="noopener">{esc(s.movie)}</a>' if s.film_url
                      else f'<span class="title">{esc(s.movie)}</span>')
        items.append(
            f'<div class="row{" pg" if pg else ""}">{time_html}'
            f'<div>{title_html}<div class="meta">{" · ".join(meta)}</div></div>'
            f'<span class="rating">{esc(s.rating)}</span></div>')
    if not items:
        msg = "No showtimes found."
        if rows and not p["all"] and p["day"] == 0:
            msg = "Nothing left today with these filters. Try tomorrow."
        items.append(f'<div class="empty">{esc(msg)}</div>')

    notes = []
    for e in errors:
        notes.append(f'<p class="err">Couldn\'t load {esc(e)}</p>')
    if skipped:
        notes.append(f'<p>Not included (no listings source yet): {esc(", ".join(skipped))}</p>')
    notes.append(f"<p>{len(shown)} showings. PG highlighted in green, ★ = premium screen. "
                 f"Tap a time to book. Listings are refreshed every "
                 f"{S.CACHE_TTL // 60} minutes.</p>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Showtimes · {esc(p['loc'].title())}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎬</text></svg>">
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>Showtimes in {esc(p['loc'].title())}</h1>
<p class="sub">{esc(target.strftime('%A %-d %B'))}</p>
<nav>{locs}</nav>
<nav class="days">{days}</nav>
{form}
<div class="list">{''.join(items)}</div>
<div class="notes">{''.join(notes)}</div>
</div><div id="loading">Loading…</div>
<script>{JS}</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "showtimes"

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/healthz":
            return self.send(200, "ok", "text/plain")
        if url.path != "/":
            return self.send(404, "not found", "text/plain")
        try:
            body = render(parse_params(url.query))
        except Exception as e:  # never leave the visitor with a blank page
            print(f"render failed: {e!r}", file=sys.stderr)
            return self.send(500, f"Something went wrong: {html.escape(str(e))}",
                             "text/html")
        self.send(200, body, "text/html")

    def send(self, code: int, body: str, ctype: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/healthz"):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving on http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
