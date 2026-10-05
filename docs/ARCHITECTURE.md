# microIndia architecture

Owner: the `tech-lead` agent. Update this file in the same change that alters a task kind, a table, a worker, an API route or a pipeline stage. Sections marked **(building)** are planned and in progress, and they move out of that state once shipped.

## Shape

```
                    ┌──────────── one Mac, one supervisor (run/local-workers.json) ─────────────┐
 Instagram  ◀─CDP─▶ │ Chrome :9222 (signed in)                                                  │
                    │   ▲            ▲                                                          │
                    │ sourcer(2 tabs) scraper(8 tabs) ─┐                                        │
                    │                                  ▼                                        │
                    │            SQLite WAL  data/microindia.sqlite3  ◀── analyzer (no browser) │
                    │              tasks · captures · creators · features · reels · dossiers    │
                    │                                  ▲            └─ ffmpeg · mlx-whisper ·   │
                    │                    api :8787 ────┘                 claude -p              │
                    │                       │  serves apps/dashboard/dist                       │
                    │ watchdog · caffeinate          │ insight plane: analyzer · backup         │
                    └───────────────────────┼───────────────────────────────────────────────────┘
                                            ▼
                                  Brand app (React/Vite)
```

## Task runtime (`apps/scraper/src/microindia_scraper/runtime/`)
- **`tasks.py` (`TaskStore`):** a single `tasks` table, unique on `(kind, key)`. Each task has a `state` of queued, leased, done, skipped, failed or auth_blocked, plus `attempts`, `run_at`, `lease_until`, a `result` JSON and `parent_task_id`. It also holds `runtime_flags` (`auth_blocked`, `network_down`, focus, pace), `runners` (heartbeats, status `starting`/`running`/`stopped`/`crashed`, and `due`: tasks the runner may take now), `usage` (counters per hour) and `metrics`.
- **`runner.py` (`Runner`):** N slots lease tasks matching `--kinds` globs. A handler with `needs_page=True` borrows a tab from the `TabPool`. Housekeeping runs the schedulers, checks the browser and sends the heartbeat, including `due`: the tasks this runner may take right now after backpressure. It is crash-only: if the browser dies the process exits and the supervisor restarts it.
  - **Nothing hangs forever.** Tab acquire, replace and recycle have timeouts. A runner never re-takes a task it still holds; if its own leases expire it knows its slots are stuck and exits. Startup (attach plus opening tabs) has a timeout and writes a `starting` heartbeat first. Any stop that can't finish ends the process after `--hard-exit-seconds` (60). After a normal stop (deploy, `/restart-worker`), the interrupted tasks get their attempt back; after a crash they don't. A long handler extends its lease to its own timeout.
  - **Offline is a pause.** A Chromium `net::ERR_INTERNET_DISCONNECTED`-class error, confirmed by a failed probe, becomes an `Offline` result. The attempt is given back, the `network_down` flag pauses every runner, and housekeeping probes `www.instagram.com:443`. When the network answers, the flag clears and the parked tasks become due at once.
- **`registry.py`:** `@handler("kind", needs_page=..., timeout_seconds=...)`. Adding a new pipeline stage takes one decorated function.
- **`results.py`:** `Done(data, follow_ups)`, `Retry(reason, after)`, `Skip`, `Fail`, `AuthBlocked`, `Offline`. Follow-ups create the next stage's tasks.
- **`browser.py`, `page.py`, `pacing.py`:** Playwright attaches over CDP, with tab recycling, same-origin `fetch_json`, login detection and a shared throttle that adapts to 429s.

## Task kinds

| Kind | Worker | Does | Queues |
|---|---|---|---|
| `source.search` | sourcer | Instagram search for one niche × city query | `scrape.profile` |
| `source.similar` | sourcer | similar accounts for a creator; the numeric user id comes from our own capture (`platform_user_id`), with Instagram search as a fallback | `scrape.profile` |
| `source.list` | sourcer | a seed file | `scrape.profile` |
| `scrape.profile` | scraper | profile header + 18 posts, saved incrementally, then eligibility | for eligible creators: `source.similar` and mentioned accounts; **(building)** `media.reel` ×15 |
| `media.reel` **(building)** | scraper | media JSON (plays, duration, audio, cover, video URL, sponsor tags), then mp4 download | `analyze.reel` |
| `analyze.reel` **(building)** | analyzer | keyframes + hook frames + audio → transcript → Claude (Sonnet) structured analysis | `analyze.creator` once 3+ reels are analysed |
| `analyze.creator` **(building)** | analyzer | all reel analyses + stats → Claude (Opus) creator dossier | — |

Schedulers: `seed_searches` and `rotate_focus` (sourcer), `refresh_eligible` (scraper, every 24h).

