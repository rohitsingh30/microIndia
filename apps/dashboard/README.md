# apps/dashboard

The microIndia brand app: React 18 + Vite + TanStack Query + Tailwind 4. In production the API serves the built `dist/` from http://127.0.0.1:8787. Rules: `CLAUDE.md`. Product: `../../docs/product/STORY.md`.

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api to 127.0.0.1:8787
npm run build    # writes dist/; the API serves it
../scraper/.venv/bin/python qa.py   # walks every page, fails on console errors or HTTP ≥ 400
```

## Pages today
- **Live** (`/`): health, headline numbers, 24h throughput, funnel, skip reasons, newest creators, activity feed.
- **Find** (`/find`): chat-style brand search through `/api/assistant`, with a shortlist and CSV export.
- **Creators** (`/creators`): search, filters kept in the URL, table or cards, CSV, detail drawer.
- **Creator** (`/creator/:handle`): profile, engagement against peers, formats, brand activity, best and weakest posts.
- **Pipeline** (`/pipeline`) and **System** (`/system`): queues, runners, failures, retries, resources.
- **⌘K** opens a palette for jumping to a creator or running an action.

Live data comes from short polling in `src/lib/live.tsx`: `/api/activity` every 3 s and `/api/summary` every 6 s. Other queries refresh when the summary `version` changes.

## Where it's going (in progress)
Brief → Shortlist → Creator dossier (reel breakdowns) → Compare → Shortlists/export, plus Ask AI on any creator or list. Live, Pipeline and System move under `/ops`. See `docs/product/STORY.md`.
