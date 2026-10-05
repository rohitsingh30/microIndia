# microIndia: rules for every session

**North star:** a brand describes a need and gets a shortlist of real Indian creators it can defend, with every pick backed by evidence from the creator's own reels. A second track, the **Local finder**, uses the same engine so people can find a local small business on Instagram for a specific task in their area. Scraping feeds both, but the product is the insight and the user experience.

**Governing artifact:** https://claude.ai/artifact/VhXp1pDab51CxYhpuyfLcZ (source `docs/GOVERNING.html`). It is the single place the owner (Rohit) looks to see where the project stands. If it's wrong, the project is out of control.

## Before you start
1. Run `/status` (or `cd apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.status`). Know what's running and whether anything is stalled.
2. Read `docs/product/STORY.md` if the task touches anything a brand sees, and `docs/ARCHITECTURE.md` if it touches code structure.

## How work flows (required)
Any change bigger than a one-file fix goes through **`/plan-change`**:
1. **product-engineer:** does this serve the story? What exactly should the brand experience be? Logs the decision in `docs/product/DECISIONS.md`. Skip this only for pure infrastructure or bug fixes with no product effect.
2. **tech-lead:** writes the plan (files, data changes, tests, how it is verified), checks it against `docs/ARCHITECTURE.md`, and rejects duplicated helpers and new legacy.
3. **Specialist agent** builds it (see routing below). Agents that touch different files can run in parallel.
4. **Verify:** tests pass and the change is exercised for real (CLI run, API call, `qa.py`).
5. **tech-lead reviews the diff.**
6. **Docs:** update `docs/ARCHITECTURE.md` and/or `STORY.md`, then run `/sync-governing` (regenerates `docs/STATE.md` and republishes the artifact).

Product questions (what to build, for whom, what to show, naming, priority) always go to **product-engineer**, even mid-task. Don't decide them silently.

## Routing: which agent owns what

| Area | Agent |
|---|---|
| Product decisions, brand flows, priorities, `docs/product/*` | `product-engineer` |
| Plans, architecture, code review, `docs/ARCHITECTURE.md` | `tech-lead` |
| Reel and creator analysis, `llm.py`, prompts, schemas, features, backfills (`analysis/`, `intelligence.py`, `insights.py`, `brand.py`) | `insights-engineer` |
| Collection: `runtime/`, `handlers/`, `e2e.py`, `watchdog.py`, supervisor, sourcing, stalls | `scraper-ops` |
| The app: `apps/dashboard`, design system, brand pages, `/ops` pages | `brand-experience` |
| Label accuracy, eligibility audits, golden sets, eval regressions (`eligibility.py`, `data/evals/`) | `data-quality` |

## Never
- Stop scraping to do other work. Restart only the affected worker with `/restart-worker <name>`.
- Kill Chrome or `local_supervisor`, or delete or overwrite `data/*.sqlite3*`. Schema changes are additive migrations in code (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`).
- Mutate raw observations (`profile_snapshots`, `content_snapshots`). Derived data is new versioned rows.
- Turn unknown into zero. Missing is `null` and shows as missing.
- Call a model any way other than `llm.py`.
- Define follower bands outside `constants.py` or niches outside `niches.py`.
- Commit casually. **Commits are checkpoints:** one commit per finished, verified unit (a phase or a feature), containing only project files.

## Done means
- `cd apps/scraper && PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` passes. App changes also pass `npm run build` and `qa.py`.
- The change was exercised for real, not only unit-tested.
- Docs are updated and `/sync-governing` has been run if architecture, product or headline numbers changed.
- The commit is made at the checkpoint, with a message that says what the checkpoint delivers.

## Conventions
- Python 3.12 at `apps/scraper/.venv`. Run with `PYTHONPATH=src` from `apps/scraper`. Standard library first, plus Playwright. Logs are JSON lines through `runtime.runner.log`.
- A new pipeline stage is a `@handler("kind", needs_page=...)` function that returns `Done`, `Retry`, `Skip`, `Fail` or `AuthBlocked`. The next stage is a `FollowUp`.
- AI: the `claude` CLI headless via `llm.py`. Sonnet for per-reel work, Opus for dossiers and brand answers. Every prompt lives in `analysis/prompts/` with a version, and every output carries `prompt_hash`, `input_hash` and model, and is cached.
- Write for brands in plain language: "comes across as an honest reviewer", not internal labels.

Area-specific rules: `apps/scraper/CLAUDE.md`, `apps/dashboard/CLAUDE.md`.
