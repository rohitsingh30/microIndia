# apps/scraper: local rules

Read the root `CLAUDE.md` first. Run everything from this directory with `PYTHONPATH=src .venv/bin/python …`.

- **Live system.** The database `data/microindia.sqlite3` is in use by running workers right now. Read it with `mode=ro` URIs for analysis. Writes go through `TaskStore`, `CaptureStore` or the module that owns the table. Schema changes are additive and idempotent (`CREATE TABLE IF NOT EXISTS`, guarded `ALTER TABLE`).
- **Restarting.** After changing a handler or runtime code, restart only the worker that runs it (`/restart-worker scraper|sourcer|analyzer|api|watchdog`). The supervisor brings it back with the new code.
- **New stage:** add a `@handler` in `handlers/` or `analysis/`, register it in the package `__init__`, add a test in `tests/test_runtime.py` style with a fake page, and add a row to the task-kinds table in `docs/ARCHITECTURE.md`.
- **Models.** Call them only through `llm.py`. Prompts live in `analysis/prompts/<name>.md` with a version line, and JSON schemas in `analysis/schemas/<name>.json`. Changing a prompt means a new version, followed by `/eval-insights`.
- **Media.** Files go under `data/media/` (git-ignored). Keep keyframes, hook frames, opus audio, transcripts and raw outputs. Delete the mp4 after analysis.
- **Tests:** `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` (unittest, not pytest). Tests must never touch the live database: use `tempfile` databases as the existing tests do.
- **Taxonomy and bands:** niches only in `niches.py`, follower bands only in `constants.py`.
- **Legacy, don't extend:** `shared_dispatcher.py`, `queue.py`, `browser_owner.py`, `candidate_source.py`, `cohort.py`, `ui.py` are being removed.
