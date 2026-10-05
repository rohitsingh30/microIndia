# Product decisions

Append-only, newest first. Each entry: date, decision, why, who decided. The product-engineer agent adds an entry whenever a product question is settled. Reverse a decision with a new entry; never edit an old one.

---

## 2026-10-06 · Owner answers on the specs
- **Local finder has no launch slice.** No city or service preference: every small business the scraper meets gets profiled properly, and coverage grows as collection runs. The app shows honestly where it's deep. *Why:* build the dataset as we go instead of steering sourcing toward chosen cities.
- **Creator size is a wide range with an upper cap only.** The Brief defaults to any size up to 100K. There's no meaningful floor, and the cap only keeps out accounts that are too famous or already very big. Brands can change it.
- **On-demand analysis and sourcing are uncapped.** "Analyse this creator" and "Find more for this brief" queue Claude analysis and scraping at high priority with no daily cap.
- **There are no pilot brands yet.** The app stays local for the owner. No access, tunnel or login work until a real brand or agency is lined up. The roadmap's "pilot with 3–5 brands" was an assumption, not a plan.
- **By:** Rohit

## 2026-10-06 · AI is a first-class surface of the brand app; ship 0→1 fast
- **Decision:** brands get what they need by talking to our AI: they write a brief and get a shortlist, refine it in plain words, and Ask AI about a creator or a shortlist. It's a primary surface, not a helper tucked into one page. Build the smallest useful version first and widen from there.
- **Why:** the current UI "looks childish" and makes the brand do the research; the value is the answer and its evidence. Specs: `specs/brand-app.md` (Brief, Ask AI) and `specs/local-finder.md`.
- **By:** Rohit

## 2026-10-06 · Second product track: Local finder for small businesses
- **Decision:** add a separate feature where people find a local small business on Instagram for a specific task in their area: "custom birthday cake in Indore this weekend", "bridal mehendi artist in Pune", "home tutor for class 10 maths in Lucknow".
- **Why:** much of India's small-business economy (home bakers, boutiques, salons, tailors, decorators, tutors) lives on Instagram and is hard to search by task and area. The scraper already meets these accounts every day and currently discards them as "business" or "non-human".
- **Shape:** the same engine (sourcing, capture, Claude analysis), a different classification and a different user. Businesses are kept and profiled for the services they offer, the area they serve, price signals, how they take orders, portfolio quality and customer proof. Users search by task and place.
- **By:** Rohit

## 2026-10-06 · Insights and the brand product come before more scraping
- **Decision:** engineering effort moves to deep reel and creator analysis and to the brand-facing product. Scraping keeps running in the background and gets only reliability work.
- **Why:** the dataset (3,000+ kept creators) is already big enough to build insight on. The product is only worth something if a brand can make a decision from it.
- **By:** Rohit

## 2026-10-06 · Personas: D2C brands and agencies, equally
- **Decision:** design for an Indian D2C brand marketer and an influencer/marketing agency planner at the same time. One core loop (brief → shortlist → evidence → decide → export) serves both. Agencies get bulk and compare features on top.
- **By:** Rohit

## 2026-10-06 · AI engine is the Claude CLI, headless
- **Decision:** every LLM call goes through `claude -p` (structured output with `--json-schema`) behind one wrapper (`llm.py`). Sonnet analyses reels; Opus writes creator dossiers and brand answers.
- **Why:** it runs on the existing subscription, needs no API key, and the wrapper keeps a later switch to the API cheap.
- **By:** Rohit

## 2026-10-06 · Reels are analysed from the full video and audio
- **Decision:** for each reel we download the video, extract keyframes, the first 3 seconds (the hook) and the audio, and transcribe on this Mac (mlx-whisper). Claude reads the frames, transcript, caption and metrics together.
- **Coverage:** at least 15 reels per kept creator, chosen as about 8 most recent, the 4 best performers, and every sponsored or collab reel, topped up with recent ones.
- **Caching:** keep transcripts, keyframes, compressed audio (opus), raw model outputs, prompt version and input hash, so later analysis can re-run without re-scraping. Only the mp4 is deleted.
- **Why:** caption keywords can't tell a brand what a creator actually makes. Quality matters more than speed.
- **By:** Rohit

## 2026-10-06 · The dashboard becomes the brand product
- **Decision:** rework `apps/dashboard` into a brand tool: Brief, Discover, Creator dossier, Compare, Shortlists and Ask AI. The current Live, Pipeline and System pages move under `/ops`. The current look is retired.
- **By:** Rohit

## 2026-10-05 · One task runtime on one signed-in Chrome
- **Decision:** sourcing and scraping are task kinds (`@handler`) on one SQLite task queue, driving one signed-in Chrome over CDP. The Browser Use agent system is retired.
- **By:** Rohit
