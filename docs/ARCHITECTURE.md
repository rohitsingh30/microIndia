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
| `scrape.profile` | scraper | profile header + 18 posts, saved incrementally, then eligibility | for eligible creators: `source.similar`, mentioned accounts, and `media.reel` for the selected reels (`analysis/selection.py`; brand-ready creators first (priority −6 vs −10), then the most recently kept, one creator at a time). Each worker leases only its own kinds, so these priorities order media work only; media competes with collection just for the pacer and Chrome |
| `media.reel` | media (insight plane, browser over CDP; **(building)** not deployed yet) | Held (gate in `run.py`, rechecked in the handler as a refunded retry) while more than 30 `analyze.reel` are queued or leased, `data/media/video` is over 2 GB, or the disk has under 20 GB free. `GET /api/v1/media/<pk>/info/` from a signed-in tab → `reel_media` (plays, likes, duration, audio, cover/video URL, paid-partnership, sponsor tags, co-authors, usertags, location, caption); mp4 by plain HTTP GET of the signed CDN URL (in-page fetch as fallback) to `data/media/video/`. A reel already analysed by the current prompt only gets its metrics refreshed. 404 / "not found" → Skip; "wait a few minutes" / `feedback_required` → refunded retry; 5xx, status 0, other 4xx → Retry | `analyze.reel` |
| `analyze.reel` | analyzer (no browser; **(building)** worker not deployed yet) | ffmpeg hook frames (0–3 s) + ≤8 scene keyframes + opus → mlx-whisper transcript → Claude Sonnet with `schemas/reel.json` → `reel_analyses`. The mp4 is deleted as soon as frames, opus and transcript are saved, and on every terminal Fail. Claude unavailable (limit, overload, signed out, network) → refunded retry in 15–30 min | `analyze.creator` (priority = reel + 1) when the dossier is due and no reel of the creator is still pending. Schedulers on the analyzer: a dossier sweep every 10 min (`ops.queue_due_dossiers`, which catches reels finishing together or a skipped last reel) and an hourly sweep of mp4s older than 24 h that no `analyze.reel` will read |
| `analyze.creator` | analyzer | current reel analyses + stats (engagement vs peers, cadence from media ids, plays, sponsored vs organic) → Claude Opus with `schemas/creator.json` → `creator_dossiers`. Due when there is no dossier at the current prompt version, or the reel set changed with 3+ new analyses, or it changed and the last dossier is 7+ days old (daily re-scrapes never cause daily Opus runs) | — |

Schedulers: `seed_searches` and `rotate_focus` (sourcer), `refresh_eligible` (scraper, every 24h).

