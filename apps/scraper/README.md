# apps/scraper

The Python side of microIndia covers:
- the browser-task runtime (sourcing and scraping),
- the SQLite store,
- the insight engine (reel and creator analysis),
- the API that serves the brand app.

Full picture: `../../docs/ARCHITECTURE.md`. Rules: `CLAUDE.md`.

## Setup (once)

```bash
python3.12 -m venv .venv && .venv/bin/pip install playwright mlx-whisper
brew install ffmpeg                      # keyframes and audio for reel analysis
cp run/local-workers.example.json run/local-workers.json
```

Chrome 136+ refuses remote debugging on the default profile, so the supervisor launches a copied, signed-in profile (`~/Library/Application Support/Google/Chrome-cdp-profile2`, `Profile 2`) with `--remote-debugging-port=9222`. If Instagram asks for a login or verification, complete it in that Chrome window and every runner resumes on its own (`run unblock` forces it).

## Run

```bash
./run/start-public-collection.sh --background   # supervisor → every worker in run/local-workers.json
bash run/install-launch-agent.sh                # optional: start at login
PYTHONPATH=src .venv/bin/python -m microindia_scraper.status          # health in 5 lines
PYTHONPATH=src .venv/bin/python -m microindia_scraper.local_supervisor --state run/local-supervisor-state.json --status
```

Workers (see `run/local-workers.example.json`):

| Worker | Command | Does |
|---|---|---|
| `chrome` | Chrome on :9222 | the signed-in session |
| `sourcer` | `run work --kinds 'source.*' --tabs 2` | search, similar accounts, seed lists |
| `scraper` | `run work --kinds 'scrape.*' --tabs 8` | profile + 18 posts, eligibility |
| `api` | `api --port 8787` | JSON API + serves `apps/dashboard/dist` |
| `watchdog` | `watchdog` | hung tabs, stalled workers, `run/health.json` |
| `keep-awake` | `caffeinate -ims` | the Mac never sleeps |

## Task runtime

Workers lease tasks from a single `tasks` table. A new stage is a single `@handler("kind")` function that returns `Done`, `Retry`, `Skip`, `Fail` or `AuthBlocked`. The next stage is queued as a `FollowUp`.

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run work --kinds 'scrape.*' --tabs 8 --name scraper
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run add source.list seeds.txt     # usernames, URLs or NDJSON
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run add scrape.profile some_handle
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run status
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run unblock
```

| Kind | Does |
|---|---|
| `source.search` | Instagram search for one India niche × city query, which queues `scrape.profile` |
| `source.similar` | similar accounts for a kept creator |
| `source.list` | a file of usernames, profile URLs or NDJSON |
| `scrape.profile` | full capture plus eligibility. Kept creators queue `source.similar` and every account they mention. Re-scraped every 24h. |

## Capture contract
- One `profile_captures` row per visit. The header is saved as soon as it is parsed, and each of the 18 recent posts is saved independently, so an interrupted capture keeps everything it got.
- Metrics use the latest 12 non-pinned posts. Unknown values stay `null`.
- Later visits create new captures and never overwrite old ones.
- **Kept** means a public, India-relevant, human creator in the follower band in `constants.py`. Brands, publishers, AI personas and repost pages are rejected (`eligibility.py`).

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```
