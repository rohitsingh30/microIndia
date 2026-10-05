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
                    │ watchdog · caffeinate · backup                                            │
                    └───────────────────────┼───────────────────────────────────────────────────┘
                                            ▼
                                  Brand app (React/Vite)
```

## Task runtime (`apps/scraper/src/microindia_scraper/runtime/`)
- **`tasks.py` (`TaskStore`):** a single `tasks` table, unique on `(kind, key)`. Each task has a `state` of queued, leased, done, skipped, failed or auth_blocked, plus `attempts`, `run_at`, `lease_until`, a `result` JSON and `parent_task_id`. It also holds `runtime_flags` (auth pause, focus, pace), `runners` (heartbeats), `usage` (counters per hour) and `metrics`.
- **`runner.py` (`Runner`):** N slots lease tasks matching `--kinds` globs. A handler with `needs_page=True` borrows a tab from the `TabPool`. Housekeeping runs the schedulers, checks the browser and sends the heartbeat. It is crash-only: if the browser dies the process exits and the supervisor restarts it.
- **`registry.py`:** `@handler("kind", needs_page=..., timeout_seconds=...)`. Adding a new pipeline stage takes one decorated function.
- **`results.py`:** `Done(data, follow_ups)`, `Retry(reason, after)`, `Skip`, `Fail`, `AuthBlocked`. Follow-ups create the next stage's tasks.
- **`browser.py`, `page.py`, `pacing.py`:** Playwright attaches over CDP, with tab recycling, same-origin `fetch_json`, login detection and a shared throttle that adapts to 429s.

## Task kinds

| Kind | Worker | Does | Queues |
|---|---|---|---|
| `source.search` | sourcer | Instagram search for one niche × city query | `scrape.profile` |
| `source.similar` | sourcer | similar accounts for a creator | `scrape.profile` |
| `source.list` | sourcer | a seed file | `scrape.profile` |
| `scrape.profile` | scraper | profile header + 18 posts, saved incrementally, then eligibility | for eligible creators: `source.similar` and mentioned accounts; **(building)** `media.reel` ×15 |
| `media.reel` **(building)** | scraper | media JSON (plays, duration, audio, cover, video URL, sponsor tags), then mp4 download | `analyze.reel` |
| `analyze.reel` **(building)** | analyzer | keyframes + hook frames + audio → transcript → Claude (Sonnet) structured analysis | `analyze.creator` once 3+ reels are analysed |
| `analyze.creator` **(building)** | analyzer | all reel analyses + stats → Claude (Opus) creator dossier | — |

Schedulers: `seed_searches` and `rotate_focus` (sourcer), `refresh_eligible` (scraper, every 24h).

## Data (SQLite, WAL)
- **Immutable raw data:** `profile_captures` (one per visit), `profile_snapshots`, `content_snapshots` (one per post), `metric_snapshots`. Never overwritten. A later visit creates a new capture.
- **Normalised:** `creators`, `posts`, `post_observations`.
- **Derived and versioned:** `post_features`, `creator_features` (`intelligence.py`), recomputable without a browser.
- **(building) Insight layer:**
  - `reel_media`: shortcode, pk, plays, views, duration, audio, cover/video URL, sponsor tags and fetch time.
  - `reel_assets`: paths to keyframes, hook frames, opus audio and transcript JSON under `data/media/`.
  - `reel_analyses`: shortcode, `analysis_version`, `prompt_hash`, `input_hash`, model, the JSON result, the raw model output and when it was created. Rows are only ever added.
  - `creator_dossiers`: creator, `dossier_version`, the reel set used, model, JSON result, raw output and when it was created.
  - `llm_cache`: input hash → output, so identical calls are never repeated.
- **Legacy (read only by `run migrate`):** `collection_jobs`, `collection_attempts`, `account_health`, `browser_pages`, `browser_owners`, `profile_candidates`, `candidate_discoveries`, `source_cursors`, `source_rejections`.

Rules: unknown is `null`, never `0`. Raw observations are never mutated. Every derived row carries a version.

## Capture and analysis modules
- **`e2e.py`:** profile and post extraction (`run`, `extract_profile`, `extract_content_item`, `quality_gate`).
- **`eligibility.py`:** India evidence, human vs brand/publisher, AI persona, repost pages.
- **`intelligence.py`:** post and creator features, rule-based reel text analysis, AI-persona evidence.
- **`insights.py`, `brand.py`:** engagement against peers, cadence, formats, sponsored lift, hashtags, fit score, brand signals.
- **`niches.py`:** the niche taxonomy (36 niches) and cities. **The single source of truth for categories.**
- **`constants.py`:** follower bands and limits. **The single source of truth for bands.**
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
- **`local_supervisor.py`:** starts and restarts every worker in `run/local-workers.json`. A launch agent starts it at login.
- **`watchdog.py`:** closes hung tabs, restarts a worker with a stale heartbeat or a stalled queue, and writes `run/health.json`.
- **(building) backup worker:** nightly `sqlite3 .backup` to `data/backups/`, keeping 7.
- **Logs:** one JSON line per event in `run/<worker>.log`.
