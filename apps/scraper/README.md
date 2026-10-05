# Local dataset scraper

This package provides a local SQLite capture/checkpoint layer, bounded collection
queue primitives, normalized creator/post records, explainable offline features,
and a read-only creator explorer. Raw observations remain immutable and derived
features can be recomputed without another browser visit.

## Task runtime (sourcer + scraper)

One signed-in Chrome runs with `--remote-debugging-port=9222`. Runners attach to
it over CDP (Playwright), each with its own tabs, and lease work from one
`tasks` table. The sourcer and the scraper are task kinds on that runtime:

| Kind | Does | Queues |
| --- | --- | --- |
| `source.search` | Instagram search for one India niche query | `scrape.profile` per public user |
| `source.similar` | Instagram's similar accounts for a creator | `scrape.profile` per account |
| `source.list` | a file of usernames / profile URLs / NDJSON | `scrape.profile` per row |
| `scrape.profile` | full profile + post capture, eligibility | for eligible creators: `source.similar` and every mentioned account |

```
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run work --kinds 'source.*' --tabs 2
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run work --kinds 'scrape.*' --tabs 6
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run add source.list seeds.txt
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run status
PYTHONPATH=src .venv/bin/python -m microindia_scraper.run migrate   # once: import old captures/jobs
```

A login or challenge page pauses every runner; they resume by themselves once
the session works again (`run unblock` forces it). Eligible creators are
re-scraped every 24h and the search grid reruns weekly. A new source or scraper
is one `@handler("kind")` function in `handlers/`.

## Capture contract

- One `ProfileCapture` is created per profile visit.
- The profile header is saved immediately after parsing.
- Each of the 18 recent content observations is saved independently.
- Metrics use the latest 12 eligible, non-pinned persisted observations.
- Interrupted captures remain resumable and retain all successful observations.
- Later visits create new immutable captures.
- Profile research records counts, bio, public external link, category, location,
  language signals, account type, and commercial-signal flags when available.
- Each content observation records caption text, hashtags, mentions, published
  time, location, pinned/collaboration flags, likes, comments, views, and metric
  availability when the public page exposes them.
- AI evidence is extracted only from public text and metadata, with per-item signals
  and creator-level aggregates; missing evidence remains `unknown`.
- Captures with explicit AI-dominant profile signals or repeated high-confidence AI
  evidence are quarantined while their raw snapshots and derived features are retained.

## India human micro-creator cohort

The pilot is intentionally **not** a general Instagram scraper. It targets public,
India-relevant human creators in the 10,000–100,000 follower band. Profiles are
rejected or quarantined when they look like a brand, publisher, institution,
agency, restaurant, company channel, private account, or non-India account.

Candidate supply comes from an approved public-profile index/provider export.
An authenticated topsearch adapter remains optional and low-volume; Reels,
Home, hashtag, and feed links are not candidate sources. Each NDJSON record
must contain `platform=instagram`, a canonical one-segment `profile_url`,
`username`, literal `is_private=false`, `source_name`, `source_record_key`, and
`observed_at`; optional follower, category, country, and niche metadata is
retained for prioritization and review. Invalid, private, ambiguous, duplicate,
or reserved-route records are rejected before a capture job is created.

The live profile remains the source of truth: follower count, public status,
India/location/language signals, account type, and human/organization evidence
are rechecked during capture. Only snapshots passing the existing eligibility
policy count toward the cohort target.

## Local development

The current machine has Python 3.9, so the foundation uses only the standard
library and can be tested immediately. The optional Browser Use integration
requires upgrading to Python 3.10+ as required by the Browser Use project.

Run the tests from this directory:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## End-to-end local Chrome run

The E2E runner requires an explicit canonical profile URL. It never chooses a
random profile, opens Explore, or falls back to a built-in list:

```bash
.venv/bin/python -m microindia_scraper.e2e https://www.instagram.com/example/
```

The runner saves the profile header and every content observation incrementally
into `data/microindia.sqlite3`. The dispatcher performs the required
discovery-page public preflight before this runner is called; the runner also
stops after the profile snapshot if the account has become private or returned a
login/challenge response.

## Local dataset dashboard

Start the read-only review UI:

```bash
.venv/bin/python -m microindia_scraper.ui
```

Open `http://127.0.0.1:8787`. The dashboard reads SQLite on every request, so
new captures and incremental content saves become visible after refreshing.

