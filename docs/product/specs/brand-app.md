# Spec: the brand app (creator product)

Owner: `product-engineer`. Builders: `brand-experience` (design + frontend), `insights-engineer` (brief, dossier, Ask AI), `tech-lead` (API plan). Written 6 Oct 2026 against live data. When this spec and the code disagree on *what the user sees*, this spec wins. When they disagree on field names, the schema in `analysis/schemas/` wins, as long as every field listed in §10 (the `Dossier` and `EvidenceReel` shapes) exists.

---

## 1. Who it's for and how we know it works

| | D2C brand marketer | Agency planner |
|---|---|---|
| Example | Growth lead at a Pune sunscreen brand, team of 3 | Planner at a Mumbai influencer agency, 6 brand clients |
| Job | "Give me 15 creators I can DM today for the launch, and tell me why each one." | "Build a defensible 80-creator plan for the client by Thursday, across 4 cities." |
| Starts in | **Brief** | **Discover** or **Brief**, then **Shortlists** |
| Must have | Answer in under a minute, the reasoning, no embarrassing picks | Bulk select, compare, notes, status, CSV, a deck-ready page |
| Success metric (first real users) | Time from first brief to a saved list of 10+ creators **< 10 min**; ≥ 60% of picks kept (not hidden or removed) | ≥ 50 creators saved across lists per session; ≥ 1 CSV or deck export per list; planner says the client accepted the evidence |
| Shared metric | Ask AI answers that cite at least one reel: **≥ 90%** | |

Instrument from day one: brief created → first pick shown (seconds) → picks saved / hidden → exports. Log to a `product_events` table (see §10).

## 2. What the data supports today (checked 6 Oct, read-only)

Kept profiles: **3,221**. Of these, 287 are small businesses (they go to the Local finder and are hidden here), leaving **2,934 creators**. Only **1,160** have posts captured; the other ~1,770 are profile-only. Reel analyses and dossiers: **0** (being built now).

| Signal | Coverage | What the app does with it |
|---|---|---|
| Followers | ~100% | Filter and show. Bands from `constants.DISPLAY_BANDS` (1K–10K, 10K–50K, 50K–100K, 100K+). |
| Posts captured (likes, comments, captions) | 1,160 creators (36%) | Only these can appear in a **Brief** shortlist. Others show in Discover as "Profile only". |
| Likes / comments per post | 95% / 88% of posts | Engagement vs peers. |
| Views / plays | **0.26% of posts** | Not filterable. Shown only from `reel_media.plays` once `media.reel` runs. |
| Post date (`published_at`) | 65% of posts | "Last posted" and "Active in last 30 days". Unknown stays unknown. |
| City | 36% of kept profiles | Filterable, with a note that unknown-city creators drop out. |
| Niche (`category`) | keyword-based; 38% "other", and wrong in places (a Chennai mom blogger tagged "food") | Filter labelled **Niche**, until dossier content pillars replace it. |
| Languages | **Polluted**: "en" on 99.8% (footer bug, fix in progress) | **Hidden** until the fix ships. Afterwards, from reel transcripts ("Speaks in"). |
| Brand work (`brand_posts`, paid partnership) | 3% / 1% of creators | Filter "Has done brand work"; show counts with the posts behind them. |
| Open to collabs (bio) | 10% | Show as a tag; filterable. |
| India confirmed | 53% | Default on in Brief; filterable in Discover. |

Follower mix of evidence-ready creators: 702 at 1K–10K, **263 at 10K–50K**, 91 at 50K–100K, 104 at 100K+. Briefs for mid-size creators in one city will often be thin. The app must say so and offer to widen or find more (§4.6), never pad the list.

## 3. Information architecture