## Data (SQLite, WAL)
- **Immutable raw data:** `profile_captures` (one per visit), `profile_snapshots` (the payload includes `platform_user_id`, Instagram's numeric id, saved since 6 Oct), `content_snapshots` (one per post), `metric_snapshots`. Never overwritten. A later visit creates a new capture.
- **Normalised:** `creators`, `posts`, `post_observations`.
- **Derived and versioned:** `post_features`, `creator_features` (`intelligence.py`), recomputable without a browser.
- **(building) Insight layer:**
  - `reel_media`: shortcode, pk, plays, views, duration, audio, cover/video URL, sponsor tags and fetch time.
  - `reel_assets`: paths to keyframes, hook frames, opus audio and transcript JSON under `data/media/`.
  - `reel_analyses`: shortcode, `analysis_version`, `prompt_hash`, `input_hash`, model, the JSON result, the raw model output and when it was created. Rows are only ever added.
  - `creator_dossiers`: creator, `dossier_version`, the reel set used, model, JSON result, raw output and when it was created.
  - `llm_cache`: input hash → output, so identical calls are never repeated.
- **Legacy (read only by `run migrate`):** `collection_jobs`, `collection_attempts`, `account_health`, `browser_pages`, `browser_owners`, `profile_candidates`, `candidate_discoveries`, `source_cursors`, `source_rejections`. The code that wrote them was deleted on 6 Oct. Their `store.py` methods and `candidate_source.py` are next.

Rules: unknown is `null`, never `0`. Raw observations are never mutated. Every derived row carries a version.

## Capture and analysis modules
- **`e2e.py`:** profile and post extraction (`run`, `extract_profile`, `extract_content_item`, `quality_gate`).
- **`eligibility.py`:** India evidence, human vs brand/publisher, AI persona, repost pages.
- **`intelligence.py`:** post and creator features, rule-based reel text analysis, AI-persona evidence.
- **`insights.py`, `brand.py`:** engagement against peers, cadence, formats, sponsored lift, hashtags, fit score, brand signals.
- **`niches.py`:** the niche taxonomy (36 niches) and cities. **The single source of truth for categories.**
- **`constants.py`:** follower bands and limits: the cohort band, scrape slack, `DISPLAY_BANDS` (peer comparison) and `EXPLORATION_BANDS` (sourcing focus). **The single source of truth for bands.**
- **(building) `analysis/`:** `media.py` (ffmpeg), `transcribe.py` (mlx-whisper), `reel.py` (handler + prompt), `creator.py` (dossier), `schemas/*.json`, `prompts/*.md`.
- **(building) `llm.py`:** the only way to call a model. Runs `claude -p --bare --output-format json --json-schema …`, with images sent through `--input-format stream-json`. Has a timeout, retry, a concurrency limit, the `llm_cache` and usage logging.

## API (`api.py`, port 8787)
- **GET:** `/api/summary`, `/api/timeseries`, `/api/activity`, `/api/creators` (filters, CSV), `/api/creators/<handle>`, `/api/pipeline`, `/api/stats`, `/api/sources`, `/api/events`.
- **POST:** `/api/actions/{unblock,retry,seed}`, `/api/search/chat`, `/api/assistant`.
- An in-memory creator index rebuilds in the background when the data changes (5–15 s for about 7K creators).
- **(building):** dossiers and reel analyses on `/api/creators/<handle>`, plus `/api/brief`, `/api/ask`, `/api/compare`, `/api/shortlists`, and a split into an `api/` package.

## App (`apps/dashboard`)
- Vite + React 18 + TanStack Query + Tailwind 4. Polls `/api/activity` every 3 s and `/api/summary` every 6 s.
- Today's pages: Live, Find, Creators, Creator, Pipeline, System.
- **(building):** Brief, Discover, Dossier, Compare and Shortlists for brands. Live, Pipeline and System move to `/ops`.

## Operations
There are two supervisor **planes**, so insight work can be redeployed without touching collection. Each has its own config, state and lock, and the watchdog and `/restart-worker` see both.

| Plane | Config | Workers |
|---|---|---|
| collection | `run/local-workers.json` | keep-awake, chrome, sourcer, scraper, api, watchdog |
| insight | `run/insight-workers.json` | backup, **(building)** analyzer |

- **`local_supervisor.py`:** starts and restarts every worker in its config, which it reads once at start. `run/install-launch-agent.sh` installs one launch agent per plane (`com.microindia.scraper`, `com.microindia.insights`). `run/start-public-collection.sh` starts both.
- **`watchdog.py`:** judges each runner on its own kinds, using its reported `due` and its newest finished task. It also flags tasks a runner holds past their lease. Its stall and lease checks pause while the session is signed out or the network is down; a stale heartbeat still restarts a runner. Its lease grace (300s) is longer than the runner's own stuck check, so a runner exits by itself first. It also closes hung tabs and writes `run/health.json`.
- **`backup.py`:** an online SQLite backup once a day to `data/backups/microindia-YYYY-MM-DD.sqlite3`, checked with `quick_check`, keeping 7. One-off backups with other names are never pruned.
- **`status.py`:** a read-only snapshot for `/status`, the session hook and `docs/STATE.md`.
- **`run requeue --offline`:** gives failed tasks a fresh start.
- **Logs:** one JSON line per event in `run/<worker>.log`.