Open `http://127.0.0.1:8787/creators` for normalized creator discovery filters.

## Local worker supervisor

The local supervisor manages explicit long-running commands, restarts crashed
workers, and writes restart/health state to `run/local-supervisor-state.json`.
The easiest foreground launch is:

```bash
./run/start-public-collection.sh
```

With no source configured, the collector remains paused:

```bash
./run/start-public-collection.sh
```

Use an approved, validated NDJSON source explicitly:

```bash
./run/start-public-collection.sh /absolute/path/approved-public-profiles.ndjson
```

PowerShell is also supported:

```powershell
./run/start-public-collection.ps1 -CandidateSource /absolute/path/approved-public-profiles.ndjson
```

To leave it running after closing the terminal, add `--background` to the
POSIX launcher or `-Background` to the PowerShell launcher. Missing sources
remain paused; launchers never substitute Reels, Home, hashtag, or feed
discovery.

The launcher starts the Chrome owner, public-profile dispatcher, dashboard,
and offline workers through the supervisor. Every source record must carry
explicit `is_private=false` evidence and still passes public preflight before
a capture job is created. The default cohort target is 1,000,000 profiles.
Use the status command printed by background mode to inspect child processes.

For manual configuration, the supervisor manages explicit long-running
commands, restarts crashed workers, and writes restart/health state to
`run/local-supervisor-state.json`. For a continuously running laptop setup:

```bash
cp run/local-workers.example.json run/local-workers.json
MICROINDIA_PUBLIC_CANDIDATE_SOURCE=/absolute/path/approved-public-profiles.ndjson \
PYTHONPATH=src .venv/bin/python -m microindia_scraper.local_supervisor \
  --config run/local-workers.json
```

The continuous example does **not** launch a second browser or create a separate
profile. It attaches the dispatcher to the operator's already signed-in Chrome
through `http://127.0.0.1:9222`, then runs the dashboard and offline reel
backfill. The dispatcher:

- keeps running after the cohort target;
- reads the configured `--candidate-source` NDJSON stream in bounded batches;
- persists byte-offset cursors, provenance, candidate status, and idempotent jobs;
- applies queue high/low-water backpressure (`1000` / `250` by default);
- performs public preflight on the one reserved discovery/Home tab before leasing
  a capture tab; and
- pauses for missing/exhausted sources, authentication, and browser-owner
  degradation rather than broadening discovery.

The repeatable coordinator contract is:

- **Source stage:** only explicit `is_private=false` records from the approved
  source enter the frontier. Malformed, private, missing-privacy, malformed-URL,
  reserved-route, and duplicate-source records are durably rejected.
- **Frontier stage:** `profile_candidates` and `candidate_discoveries` retain
  current state and append-only provenance independently of capture payloads.
- **Preflight stage:** the discovery page uses same-origin fetch/metadata
  inspection. Private, unknown, expired, malformed, and auth-required results
  never reach a visible profile `goto`.
- **Research stage:** only public-preflight candidates lease idle capture tabs;
  profile transitions to private stop before content-link extraction.
- **Persistence stage:** immutable profile/content observations remain separate
  from candidate/job state, and cohort `eligible` remains the only target count.

The default topology is one Browser Use coordinator and one CDP owner. For
horizontal scale, each shard needs an independent CDP endpoint and Chrome
user-data directory; `--shard-id` and `--shard-count` use stable candidate-key
hashing to prevent duplicate capture ownership.

The signed-in Chrome account `rsinghtomar3011@gmail.com` is mapped to Chrome
profile directory `Profile 2`. Chrome 136+ rejects remote debugging against
the default Chrome user-data directory, so the continuous worker uses the
CDP-enabled local copy:

```text
$HOME/Library/Application Support/Google/Chrome-cdp-profile2
```

That copy contains `Profile 2` and its existing local session data. The
supervisor launches it automatically and the dispatcher attaches to
`http://127.0.0.1:9222`:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/Library/Application Support/Google/Chrome-cdp-profile2" \
  --profile-directory="Profile 2"