| Route | Screen | Notes |
|---|---|---|
| `/` | **Brief** home: composer + recent briefs | The landing page. |
| `/brief/:id` | Brief result: understanding → questions → shortlist → refine | `?rev=n` shows an older revision. |
| `/discover` | **Discover**: search + filters + results | All state in the URL query. |
| `/c/:handle` | **Creator** (the dossier) | `/creator/:handle` redirects here. |
| `/compare?h=a,b,c` | **Compare** 2–4 creators | |
| `/lists` | **Shortlists** index, grouped by client | |
| `/lists/:id` | One shortlist | |
| `/lists/:id/deck` | Deck view (print-ready) | No app chrome. |
| `/local/*` | Local finder | Own shell; see `local-finder.md`. |
| `/ops`, `/ops/pipeline`, `/ops/system` | Today's Live, Pipeline, System | Old `/pipeline`, `/system` redirect. `/find`, `/creators` redirect to `/` and `/discover`. |

**Navigation (top bar, brand shell):** `microIndia` wordmark · **Brief** · **Discover** · **Shortlists** (count badge) · right side: **Find a local business** (text link to `/local`), creator search (⌘K, by name or handle). No collection status pill in the brand shell. `Ops` lives only in the ⌘K menu and the footer.

**Ask AI** is a right-side panel, not a page. It opens from the creator page, Compare, a shortlist and a brief result, and is always scoped (§8).

**Selection tray:** ticking creators anywhere (Brief, Discover, shortlist) shows a bottom bar: "3 selected · Compare · Add to shortlist · Clear". It persists across pages for the session.

## 4. Brief

### 4.1 Flow
1. **Compose.** One large text box. Placeholder: *"Hinglish skincare creators in Pune and Mumbai, 10–50K followers, who've done honest product reviews. For a sunscreen launch in November. Avoid anyone who's worked with Minimalist."* Under it, four example briefs as chips (food in Hyderabad, ethnic wear in Jaipur, fitness in Bengaluru, parenting in Chennai). Below that: **Recent briefs** (title, date, creators found).
2. **Understanding (≤ 3 s).** The brief turns into an editable card titled **"Here's what I understood"**, with these chips. Unstated fields show as `Any`:

| Chip label | Example | Default if not stated |
|---|---|---|
| What they make | Skincare reviews, routines | (required: ask if missing) |
| Cities | Pune, Mumbai | Anywhere in India |
| Size | 10K–50K followers | Any size up to 100K (no floor; the cap only keeps out accounts that are too famous) |
| Language | Hinglish | Any (chip disabled until the language fix ships) |
| Must have | Has done honest product reviews | — |
| Avoid | Worked with Minimalist | — |
| For | Sunscreen launch, November | — |
| How many | 15 | 15 |

3. **Clarifying questions (0–2).** Ask only when the answer changes the ranking. Each has tap-to-answer options and **Skip**. Priority order:

| Ask when | Question | Options |
|---|---|---|
| No product or niche can be inferred | "What are you promoting?" | free text |
| Pool > 60 after filters and no style stated | "What kind of content works for you?" | Honest reviews · Tutorials and routines · Day-in-my-life · Comedy skits · Don't mind |
| Brand work not stated and pool > 30 | "Should they have done brand deals before?" | Yes, must have · Nice to have · Doesn't matter |
| Competitors not stated and the category has well-known brands | "Any brands they shouldn't have worked with?" | free text · None |

   Never ask about budget (we have no rate data). Never ask more than 2.
4. **Searching (progress, then partial results).** Show stages with real numbers, one line each, ticking as they finish:
   - "Understood your brief"
   - "Found **38** creators in Pune and Mumbai with recent posts" (the in-scope pool)
   - "Reading reels from the **25** closest fits"
   - "Writing why each one fits"
   
   As soon as the pool is ranked (target ≤ 5 s), show the cards **without** the why, in a muted state ("Reading her reels…"). Each card fills in as its why arrives. Full shortlist target: **≤ 60 s**.
