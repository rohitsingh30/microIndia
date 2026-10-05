---
name: creator-dossier
description: Build or refresh one microIndia creator's dossier (reel selection, reel analyses, Opus synthesis) and present it the way a brand would read it. Use to check dossier quality, prepare a creator for a brand conversation, or debug the dossier prompt.
argument-hint: <instagram handle> [--force]
---

# /creator-dossier $ARGUMENTS

1. Run:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.analysis creator $ARGUMENTS
   ```
   The command:
   - selects at least 15 reels (recent, best performers, sponsored or collab),
   - fetches and analyses any reel without a current analysis,
   - synthesises the dossier.

   Cached work is reused unless `--force` is passed.
2. Present it as a brand would see it:
   - the one-paragraph brand summary,
   - content pillars and signature formats,
   - voice, tone and languages,
   - the audience persona,
   - production level,
   - brands worked with, and how sponsored posts did against organic,
   - category fit (ranked, each with 1–2 evidence reels),
   - strengths and risks, and brand safety,
   - 3 pitch angles,
   - headline stats: engagement against peers, cadence, reel plays.
3. Then give a short critique. Does each claim trace to evidence? Is anything generic or vague? Is anything missing that a D2C marketer or agency planner would ask? Pass concrete fixes to the `insights-engineer`.
