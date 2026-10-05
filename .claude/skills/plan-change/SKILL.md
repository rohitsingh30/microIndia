---
name: plan-change
description: The required workflow for any non-trivial microIndia change, meaning a new feature, a new pipeline stage, a schema change, a UI flow, or a multi-file refactor. It routes through product-engineer, then a tech-lead plan, then the specialist agents, then verification, review, docs and the governing artifact. Use it whenever work is bigger than a one-file fix.
argument-hint: <what you want to change>
---

# /plan-change $ARGUMENTS

Follow these steps in order. Don't skip a step silently; if you skip one, say why.

## 1. Product verdict (product-engineer)
Skip this step only for pure infrastructure or bug fixes with no effect on what users see, and say so.

Spawn the `product-engineer` agent with the goal. Ask it for:
- who the change serves,
- the exact user-facing behaviour and copy,
- the evidence shown beside each claim,
- what's out of scope,
- how we'll know it worked,
- a ship / change / don't-build verdict.

It logs the decision in `docs/product/DECISIONS.md`.

## 2. Plan (tech-lead)
Spawn `tech-lead` with the goal and the product verdict. Ask for:
- what to reuse, with paths,
- files to change,
- additive data changes,
- the live rollout (which worker restarts; scraping never stops),
- tests,
- the real-data verification commands,
- which specialist builds which part, and what can run in parallel.

If the change is large or ambiguous, show the user the plan before building.

## 3. Build (specialists)
Route by ownership (see the routing table in `CLAUDE.md`):
- `insights-engineer`: analysis, `llm.py`, prompts
- `scraper-ops`: runtime, handlers, collection
- `brand-experience`: the app
- `data-quality`: evals and audits

Give each agent its slice of the plan. Run agents in parallel only when they touch different files.

## 4. Verify
- `cd apps/scraper && PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- The plan's real-data commands (CLI or API calls on the live database, read-only where possible)
- For app changes: `npm run build` plus `qa.py`, with screenshots at 1440 and 390 px
- For prompt or schema changes: `/eval-insights`

## 5. Review
Spawn `tech-lead` to review `git diff`, then fix what it lists.

## 6. Docs and checkpoint
- Update `docs/ARCHITECTURE.md` (tech-lead) and `docs/product/STORY.md` (product-engineer) if either changed.
- Run `/sync-governing`.
- Restart the affected worker with `/restart-worker`.
- Commit once, as a checkpoint: only project files, and a message that says what the checkpoint delivers.
