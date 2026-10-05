---
name: analyze-reel
description: Run microIndia's full reel pipeline (media fetch, keyframes and hook frames, audio transcript, Claude analysis) on a single reel and show the result. Use when developing or debugging reel prompts and schemas, checking a specific reel, or before queueing a backfill after a prompt change.
argument-hint: <reel permalink or shortcode> [--force]
---

# /analyze-reel $ARGUMENTS

1. Run the pipeline on that one reel. This attaches its own tab to the signed-in Chrome and doesn't disturb the running workers:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.analysis reel $ARGUMENTS
   ```
   `--force` skips the cache and re-runs the model with the current prompt version.
2. Show the user, compactly:
   - the media facts (plays, duration, audio, sponsor tags),
   - the transcript language and its first 2 lines,
   - the hook,
   - the format, topic and spoken languages,
   - products or brands visible, and sponsored (detected vs disclosed),
   - production quality, tone and target audience,
   - brand-safety flags,
   - the 2-line summary and why it performed,
   - the evidence references,
   - the model, prompt version, seconds taken and whether it came from cache.
3. Point out anything that looks wrong: invented evidence, a wrong language, or a generic summary. For prompt work, suggest the specific prompt or schema change to the `insights-engineer`.

Keyframes are saved under `data/media/frames/<shortcode>/`, and you can open them with Read to check the model's claims.
