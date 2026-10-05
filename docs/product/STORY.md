# microIndia product story

Owner: the `product-engineer` agent. Every product decision is checked against this file, and settled decisions are logged in `DECISIONS.md`. Keep this file short and true: rewrite sections when they change, don't append history here.

## One line
A brand describes who it needs in plain words and gets a shortlist of real Indian creators it can defend. Every pick is backed by evidence from the creator's actual reels.

A second track, the **Local finder**, applies the same engine to small businesses: people find a local business on Instagram for a specific task in their area.

## The problem
- Indian brands increasingly spend on micro-creators (roughly 5K–100K followers), but finding the right ones is manual. Planners scroll Instagram, ask for media kits and trust follower counts.
- Existing discovery tools sell follower filters and vanity engagement numbers. They can't say *what a creator actually makes*, *how it lands*, *whether they have done paid work well*, or *why this creator fits this brief*.
- Agencies redo this research for every client and every campaign.

## Who we serve (equal priority)

**1. D2C brand marketer** (for example a skincare, snacks, ethnic-wear or wellness brand, with a team of 1–5 people)
- Needs 10–30 creators for a launch or always-on programme, usually with a city or language focus.
- Has little time and no research team. Wants an answer and the reasoning behind it, not a database.
- Success means a confident shortlist in under 10 minutes, outreach sent the same day, and no embarrassing picks (fake reach, off-brand content, competitor deals).

**2. Agency planner** (an influencer or marketing agency running several brand clients)
- Builds plans of 50–200 creators across niches and cities, and defends them to clients.
- Needs bulk search, side-by-side comparison, notes, export into decks and spreadsheets, and repeatable briefs.
- Success means cutting planning time from days to hours, and evidence the client accepts.

## The core loop
1. **Brief:** the brand writes a need: "Hinglish skincare creators in Pune and Mumbai, 10–50K, who have done honest product reviews, for a sunscreen launch." The AI may ask one or two clarifying questions (budget? content style? must-avoid?).
2. **Shortlist:** a ranked list where every creator has a *why*: the 2–3 reels that prove fit, how those reels did against the creator's own baseline, and any risks.
3. **Evidence:** open a creator dossier: what they make (content pillars and signature formats), how they sound (language and tone), who watches (audience persona), brand history (who they have worked with and how sponsored posts did against organic), production level and brand safety.
4. **Decide:** compare 2–4 creators side by side. Ask AI anything about a creator or the shortlist.
5. **Act:** save the shortlist, add notes, export to CSV or a deck-ready page, and draft outreach in the brand's voice.

## What we promise (and how)

| Promise | Built from |
|---|---|
| Real, Indian, human micro-creators | eligibility rules (India signals, human not brand or publisher, not an AI persona) |
| What they actually make | per-reel video, audio and caption analysis by Claude |
| How content performs | engagement against the creator's own median and against peers in the same follower band and niche |
| Brand readiness | detected and disclosed sponsorships, brands worked with, sponsored vs organic lift |
| Fit for *this* brief | retrieval over creator dossiers, with an explanation tied to evidence reels |
| Freshness | creators re-scraped every 24h; dossiers versioned and dated |

## Second track: Local finder (small businesses)
A separate feature with its own entry point in the app.

**Who:** anyone in an Indian city who needs a specific job done by a local business that lives on Instagram:
- a mother ordering a themed birthday cake,
- a bride looking for a mehendi artist,
- someone who needs a blouse tailored by Friday,
- a family looking for a home tutor or a party decorator.

**The job:** "find me someone *near me* who *does this task* well, *takes orders the way I can use* (DM, WhatsApp, call), and *fits my budget*." Today people scroll hashtags, ask friends, or rely on reviews that don't exist for Instagram-only sellers.

**The loop:**
1. **Task + place:** "eggless photo cake, Indore, Vijay Nagar, under ₹1,500, for Sunday."
2. **Matches:** businesses that do this, ranked by fit, each with portfolio reels or posts showing *this kind* of work.
3. **Business card:** services and specialities, the area served and delivery, price signals seen in posts or comments, how to order (public contact or link, as shown on the profile), proof (customer tags, repeat customers, comments), activity, and response signals.
4. **Act:** open the profile or contact link, save it, and compare a few.

**What we promise:** real, active, local businesses, found by *what they actually make* (from their posts and reels) rather than by their name or hashtags.

**What it reuses:** sourcing (search by service × city/area), capture, Claude analysis of posts and reels (what is made, quality, price mentions). The difference is a business classification instead of creator eligibility, plus its own profile schema ("business card").

**What it doesn't do (for now):** take orders, handle payments, or verify businesses. We link out to the business's own public channel.

## What we don't do (for now)
- No audience demographics we can't observe (age or gender splits need the creator's own insights; they come later through creator claim).
- No pricing or rate cards yet.
- No campaign execution, contracts or payments.

## Roadmap (product view)
1. **Now:** the insight engine. Reel analysis (video, audio and text) gives reel analyses, which give creator dossiers. Fix the corrupted language and topic signals.
2. **Next:** the brand app: Brief, Discover, Dossier, Compare, Shortlists, Ask AI, export. Ops moves to `/ops`.
3. **Local finder (parallel track):** keep the small businesses we currently discard, source by service × city, generate business cards, and ship a "Find a local business" entry in the app. Start with 2–3 categories in 2–3 cities (for example home bakers, mehendi, boutiques in Indore, Pune and Lucknow), then widen.
4. **Then:** pilot with 3–5 real brands or agencies. Measure time to shortlist, picks accepted and outreach sent.
5. **Later:** creator claim and verify (unlocks audience data and opt-out), brand accounts and logins, pricing signals, campaign tracking.

## Product principles
- **Evidence over scores.** Every number or label shown to a brand can be traced to the reels behind it.
- **Plain language.** Name things the way a marketer would: "comes across as an honest reviewer", not "tone_cluster_4".
- **Unknown is unknown.** Missing data shows as missing and is never guessed or zero-filled.
- **AI is the interface, data is the moat.** The chat is only as good as the dossiers underneath it, so invest in analysis depth first.
- **Fast to a first answer.** A brand should see a useful shortlist within a minute of arriving.

## Open product questions
- How should we price it: per seat, per shortlist or per campaign?
- Do brands want us to contact creators, or only to find them?
- Which 3–5 pilot brands or agencies do we approach first?
