# microIndia dashboard

React + Vite + Tailwind single-page app for the always-on collector. It is
served by `microindia_scraper.api` on http://127.0.0.1:8787 from `dist/`, and it
updates live through the `/api/events` Server-Sent Events stream.

- **Live**: health, KPIs, 24h throughput, funnel, why profiles get skipped,
  newest creators, a streaming activity feed, best sources.
- **Creators**: search (`/`), filters kept in the URL, table or card view, CSV
  export. Click any row to open the detail drawer (`?creator=handle`).
- **Pipeline**: task queues, runners, failures with retry, skip reasons,
  up-next, and an "add seeds" box.
- **⌘K**: jump to any creator or run an action. `1`/`2`/`3` switch pages and
  `T` toggles the theme.

```
npm install
npm run dev      # http://localhost:5173, proxies /api to 127.0.0.1:8787
npm run build    # writes dist/; the API serves it
```
