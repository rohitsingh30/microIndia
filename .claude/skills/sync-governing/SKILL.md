---
name: sync-governing
description: Refresh microIndia's governing artifact, the owner's single view of the project. It regenerates docs/STATE.md from live data, brings the numbers, status, roadmap and architecture in docs/GOVERNING.html up to date, and republishes the artifact to its fixed URL. Use after any change to architecture, product direction, roadmap or headline numbers, and at the end of every phase.
---

# /sync-governing

The governing artifact is https://claude.ai/artifact/VhXp1pDab51CxYhpuyfLcZ. Its source is `docs/GOVERNING.html`. Always republish to that URL, never to a new one.

1. **Regenerate the state:**
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.status --markdown ../../docs/STATE.md
   PYTHONPATH=src .venv/bin/python -m microindia_scraper.status --json
   ```
2. **Read what changed:** `docs/STATE.md`, recent `docs/product/DECISIONS.md` entries, `docs/ARCHITECTURE.md`, and `git log --oneline -10`.
3. **Edit `docs/GOVERNING.html`** so every section is true *now*:
   - the snapshot timestamp and headline numbers (kept, brand-ready, queue, last hour, insight backlog),
   - the "Right now" process table and alert. Remove the alert if the issue is fixed, add one if a new issue is open,
   - "What happened": add a timeline entry for each checkpoint,
   - Architecture and Insight pipeline: move items out of **(building)** once shipped,
   - Product story: the one-liner, personas, tracks (brand product, Local finder) and roadmap from STORY.md,
   - Agents and workflow,
   - Problems: mark fixed items as fixed and add new ones,
   - Roadmap and decisions: tick off done items and add newly decided ones.

   Keep the page's existing design tokens and structure. Copy is plain and specific. Use real numbers only.
4. **Republish:** call the Artifact tool with `file_path` set to `docs/GOVERNING.html` and `url` set to `https://claude.ai/artifact/VhXp1pDab51CxYhpuyfLcZ`. In a new conversation, read it first with `action: "read"`.
5. **Tell the user** in 2–3 lines what changed on the page.
