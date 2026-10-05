# apps/dashboard: local rules

Read the root `CLAUDE.md` and `docs/product/STORY.md` first. This app is the **brand product**. The ops pages (Live, Pipeline, System) live under `/ops`.

- **Product first.** Any new page, flow or change in what a brand sees starts with the `product-engineer` agent. Visual direction changes are proposed as two options, and product-engineer picks.
- **Design system.** Colours, type and spacing come from tokens in `src/index.css`. No hex values in TSX. Shared building blocks live in `src/components/ui.tsx`; don't redefine small components inside pages.
- **Data.** All requests go through `src/lib/api.ts` (`get()` checks `response.ok`). Live polling lives only in `src/lib/live.tsx`; pages don't add their own intervals on the same query.
- **Copy.** Write for marketers: plain words, evidence before scores, missing data shown as missing.
- **Verify:** `npm run build` passes, and `../scraper/.venv/bin/python qa.py` passes with no console errors (needs the API on :8787). Check screens at 1440 px and 390 px, in light and dark.