```

The one-time profile copy was performed while Chrome was closed. The collector
does not write cookies or tokens to SQLite or logs. If Instagram requests
verification in the copied profile, complete it manually in that visible
Chrome window; the collector does not bypass login challenges.

`max_restarts: null` means a worker is restarted without a restart-count ceiling.
The supervisor also uses a lock, so two copies cannot manage the same workers.

For automatic start after macOS login and restart after supervisor failure:

```bash
bash run/install-launch-agent.sh
```

This creates `run/local-workers.json` if needed and installs
`~/Library/LaunchAgents/com.microindia.scraper.plist`. It supervises the
CDP-enabled Chrome profile, the deterministic collection agent pool, dashboard,
and offline backfill. Laptop sleep, shutdown, network loss, and an Instagram
challenge remain explicit pauses; launchd resumes the collector after login or
restart, but it does not bypass those controls.

Inspect worker state without starting anything:

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.local_supervisor \
  --state run/local-supervisor-state.json --status
```

## Reel-analysis backfill

Reel analysis is derived offline from immutable raw observations and can run while
browser collection continues. It is resumable and safe to rerun:

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.backfill_reel_analysis \
  --database data/microindia.sqlite3 --batch-size 25
```

## Browser/CDP health

Run a non-invasive health check before diagnosing or restarting collection:

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.browser_health \
  --cdp-url http://127.0.0.1:9222 \
  --database data/microindia.sqlite3 \
  --minimum-pages 2 \
  --check
```

The result is also written atomically to `run/browser-health.json`. This checks
the Chrome DevTools HTTP endpoint, visible page targets, SQLite reachability, and
registered browser-page leases. It intentionally does not claim that Browser Use
startup handlers are healthy; those require an actual dispatcher session probe.

## Bounded queue worker

Queue jobs are leased atomically and are safe for a small local pilot. The normal
mode uses one isolated authorized Chrome profile per worker. Never start multiple
Chrome processes against the same user-data directory.
The shared dispatcher is the safe capture path because it performs the final
public preflight. Queue jobs without `candidate_key`, canonical `profile_url`,
`public_verified_at`, and `source_name` are rejected; do not manually create
capture jobs or use arbitrary profile URLs.

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.queue \
  --user-data-dir /Users/rohit/worker-chrome-profile-a \
  --profile-directory Default \
  --account-alias worker-a-account \
  --max-jobs 1
```

For queue validation without opening Chrome:

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.queue --dry-run --once
```

Each concurrent worker in normal mode must receive a different Chrome user-data
directory. `account_alias` is operational metadata only; credentials, cookies, and
tokens are never written to SQLite.

### Attaching to the operator's signed-in Chrome

The continuous collector attaches to the CDP-enabled Chrome process using the
copied `Profile 2` session. Follow the launch sequence in the continuous worker
section above; an already-running Chrome process without CDP cannot be
retrofitted or attached after startup.

The supervisor launches one CDP-enabled Chrome process from the local copied
profile; it does not launch additional browser processes. It stores no cookies
or tokens in SQLite or logs. The coordinator owns the single Browser Use
connection, registers ten page slots, and runs three deep captures
concurrently. Do not run `browser_owner.py` or separate queue CDP workers
against the same endpoint.

To run the dispatcher manually:

```bash
PYTHONPATH=src .venv/bin/python -m microindia_scraper.shared_dispatcher \
  --cdp-url http://127.0.0.1:9222 \
  --database data/microindia.sqlite3 \
  --candidate-source /path/to/approved-public-profiles.ndjson \
  --queue-buffer 1000 \
  --source-batch-size 500 \
  --queue-low-water 250 \
  --min-pages 10 \
  --capture-concurrency 3 \
  --shard-id 0 --shard-count 1 \
  --max-jobs 0
```

The dispatcher owns the only Browser Use connection to Chrome and runs source
ingestion and bounded capture dispatch on the same coordinator. An empty
`--candidate-source` is a safe stopped state. Missing or exhausted sources are
reported as pauses; the dispatcher does not scroll Home or synthesize fallback
profiles to increase recall.

Every capture job carries `candidate_key`, canonical `profile_url`,
`public_verified_at`, and `source_name`. A job is leased only after the
discovery/Home tab performs a non-navigation public preflight. Private,
unknown, expired, malformed, and authentication responses terminate or pause
the candidate before any capture tab is leased. A public-to-private transition
after the final preflight is quarantined immediately after the profile snapshot,
before content-link extraction.

`eligible` is the only count toward the cohort target. Dashboard frontier
counts distinguish discovered, verified public, queued, leased, captured,
quarantined, failed, source cursor, source exhaustion, and public-preflight
rejections. Do not start separate queue CDP workers against the same endpoint.