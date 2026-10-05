---
name: local-search
description: Try microIndia's Local finder as a user would. Give it a task and a place (for example "eggless photo cake in Indore under 1500") and it shows matching small businesses with evidence posts. Use to test or demo the Local finder, or to check business-card quality for a city or service.
argument-hint: "<task>" [--place <city/area>]
---

# /local-search $ARGUMENTS

1. Run:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.analysis local-search $ARGUMENTS
   ```
2. Show the parsed task (service, place, budget, timing) and then the top matches. For each match:
   - what they make (from their business card),
   - the area served,
   - price signals,
   - how to order (as shown publicly on the profile),
   - 2 evidence posts,
   - recent activity.
3. Judge the results as the person searching would. Are these really local? Do they really do this task? Is anything missing? If coverage is thin for that service or city, say so and suggest the `source.local` queries to queue for `scraper-ops`.