## Data (SQLite, WAL)
- **Immutable raw data:** `profile_captures` (one per visit), `profile_snapshots` (the payload includes `platform_user_id`, Instagram's numeric id, saved since 6 Oct), `content_snapshots` (one per post), `metric_snapshots`. Never overwritten. A later visit creates a new capture.
- **Normalised:** `creators`, `posts`, `post_observations`.
- **Derived and versioned:** `post_features`, `creator_features` (`intelligence.py`), recomputable without a browser. Rows carry `intelligence.FEATURE_VERSION` (`features-v2` since 6 Oct: languages, topics and CTAs come only from the caption, never the page body with its footer language list; topic terms match whole words). Older rows are `features-v1`/capture-schema labels and are recomputed by `backfill_reel_analysis` (reels) or re-materialising a capture.
- **Insight layer** (`analysis/db.py`, additive `CREATE TABLE IF NOT EXISTS`):
  - `reel_media`: one row **per fetch** (metrics change): shortcode, pk, owner, creator, `taken_at`, duration, `play_count`, `ig_play_count`, `view_count` (null on reels today), likes, comments, `has_audio`, `audio_type` + `audio_json` (track or original sound), cover/video URL, size, `is_paid_partnership`, `sponsor_tags`, `coauthors`, `usertags`, location, caption, caption language, the raw item (minus the DASH manifest and liker lists) and `fetched_at`.
  - `reel_assets`: one row per reel: keyframe and hook-frame lists (id, time, path), opus path, transcript path and language, `speech_detected`, the mp4 path while it exists, and per-step seconds.
  - `reel_analyses`: append-only: shortcode, creator, `analysis_version` (the prompt's `version:`), `prompt_hash` (prompt + schema), `input_hash` (the `llm_cache` key: model, system, prompt, schema, image bytes), model id, result JSON, raw CLI envelope, seconds.
  - `creator_dossiers`: append-only: creator, `dossier_version`, `prompt_hash`, `input_hash`, `reel_set` + `reel_set_hash`, model id, result JSON, raw envelope, seconds.
  - `llm_cache` (`llm.py`): input hash → parsed output + raw envelope.
- **Media files** (`data/media/`, git-ignored): `frames/<shortcode>/H0–H3.jpg, K1–K8.jpg` (longest side 768 px), `audio/<shortcode>/audio.opus` (mono 32 kbps), `transcripts/<shortcode>/transcript.json` (segments with whisper confidences), `video/<shortcode>.mp4` only until analysed.
- **Legacy (read only by `run migrate`):** `collection_jobs`, `collection_attempts`, `account_health`, `browser_pages`, `browser_owners`, `profile_candidates`, `candidate_discoveries`, `source_cursors`, `source_rejections`. The code that wrote them was deleted on 6 Oct. Their `store.py` methods and `candidate_source.py` are next.

Rules: unknown is `null`, never `0`. Raw observations are never mutated. Every derived row carries a version.

## Capture and analysis modules
- **`e2e.py`:** profile and post extraction (`run`, `extract_profile`, `extract_content_item`, `quality_gate`).
- **`eligibility.py`:** India evidence, human vs brand/publisher, AI persona, repost pages.
- **`intelligence.py`:** post and creator features, rule-based reel text analysis, AI-persona evidence.
- **`insights.py`, `brand.py`:** engagement against peers, cadence, formats, sponsored lift, hashtags, fit score, brand signals.
- **`niches.py`:** the niche taxonomy (36 niches) and cities. **The single source of truth for categories.**
- **`constants.py`:** follower bands and limits: the cohort band, scrape slack, `DISPLAY_BANDS` (peer comparison) and `EXPLORATION_BANDS` (sourcing focus). **The single source of truth for bands.**
- **`analysis/`** (the insight engine; importing it registers `media.reel`, `analyze.reel`, `analyze.creator`):
  - `media.py`: shortcode ↔ pk (base64 alphabet; ids embed the upload time, used for recency and cadence), media paths, ffmpeg (scene keyframes, hook frames, wav for whisper, opus to keep).
  - `transcribe.py`: mlx-whisper `large-v3-turbo` (one at a time per process). Each segment keeps whisper's scores and a `speech` flag; music guesses are marked unsure, and the prompt never treats them as the creator's words.
  - `selection.py`: ≥15 reels per creator (8 most recent by media id, 4 best by likes+comments vs median, every sponsored/collab reel, topped up with recent) from every capture plus `reel_media` flags; `reel_follow_ups` for `scrape.profile`.
  - `fetch.py` (`media.reel`), `reel.py` (`analyze.reel`, prompt building, evidence checks), `creator.py` (`analyze.creator`, stats), `db.py` (tables), `spec.py` (prompt + schema loading and hashing; `fence()` wraps captions, transcripts, bios and analyses as data the prompts say never to obey), `ops.py` (media gate, disk stop, sweeps, refunded retries), `evals.py` (golden-set scoring), `cli.py`.
  - `prompts/reel.md`, `prompts/creator.md` (first line `version:`), `schemas/reel.json`, `schemas/creator.json` (niche enums = `niches.NICHES`). Every claim cites evidence: `H0–H3`/`K1–K8` frames, `T<start>-<end>` transcript times, `caption`, `meta:<field>`, or reel shortcodes in dossiers; unknowns go to an `unknowns` list with a reason. Invalid references are counted by `/eval-insights`.
  - CLI: `python -m microindia_scraper.analysis reel <permalink|shortcode> [--force] | creator <handle> [--force] | eval [reels|creators|businesses|all] | local-search` (stub until the Local finder). `reel`/`creator` open their own tab on Chrome only when a reel has no cached media, and close it after.
- **`llm.py`:** the only way to call a model: `claude -p --model sonnet|opus` with `--tools "" --setting-sources "" --strict-mcp-config --disable-slash-commands --no-session-persistence --system-prompt …` from a temp cwd (`--bare` is added only when `ANTHROPIC_API_KEY` is set, because `--bare` never uses the subscription login). Text: `--output-format json`; structured output arrives in the envelope's `structured_output`. Images: `--input-format stream-json` (requires `--output-format stream-json --verbose`), base64 image blocks, result in the last `type: result` line. Every call takes a `deadline` (waits and attempts are cut to fit; no attempt starts with under 60 s left) and runs in its own process group with `DISABLE_AUTOUPDATER=1`, killed whole on timeout. One retry, except when Claude is unavailable: that raises `LLMUnavailable` and sets `runtime_flags.llm_cooldown_until` (20 min) so later calls fail fast. A schema call without the schema's object is an error and is never cached. `MICROINDIA_LLM_SLOTS` (default 2) flock slots across processes (brand chat waits at most 5 s and doesn't retry), `llm_cache`, and `usage` rows `llm_calls`, `llm_seconds`, `llm_failures`, `llm_cache_hits`, `llm_cost_usd` per hour and per day. Brand chat (`search_ai`, `assistant`) uses it only with `MICROINDIA_LLM=on`, otherwise the rules/retrieval fallback.

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
| insight | `run/insight-workers.json` | backup, **(building)** analyzer (`run work --kinds 'analyze.*' --tabs 2`), **(building)** media (`run work --kinds 'media.*' --tabs 1`: a browser worker that attaches to the collection plane's Chrome over CDP and opens its own tab) |

- **`local_supervisor.py`:** starts and restarts every worker in its config, which it reads once at start. `run/install-launch-agent.sh` installs one launch agent per plane (`com.microindia.scraper`, `com.microindia.insights`). `run/start-public-collection.sh` starts both.
- **`watchdog.py`:** judges each runner on its own kinds, using its reported `due` and its newest finished task. It also flags tasks a runner holds past their lease. Its stall and lease checks pause while the session is signed out or the network is down; a stale heartbeat still restarts a runner. Its lease grace (300s) is longer than the runner's own stuck check, so a runner exits by itself first. It also closes hung tabs and writes `run/health.json`.
- **`backup.py`:** an online SQLite backup once a day to `data/backups/microindia-YYYY-MM-DD.sqlite3`, checked with `quick_check`, keeping 7. One-off backups with other names are never pruned.
- **`status.py`:** a read-only snapshot for `/status`, the session hook and `docs/STATE.md`.
- **`run requeue --offline`:** gives failed tasks a fresh start.
- **Logs:** one JSON line per event in `run/<worker>.log`.
