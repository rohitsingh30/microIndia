---
name: eval-insights
description: Score microIndia's current analysis prompts and schemas against the hand-checked golden sets (reels, creators, businesses) and compare with the previous run. Required after any prompt, schema, model or feature-version change, and before a large backfill.
argument-hint: [reels|creators|businesses|all]
---

# /eval-insights $ARGUMENTS

1. Run:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.analysis eval ${ARGUMENTS:-all}
   ```
   This uses `data/evals/golden_*.json`, writes `data/evals/runs/<timestamp>.json` and prints field-level scores against the previous run.
2. Report:
   - overall score and per-field accuracy,
   - null rate,
   - the rate of evidence that doesn't exist (an invented frame, quote or brand),
   - every field that regressed by more than 5 points, with 2 example rows each.
3. Recommend one of:
   - **ship**: no regressions, and a gain on the target fields.
   - **iterate**: name the prompt or schema change.
   - **revert**.

   The `insights-engineer` owns the fix. If a golden set is missing or has fewer than 30 rows, ask the `data-quality` agent to build or extend it first.