5. **Shortlist.** Ranked cards (§4.3). Header: brief title (AI-written, editable, e.g. "Sunscreen launch · Pune + Mumbai"), "15 creators · from 38 in scope · 6 Oct", buttons **Save all to a shortlist**, **Compare selected**, **Ask AI about this list**.
6. **Refine.** A chat box pinned under the results: *"Tell me what to change: 'more Hinglish', 'only Mumbai', 'drop anyone with under 2% engagement'"*. Each refine makes a new revision. The page shows the diff at the top: "**Revision 2:** 3 removed (didn't do reviews), 4 added." The revision switcher sits in the header.
7. **Hide with a reason.** Each card has **Not a fit** with quick reasons: Wrong niche · Too big · Too small · Content quality · Worked with a competitor · Other. Hidden creators collapse into "4 hidden", and the reasons feed the next revision as constraints.

### 4.2 Ranking rules (for insights-engineer)
- Pool = kept, `kind = creator`, India-confirmed, has posts captured, inside the hard constraints (city, size, avoid list).
- Retrieval first (current `assistant.retrieve`, extended to dossier text and reel analyses when present), then Opus reads the top 25 dossiers (or compact profiles when there's no dossier) and returns picks, a why, evidence reels and risks.
- Creators with a dossier rank above equally good creators without one. A pick without a dossier is labelled **"Based on captions and post stats. Reels not analysed yet."**
- No numeric fit score on screen. Use three fit labels:
  - **Strong fit**: 2+ evidence reels that directly show the brief, and no hard misses.
  - **Good fit**: 1 evidence reel, or one soft miss (e.g. slightly over the size range).
  - **Worth a look**: a profile and caption match, with thin reel evidence.

### 4.3 Shortlist card (exact contents)
| Element | Example | Source |
|---|---|---|
| Rank, avatar, name, @handle | 1 · Shanthi · @mummastessa | `creators` |
| Fit label | Strong fit | §4.2 |
| Location · size | Chennai · 14K followers | `city`, `followers` |
| **Why she fits** (1–2 sentences, must reference evidence) | "Does sit-down honest reviews of kids' products in Tamil-English; her FirstCry review reel got 3× her usual likes." | brief answer |
| Evidence reels (2–3) | thumbnail · "12 Sep · 3.1× her usual likes · Sponsored (FirstCry)" | `EvidenceReel` (§10) |
| Watch out (only if present) | "Did a paid post for Himalaya Baby in Aug, a possible conflict." | brief answer |
| Stat row | Engagement: above 72% of creators her size · Last posted 3 days ago · 5 brand posts in last 18 | insights |
| Actions | ☐ select · Save · Not a fit · Open | — |

### 4.4 States
| State | What shows |
|---|---|
| Brief too vague ("influencers for my brand") | Ask question 1. Never return random creators. |
| 0 in pool | "No creators match all of this yet." + widen chips (§4.6) + **Find more for this brief** |
| < 5 picks | Show them, plus a banner: "Only 4 creators fit everything. Widen: + Mumbai (**9** more) · any size (**6** more) · drop 'done brand deals' (**12** more)". Each chip shows the real count from relaxing that one constraint. |
| AI slow (> 60 s) | Keep the retrieval-ranked cards visible; the line reads "Still writing reasons, about 30 s more." |
| AI failed | Keep the ranked cards. Banner: "Couldn't write the reasons this time. The ranking is from captions and stats. **Try again**". Never show a blank page. |
| Network or API error | Inline error with **Retry**; the brief text is never lost (draft kept in sessionStorage). |

### 4.5 Copy
Composer button: **Find creators**. Progress title: **Working on your brief**. Results title: the brief title. Empty recent list: "Your briefs will show up here."

### 4.6 Find more for this brief
When the pool is under 15, show **Find more for this brief**. It queues sourcing searches built from the brief (no cap, owner decision 6 Oct) (for example `pune skincare`, `mumbai skincare review`) through the existing seed action. Copy after clicking: "We're searching Instagram for more skincare creators in Pune and Mumbai. New ones usually appear within a few hours. Re-run this brief to include them." The brief shows "**7 new creators** since you asked" with a **Re-run** button.

## 5. Discover

Layout: search bar on top. Filter rail on the left (it becomes a sheet at 390 px). Results as a table by default, with grid as an option. Bulk-select checkbox column.

**Search** (`q`) matches name, handle, bio, captions and hashtags, and, once present, dossier summary and content pillars. Placeholder: *"Search by what they make, e.g. 'saree draping', 'millet recipes', 'UPSC'"*.

| Filter (label) | Control | Param | Notes |
|---|---|---|---|
| Niche | multi-select, top 12 with counts | `category` | Footnote: "Based on bio and captions. Improves as we analyse reels." |
| City | multi-select with counts | `city` | Footnote: "City known for 36% of creators. Others are hidden when you filter by city." |
| Size | band chips | `min_followers`/`max_followers` | `DISPLAY_BANDS` |
| Engagement | "Above most creators their size" toggle → `min_peer_pct=0.5`; advanced: min % | `min_peer_pct`, `min_engagement` | needs posts |
| Posted recently | 7 / 30 / 90 days | `active_days` | unknown date excludes |
| Has done brand work | toggle | `brand=1` | |
| Open to collabs | toggle | `open=1` | new param |
| Reels analysed | toggle | `analysed=1` | new param; default **off** |
| Has posts we can show | toggle, default **on** | `has_posts=1` | new param |
| Speaks in | multi-select | `language` | **Hidden** until the language fix ships |
| Hidden: India confirmed | always on in the brand app | `india=1` | |
| Hidden: creators only | always | `kind=creator` | small businesses → Local finder |

**Sort:** Best match (when `q` is set) · Engagement vs peers · Followers · Recently posted · Most brand work.

**Table columns:** select · creator (avatar, name, handle, tags: *Reels analysed*, *Open to collabs*) · City · Followers · Engagement vs peers ("Top 30%") · Last posted · Brand posts · one-line summary (dossier summary when present, else the bio). Unknown shows as "—" with a tooltip saying why ("City not stated on profile").

**Bulk actions** (tray): Add to shortlist · Compare (2–4) · Export these results (CSV, existing `format=csv`).

**Ask AI** button: "Ask AI about these results". Scope = current results, top 25.

**States:** loading skeleton rows; zero results: "No creators match. Remove a filter:" + active filter chips with ×; error: inline retry.

## 6. Creator page (the dossier)

Header (sticky): avatar, name, @handle → Instagram, city, followers, **Last posted 3 days ago**, analysis pill (one of the states in 6.3), actions: **Save** · **Compare** · **Ask AI about her** · **Open on Instagram**.

### 6.1 Sections in order
| # | Section (label on screen) | Fields shown | Today (no dossier) | With dossier |
|---|---|---|---|---|
| 1 | **In short** | 3–4 sentence summary for a marketer | Not shown. Replaced by the "not analysed" block (6.3) | `dossier.summary` |
| 2 | **Numbers** | Engagement vs peers ("Gets more likes and comments than 41% of the 286 creators her size"); typical likes and comments per post; typical reel plays; posting rhythm ("about 3 posts a week"); sponsored vs usual ("Sponsored posts get 0.8× her usual likes") | `insights.peer_percentile`, `peer_count`, `median_likes`, `median_comments`, `cadence`, `sponsored_lift`. Plays: "—, not collected yet" | same, plus `reel_media` median plays |
| 3 | **What she makes** | Content pillars (name, share of analysed reels, 2 evidence reels each); signature formats ("talking-head reviews in the car") | Top hashtags and content mix (reels vs posts), labelled "From hashtags" | `dossier.content_pillars[]`, `dossier.signature_formats[]` |
| 4 | **How she comes across** | Languages spoken, with share; tone in plain words ("warm, chatty, honest about cons"); on camera (face / voiceover / text only); 2 hook lines quoted with reel and timestamp | Hidden | `dossier.voice` + `reel_analyses.transcript_language`, `hook` |
| 5 | **Who watches** | Audience persona in 1–2 sentences. Label: "Our read from her content and comments. Not Instagram's audience data." | Hidden | `dossier.audience_persona` + evidence |
| 6 | **Brand work** | Brands worked with: name, date, reel, disclosed (paid-partnership tag or #ad) yes/no, how it did vs her usual. Categories she's promoted. | Posts with brand mentions or paid tags from `posts_sample` + `brand_posts`, `paid_partnerships` counts | `dossier.brand_history[]` + `reel_analyses.sponsor` |
| 7 | **Good fit for** | Ranked categories (e.g. Baby care, Family travel), each with 1–2 evidence reels; 3 pitch angles ("A 'what I pack for a beach weekend' reel featuring your sunscreen") | Hidden | `dossier.category_fit[]`, `dossier.pitch_angles[]` |
| 8 | **Production and safety** | Production level (phone, lit, edited / studio), with an example reel; brand-safety flags each with a reel, or "Nothing concerning in the 17 reels we watched" | Hidden. Never show "safe" without analysis | `dossier.production`, `dossier.safety` |
| 9 | **Strengths and watch-outs** | 2–4 each, every one citing a reel or a number | `insights.summary` bullets | `dossier.strengths[]`, `dossier.risks[]` |
| 10 | **Reels we looked at** | Grid: thumbnail, date, likes or plays vs her usual (×), Sponsored tag, one line ("Reviews 3 baby sunscreens, picks one"), **Open on Instagram** | `posts_sample` as caption cards (no thumbnail), best and weakest from `insights` | `reel_analyses` + `reel_media` |
| 11 | **Similar creators** | 6 cards | `similar` | same, ranked by dossier similarity later |
| 12 | **About this data** (footer, small) | "Profile checked 5 Oct · 17 reels analysed 6 Oct · Dossier v1" | `captured_at`, counts | `dossier.created_at`, `dossier_version`, `reel_count` |

Every number has a hover or tap note showing what it's based on ("Median of 12 posts, 3 Sep–2 Oct"). Every pillar, risk and fit claim links to its reels.

### 6.2 Copy rules for this page
Use the creator's pronoun if the dossier states it, else "they". Ratios are written as "3× her usual likes", never "lift 2.1". Percentiles are written as "above 72% of creators her size", never "p72".

### 6.3 Analysis states (pill + block)
| State | Pill | Block in place of sections 1, 3–5, 7–8 |
|---|---|---|
| Dossier ready | **Reels analysed · 6 Oct** | — |
| Dossier older than 30 days | **Analysed 34 days ago** | Sections shown, plus a **Refresh** link |
| In progress | **Analysing reels · 6 of 15** | "We're watching her reels. This page fills in as we go." |
| Has posts, no dossier | **Not analysed yet** | "We haven't watched this creator's reels yet. What's below comes from captions and post stats." + **Analyse this creator** (queues `media.reel` + `analyze.*` at high priority) |
| Profile only | **Profile only** | "We only have this creator's profile so far. Their posts are queued." Shows header, bio and followers; everything else is hidden |
| Analysis failed | **Analysis didn't finish** | "We couldn't analyse enough reels (private or removed)." + what we do have |

## 7. Compare (2–4 creators)

Columns = creators (sticky header with avatar, name, × remove, **+ Add** up to 4). Rows, in order:

In short · Fit for this brief (only when opened from a brief: fit label + why) · Followers · Engagement vs peers · Typical reel plays · Posting rhythm · Last posted · Speaks in · What she makes (top 3 pillars) · Best reel (thumbnail + ×) · Brand work (count + last brand) · Sponsored vs usual · Production · Brand safety · Who watches · City.

- The best value in a numeric row gets a subtle highlight. Unknown never wins and shows "—".
- **Ask AI to pick** opens Ask AI scoped to these creators, prefilled: "Which of these is the best fit for {brief title or 'a skincare launch'} and why?"
- Entry points: the selection tray, the shortlist bulk action, and the creator page "Compare" (adds to the tray).

## 8. Ask AI

A right-side panel (full screen at 390 px). The scope chip at the top can't be removed, only changed by opening Ask AI elsewhere.

| Scope | Chip | Example questions shown |
|---|---|---|
| One creator | "Asking about @mummastessa" | "Has she done paid work for baby brands, and how did it do?" · "Would she suit a ₹499 sunscreen for kids?" · "What would a good pitch to her look like?" · "Any content I should worry about?" |
| Shortlist or brief | "Asking about 'Sunscreen launch' · 15 creators" | "Who on this list has done honest reviews?" · "Who speaks Hinglish on camera?" · "Which 5 would you pick for a ₹2L budget and why?" · "Anyone who has promoted a competitor?" |
| Compare set | "Comparing 3 creators" | "Which is the best fit and why?" · "Who gets the best sponsored-post results?" |
| Discover results | "Asking about these 25 results" | "Which of these make food content in Telugu?" |

**Answer format (fixed):**
1. **Direct answer**, 1–4 sentences, plain language.
2. **Points**, each ending with citations like `[1]`, `[2]`.
3. **Evidence**: numbered reel cards matching the citations (thumbnail, @handle, date, one line, quote with timestamp when from speech, "Open on Instagram"). A citation can also be a stat ("Engagement vs peers, 12 posts").
4. **Couldn't check** (only if relevant): "We don't have rate cards, so we can't judge budget fit." / "Only 4 of 15 creators have analysed reels."
5. Three follow-up chips.

Rules: answer only from the scoped creators' data. If asked about anyone else: "I can only answer about the creators in this list." Never invent numbers. If no evidence is found: say so. The thread persists per scope for the session.

**States:** thinking ("Reading 15 creators' reels…", with elapsed seconds); slow (> 45 s): "Still reading, long lists take up to a minute"; error: "Couldn't answer this time. **Try again**" (the question stays in the box).

## 9. Shortlists

Stored on the server (not localStorage). There are no logins yet: one shared workspace. The current localStorage shortlist is imported once into a list called "My shortlist".

**Index (`/lists`):** grouped by **Client** (free text, optional; "No client" group). Each row: list name, brief it came from (link), creators, updated, **Open** · **Deck** · **CSV**. Button **New shortlist**.

**List page (`/lists/:id`):** title and client (editable inline), the source brief link, and a table:

| Column | Notes |
|---|---|
| select | bulk: Compare · Move to list · Remove · Set status |
| Creator | avatar, name, handle, city |
| Followers · Engagement vs peers · Last posted | from creator |
| Why | brief why if added from a brief, else empty (editable) |
| Status | **Considering** (default) · **Contacted** · **Confirmed** · **Dropped** |
| Note | free text, autosaves |
| Added | date |

Header actions: **Ask AI about this list** · **Compare selected** · **Export CSV** · **Deck view** · **Copy link**.

**CSV columns (exact order):** `handle, name, instagram_url, city, followers, follower_band, engagement_rate, engagement_vs_peers_pct, median_likes, median_reel_plays, posts_per_week, last_posted, brand_posts, brands_worked_with, open_to_collabs, fit_label, why, evidence_reel_1, evidence_reel_2, evidence_reel_3, status, note, analysed_on`. Unknown = empty cell, never 0.

**Deck view (`/lists/:id/deck`):** no app chrome, A4 landscape print CSS, one creator per page: name, handle, city, followers, In short, Why (for this brief), 3 evidence reel thumbnails with one line each, 4 stats (engagement vs peers, typical plays, posting rhythm, brand work), the note. First page: list title, client, date, "N creators", with a table of contents. Footer: "Prepared with microIndia · data as of 6 Oct 2026". The planner prints it to PDF or screenshots it into the client's deck.

**States:** empty list: "No creators yet. Add them from a brief or Discover."; save conflict (two tabs): last write wins per field, no data loss for notes (save on blur).

## 10. API contract

Existing endpoints are reused and extended. All new endpoints are JSON. Unknown values are `null`.

### Shared shapes
```ts
type CreatorCard = {            // extends today's Creator; list endpoints return this
  handle; name; url; city: string|null; followers: number|null; follower_band: string|null;
  category: string|null; engagement_rate: number|null; peer_percentile: number|null; peer_count: number|null;
  last_post_ts: number|null; brand_posts: number; open_to_collabs: boolean;
  has_posts: boolean; analysis_state: "ready"|"stale"|"analysing"|"not_analysed"|"profile_only"|"failed";
  summary: string|null;          // dossier summary, else null
  languages: string[]|null;      // null until the language fix ships
};
type EvidenceReel = {
  shortcode; permalink; thumb_url: string|null;   // served by us: /api/media/<shortcode>/thumb.jpg (from keyframes); IG CDN URLs expire
  published_ts: number|null; type: "reel"|"post"|"carousel";
  likes: number|null; comments: number|null; plays: number|null;
  vs_usual: number|null;          // engagement ÷ creator median, e.g. 3.1
  sponsored: boolean; brand: string|null;
  what: string|null;              // one line from reel analysis, else caption snippet
  quote: { text: string; at_s: number } | null;
};
type Citation = { n: number; kind: "reel"; reel: EvidenceReel; handle: string } | { n: number; kind: "stat"; handle: string; label: string; basis: string };
```

### Endpoints per screen
| Screen | Method + path | Request | Response |
|---|---|---|---|
| Brief | `POST /api/briefs` | `{text}` | `{id, status:"understanding"}` |
| Brief | `GET /api/briefs/:id` (poll every 1.5 s while `status` isn't final; polling lives in `lib/live.tsx`) | `?rev=n` | `{id, title, text, rev, revisions:[{rev, note, created_at}], status:"understanding"|"needs_answer"|"searching"|"writing"|"done"|"failed", understood:{what, cities[], min_followers, max_followers, language, must[], avoid[], purpose, count}, questions:[{id, text, options[], free_text:boolean}], stages:[{key, label, done:boolean}], pool_size, picks:[{rank, creator:CreatorCard, fit:"strong"|"good"|"worth_a_look"|null, why:string|null, evidence:EvidenceReel[], watch_out:string|null}], hidden:[{handle, reason}], widen:[{label, param, value, adds}], error:string|null, created_at}` |
| Brief | `POST /api/briefs/:id/answers` | `{answers:{[question_id]: string|null}}` (null = skipped) | same as GET |
| Brief | `PATCH /api/briefs/:id` | `{understood?: {...}, title?}` (chip edits) | new revision |
| Brief | `POST /api/briefs/:id/refine` | `{message?, hide?: {handle, reason}}` | new revision, `{rev, diff:{added[], removed:[{handle, reason}]}}` |
| Brief | `POST /api/briefs/:id/find-more` | `{}` | `{queued:[query...]}` (wraps `/api/actions/seed`) |
| Brief | `GET /api/briefs` | `?limit=20` | `[{id, title, created_at, picks_count}]` |
| Discover | `GET /api/creators` (existing) | existing params + `kind`, `has_posts`, `analysed`, `open`, `min_peer_pct`; multi-value `city`, `category` (comma-separated) | existing `{total, items: CreatorCard[], facets}`; `format=csv` unchanged |
| Creator | `GET /api/creators/:handle` (existing) | — | existing `CreatorDetail` + `analysis_state`, `analysis_progress:{done, total}|null`, `dossier: Dossier|null`, `reels: EvidenceReel[]` (analysed reels, newest first) |
| Creator | `POST /api/creators/:handle/analyse` | `{}` | `{analysis_state, queued:number}` |
| Thumbs | `GET /api/media/:shortcode/thumb.jpg` | — | jpeg, 404 → UI shows caption card |
| Compare | `GET /api/compare` | `?h=a,b,c&brief=id` | `{creators:[CreatorDetail-lite: CreatorCard + insights + dossier summary/pillars/brand_history/production/safety/audience + best_reel + fit?]}` |
| Shortlists | `GET /api/shortlists` | — | `[{id, name, client, brief_id, count, updated_at}]` |
| | `POST /api/shortlists` | `{name, client?, brief_id?, handles?:[{handle, why?}]}` | list |
| | `GET /api/shortlists/:id` | — | `{id, name, client, brief_id, items:[{creator:CreatorCard, why, fit, status, note, added_at, evidence:EvidenceReel[]}]}` |
| | `PATCH /api/shortlists/:id` | `{name?, client?}` | list |
| | `DELETE /api/shortlists/:id` | — | `{ok}` |
| | `POST /api/shortlists/:id/items` | `{items:[{handle, why?, fit?}]}` | list (dedupes) |
| | `PATCH /api/shortlists/:id/items/:handle` | `{status?, note?, why?}` | item |
| | `DELETE /api/shortlists/:id/items/:handle` | — | `{ok}` |
| | `GET /api/shortlists/:id/export.csv` | — | CSV, columns as in §9 |
| Ask AI | `POST /api/ask` | `{scope:{type:"creator"|"shortlist"|"brief"|"compare"|"results", id?, handles?}, question, thread_id?}` | `{id, thread_id, status:"thinking"}` |
| Ask AI | `GET /api/ask/:id` (poll) | — | `{status:"thinking"|"done"|"failed", answer, points:[{text, cites:number[]}], citations: Citation[], couldnt_check:string[], follow_ups:string[], scope_label, error}` |
| All | `POST /api/events/product` | `{name, props}` | `{ok}`, written to `product_events` |

`/api/assistant` and `/api/search/chat` retire when Brief ships. `Dossier` = the `creator_dossiers.result` JSON. It must contain at least: `summary`, `content_pillars[{name, share, reels[]}]`, `signature_formats[]`, `voice{languages[{code, share}], tone, on_camera}`, `audience_persona{text, evidence[]}`, `brand_history[{brand, date, shortcode, disclosed, vs_usual}]`, `category_fit[{category, reels[]}]`, `pitch_angles[]`, `production{level, example}`, `safety{flags[{text, shortcode}], checked_reels}`, `strengths[{text, cites}]`, `risks[{text, cites}]`. Every reel reference is a shortcode the API expands to `EvidenceReel`.

## 11. Global states
| Situation | Behaviour |
|---|---|
| First load | Skeletons shaped like the final layout; no spinners on whole pages |
| API down | Top banner: "Can't reach microIndia right now. Retrying…" Pages keep their last data |
| Model unavailable (`llm.py` fails) | Brief returns the retrieval ranking with a banner; Ask AI says "AI is unavailable right now, try in a minute" |
| Unknown value | "—", with a tooltip giving the reason. Never 0, never hidden silently |
| Mobile (390 px) | Brief, creator page, shortlist and Ask AI are fully usable; Discover filters in a sheet; Compare scrolls sideways with a sticky first column |

## 12. Labels (use exactly)
| Concept | Say | Never say |
|---|---|---|
| eligible | (don't surface) | eligible, kept, quarantined |
| engagement vs peers | "Above 72% of creators her size" | percentile, p72, ER |
| engagement rate | "Engagement 3.2%" (with "likes + comments ÷ followers" tooltip) | ER |
| sponsored lift | "Sponsored posts get 0.8× her usual likes" | lift |
| brand_ready | "Has done brand work" | brand ready |
| dossier | "Creator page" / "Reels analysed" | dossier |
| follower band | "10K–50K followers" | micro, nano, band |
| category | "Niche" (now) → "What she makes" (with dossier) | category |
| analysis pending | "Not analysed yet" | pending, null |
| shortlist | "Shortlist" | list, bucket |

## 13. Deferred (and why)
| Item | Why not now |
|---|---|
| Logins, brand accounts, per-user lists | No outside users yet: the app is local and used by the owner. Add accounts when the first real brand or agency uses it |
| Rate cards / budget fit | No data. Ask AI says so |
| Audience demographics (age, gender, city split) | Not observable without creator claim |
| Outreach drafting and sending | After the core loop works end to end; then "Draft a DM" in Ask AI |
| Saved Discover views, alerts ("new creators match your brief") | After brief re-run proves useful |
| Language filter | Until the language fix ships and transcripts exist |
| Shared links with permissions, comments on lists | Needs accounts |
| Native PPTX export | Deck view + print to PDF covers the agency need for now |
| Streaming tokens (SSE) for AI answers | Polling with staged progress is enough; revisit if answers feel slow |
