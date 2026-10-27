# showtimes

Today's showtimes for every cinema in Cork, Waterford, Dublin or Blanchardstown in one table,
with PG films highlighted, premium screens (IMAX, MAXX, PURE, Gate Screen...) starred, and
links straight to booking.

## Command line

```bash
pip install -r requirements.txt
python3 showtimes.py              # Cork, today
python3 showtimes.py d --day 1    # Dublin, tomorrow
python3 showtimes.py --help
```

## Web page

```bash
python3 web.py                    # http://localhost:8080
```

Pushes to `main` build `ghcr.io/jak0lantash/showtimes:sha-<commit>`, which the homelab repo
deploys to the NAS.

## Sources

The Reel Picture and Arc Cinema sites, omniplex.ie, and cinematimes.com (for Odeon, IMC,
Cineworld, Vue and the Dublin cinemas). Requests to each site are throttled and pages are
cached for 15 minutes.
