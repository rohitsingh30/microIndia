# Spec: Local finder

Owner: `product-engineer`. Builders: `scraper-ops` (`source.local`, business capture), `insights-engineer` (`analyze.business`, business cards, query parsing), `brand-experience` (`/local` screens), `tech-lead` (plan). Written 6 Oct 2026 against live data. Decision of record: DECISIONS.md, 2026-10-06 "Second product track: Local finder".

---

## 1. User, job, success

| | |
|---|---|
| **Who** | Anyone in an Indian city who needs a specific job done by a business that lives on Instagram. Examples: Priya in Koramangala wants a unicorn-theme eggless cake for her daughter's 5th birthday on Sunday; Divya in Anna Nagar needs a bridal mehendi artist for 14 Dec; Neha in Andheri wants a party makeup artist who'll come home. |
| **Job** | "Find me someone *near me* who *does exactly this* well, *takes orders in a way I can use*, *fits my budget* and *can do it by my date*." |
| **Today** | Scroll hashtags, ask the society WhatsApp group, DM 10 accounts and wait. Google and Justdial miss Instagram-only home businesses. |
| **Device** | Phone first (390 px). Desktop works but isn't the design target. |
| **Success (launch)** | ≥ 70% of in-coverage searches return 5+ matches; ≥ 30% of searches end in a contact tap (Message / WhatsApp / Call); ≥ 20% of users who saved a business come back within 14 days. |

## 2. The loop

1. **Ask.** One box: *"What do you need, and where?"* Placeholder rotates: *"Eggless photo cake in Indiranagar under ₹1,500 for Sunday"* · *"Bridal mehendi artist in Anna Nagar for 14 Dec"* · *"Party makeup at home in Andheri West"*.
2. **We understood.** Chips: **Task** (Eggless photo cake) · **Area** (Indiranagar, Bengaluru) · **Budget** (under ₹1,500) · **When** (Sun 11 Oct) · **Must** (eggless). Tap a chip to edit it. If the city is missing, ask the one question we're allowed: *"Which area are you in?"*, with a location-free text box. We never ask for GPS.
3. **Matches.** A list of business cards (compact), best first. Each card shows 3 portfolio thumbnails of *this kind* of work and one line on why it matched: "12 photo cakes in the last 2 months · Indiranagar · delivers in east Bengaluru".
4. **Business card.** Tap a match to see the full card (§4).
5. **Act.** **Message on Instagram** (always available) · **WhatsApp** / **Call** (only when the business shows the number publicly) · **Save** · **Compare** (up to 3, side by side). We link out; we never take the order.

## 3. How a query is parsed

Rules first (fast, free), then Sonnet through `llm.py` only when the rules leave the task or the place empty.

| Field | Examples it must handle | Output | If missing |
|---|---|---|---|
| **Task** (service + specifics) | "photo cake", "fondant cake 2 kg", "bridal mehendi both hands full", "HD party makeup", "saree draping" | `service` (launch: `cakes` · `mehendi` · `makeup`) + `specifics[]` (eggless, photo print, theme: unicorn, 2 kg, home visit, bridal) | Ask "What do you need done?" |
| **Place** | "Indiranagar", "near Forum mall Koramangala", "Anna Nagar West", "Andheri W", "Bandra" | `city` + `area` (from an area gazetteer per launch city) + `radius_hint` | Ask "Which area are you in?" If the city is out of coverage, show the out-of-coverage state |
| **Budget** | "under 1500", "₹2k", "budget 800-1200", "sasta", "premium" | `max_inr`, `min_inr` or `tier` | Not asked. Unknown is fine |
| **Timing** | "Sunday", "this weekend", "14 Dec", "kal", "urgent", "by Friday" | `date` (resolved against IST today) + `urgent:boolean` | Not asked |
| **Must** | "eggless", "pure veg", "home visit", "organic henna", "female artist" | `must[]` | — |

Hinglish works the same way ("kal tak cake chahiye Indiranagar mein, 1500 ke andar").

## 4. Business card

| Section | Field | Example | Source | When unknown |
|---|---|---|---|---|
| Header | Name, @handle, profile photo, followers (small), **Active** badge | "Sugar & Spice by Ritu · @sugarspice.blr · Posted 2 days ago" | profile | — |
| What they do | Services, specialities | "Custom cakes: photo, theme, fondant. Specialities: eggless, bento cakes" | `analyze.business` from bio + posts | "Services not clear from their posts" (card ranks low) |
| Where | Area served, delivery | "Based in Indiranagar · delivers across east Bengaluru (₹100–200)" | bio, captions, IG business address if public | "Area not stated. Ask before ordering" |
| Price signals | Prices seen, with source and date | "Photo cakes from ₹1,200/kg (post, 12 Sep)" · "Bento cake ₹450 (post, 3 Oct)" | captions and text in images (Sonnet reads post images) | **"Prices not posted. Ask them."** (expected for most: only ~4% of cake, mehendi and makeup captions we've seen mention a price) |
| How to order | Channels as shown publicly; order notes | "DM to order · WhatsApp 98xxxxxx12 (in bio) · Needs 2 days' notice" | bio text, bio links, IG public contact fields, captions | "Order through Instagram DM" (always true) |
| Lead time | Notice needed | "Orders 48 hrs in advance" | bio, captions | Hidden |
| Portfolio | 6–9 posts matching the task, each with a one-line description | "Unicorn theme, 1 kg, eggless" | `analyze.business` per post | If < 3 matching posts the business isn't shown for this task |
| Proof | Customer mentions and tags in captions, "repeat order" mentions, posts of delivered orders | "Tags 14 customers in the last 2 months" | captions, mentions | "No customer posts found" (neutral, not a penalty label) |
| Activity | Last post, posts in the last 30 days | "Posted 2 days ago · 11 posts this month" | posts | "Last post date unknown" |
| Footer | "Checked 5 Oct. Details come from their public Instagram. Confirm price and date with them." | capture date | — |

We don't show ratings, stars or any "verified" mark. We don't show "replies fast": we can't observe it.

## 5. Ranking

**Hard filters:** same city; classified as a business that takes orders (not a hobbyist or a creator); at least 3 portfolio posts for this service; posted within the last 60 days; `must[]` satisfied where we can tell (an unknown "eggless" stays in the list, labelled "Ask if eggless").

**Signals, in order of weight:**

| # | Signal | Notes |
|---|---|---|
| 1 | Task match | Count and recency of portfolio posts showing *this specific* work (photo cake ≠ wedding cake). |
| 2 | Place | Same area > neighbouring area (gazetteer adjacency) > delivers to the area > same city. |
| 3 | Active | Posts in the last 30 days; last post date. |
| 4 | Can order | Clear order channel, lead time stated, and the lead time fits the date when we know both. |
| 5 | Proof | Customer tags and mentions, delivered-order posts. |
| 6 | Price fit | Boost only when a known price is inside the budget. An unknown price never removes or demotes a business. A known price clearly above budget sinks it, labelled "Usually above your budget". |
| 7 | Portfolio quality | Sonnet's read of the images (finish, consistency), with a cited post. Tie-breaker only. |

The "why matched" line on each card names the top 2 signals in plain words. No numeric score is shown.

## 6. Coverage: no launch slice, it grows with scraping

**Owner decision (6 Oct): no city or service preference.** Every small business the scraper meets is classified and profiled properly, and the Local finder covers whatever that produces. Coverage grows as collection runs; we don't pick launch cities.

What that means in practice:
- **Profile every business we meet.** At `scrape.profile`, an account that looks like a local business is routed to the business path (posts captured, `analyze.business`, a versioned business card) instead of being discarded. Creator eligibility is unchanged.
- **Re-check what we already threw away first.** About 880 handles: 166 skipped as "account type is business", 70 as "big business (over 50K)", and 645 the API labels small business (287 of those slipped into the creator pool). That costs no new sourcing.
- **Be honest about coverage.** `/local` shows where we have real depth: services × cities with counts, computed live from business cards. A search outside covered ground still runs, shows what exists, and logs the unmet demand. That log tells us where depth is missing.
- **Where we are today** (6 Oct, bio and name text, cakes, mehendi and makeup): Chennai 67, Delhi NCR 44, Mumbai 39, Bengaluru 38, Pune 2, Lucknow 2, Indore 0. Mehendi 115, makeup 108 (+52 tagged by Instagram), cakes 94, boutiques and tailors 23.
- **A cell counts as "covered"** in the coverage chips once it has ≥ 40 active cards across ≥ 8 areas. This is display logic only; it never hides results.

## 7. Sourcing: `source.local`

| Step | What | Owner |
|---|---|---|
| 1 | **Gazetteer:** areas for the cities we actually see businesses in, starting with the densest (Bengaluru: Indiranagar, Koramangala, HSR Layout, Whitefield, Jayanagar, JP Nagar, Malleshwaram, Hebbal…; Chennai: Anna Nagar, T. Nagar, Velachery, Adyar, OMR, Mylapore…; Mumbai: Andheri W/E, Bandra, Powai, Borivali, Thane, Chembur, Malad…) plus adjacency. One source of truth next to `niches.CITIES` (tech-lead picks the file). | tech-lead, product-engineer supplies the list |
| 2 | **Service terms:** cakes = `home baker`, `homebaker`, `custom cakes`, `cake`, `bento cake`; mehendi = `mehendi artist`, `mehndi`, `bridal mehendi`, `henna`; makeup = `makeup artist`, `bridal makeup`, `party makeup`, `mua`. | insights-engineer |
| 3 | **`source.local`** task (key = `service:city:area`), *later and optional*: runs Instagram search for `"{term} {area}"`, then queues `scrape.profile` with a `local` hint. Used only to deepen thin areas that the demand log shows people asking for. Not needed for launch. | scraper-ops |
| 4 | **Similar accounts:** each business that passes as a local business queues `source.similar`. Local businesses follow each other, so this is the cheapest density. | scraper-ops |
| 5 | **Re-check the ~880 discarded handles** first (one-off backfill). | scraper-ops |
| 6 | **Classification:** at `scrape.profile`, an account with business signals plus India and local-service evidence is routed to the business path (posts captured, `analyze.business`) instead of being skipped. Creator eligibility is unchanged. | data-quality + insights-engineer |
| 7 | **Capture for businesses**, when public: bio links, public phone or email, WhatsApp links, business address or city, IG business category. Today none of these are stored (only `external_url`, which is often just a Threads link). | scraper-ops |
| 8 | **`analyze.business`** → `business_cards` (versioned): services, specialities, area served, delivery, prices with post source, order channels, lead time, portfolio posts tagged by service and specifics, proof, quality note. | insights-engineer |

Freshness: re-scrape active businesses every 7 days. Activity drives ranking, and a stale card loses trust fast.

## 8. Screens

| Route | Screen | Key states |
|---|---|---|
| `/local` | Ask box, chips of what's covered, computed live (for example "Mehendi · Chennai · 63"), saved businesses | — |
| `/local/search?q=` | We understood (chips) + matches | Searching (≤ 3 s, skeleton cards) · **< 3 matches**: show them + "Try nearby: Domlur (6) · HAL (4)" · **0 matches**: "No one we know does this in Indiranagar yet." + nearby areas + **Tell me when there's someone** (logs demand, no account needed) · **Out of coverage**: "We're live in Bengaluru, Chennai and Mumbai for cakes, mehendi and makeup. We'll use your search to decide where to go next." · error: retry |
| `/local/b/:handle` | Full business card | Card older than 14 days: "Checked 18 days ago" · business went private or was deleted: "This business isn't on Instagram publicly any more" |
| `/local/compare?h=a,b,c` | 2–3 cards side by side: portfolio, area, prices seen, how to order, lead time, activity | — |
| `/local/saved` | Saved businesses (kept in this browser, localStorage; no login) | Empty: "Tap Save on a business to keep it here." |

Shell: its own minimal header ("microIndia · Local"), no brand-app nav, a link back to the brand app in the footer only.

## 9. API contract

```ts
type LocalQuery = { task: string|null; service: "cakes"|"mehendi"|"makeup"|null; specifics: string[];
  city: string|null; area: string|null; max_inr: number|null; min_inr: number|null;
  date: string|null; urgent: boolean; must: string[] };
type PortfolioPost = { shortcode; permalink; thumb_url: string|null; published_ts: number|null; what: string };
type BusinessCardLite = { handle; name; avatar_url: string|null; city; area: string|null; delivers_to: string[]|null;
  last_post_ts: number|null; posts_30d: number|null; why_matched: string; price_hint: string|null;
  portfolio: PortfolioPost[]; order_channels: ("instagram"|"whatsapp"|"phone"|"website")[]; flags: string[] };
type BusinessCard = BusinessCardLite & { services: string[]; specialities: string[];
  prices: {text: string; inr_min: number|null; inr_max: number|null; unit: string|null; shortcode: string; seen_ts: number}[];
  contact: { instagram_dm: string; whatsapp: string|null; phone: string|null; website: string|null; source: string };
  lead_time: string|null; proof: {text: string; shortcodes: string[]}[]; quality_note: string|null;
  followers: number|null; checked_ts: number; card_version: string };
```

| Method + path | Request | Response |
|---|---|---|
| `GET /api/local/coverage` | — | `{cells:[{service, city, cards, live:boolean}], areas:{[city]: string[]}}` |
| `POST /api/local/search` (synchronous, target ≤ 3 s, cards are precomputed) | `{q, override?: Partial<LocalQuery>}` | `{query: LocalQuery, question: string|null, coverage: "ok"|"city_not_live"|"service_not_live", matches: BusinessCardLite[], nearby: [{area, count}], total}` |
| `GET /api/local/businesses/:handle` | — | `BusinessCard` |
| `GET /api/local/compare?h=a,b,c` | — | `{cards: BusinessCard[]}` |
| `POST /api/local/demand` | `{q, query: LocalQuery, contact?: string}` | `{ok}`, stored in `local_demand`. Every zero or out-of-coverage search is also logged automatically |
| `POST /api/events/product` | `{name: "local_contact_tap"|"local_save"|..., props}` | `{ok}` (shared with the brand app) |

Contact links are built client-side: `https://ig.me/m/{handle}`, `https://wa.me/91{number}`, `tel:+91{number}`, only from `contact` fields that are non-null.

## 10. Deferred
| Item | Why |
|---|---|
| Taking orders, payments, delivery | Not our job; we link out (STORY) |
| Verification, ratings, reviews written by users | Needs accounts and moderation |
| "Replies fast" or response-time signals | We can't observe DMs |
| Comment-based proof ("ordered again, loved it!") | Comment text isn't captured yet. Add it when `media.reel` fetches top comments |
| GPS and "near me" by location | Area names are enough for launch and avoid a permission prompt |
| Services beyond the launch three (boutiques, tailors, decorators, tutors, salons) | Data is thin; add one at a time once a cell can reach 40 cards |
| Cities beyond the launch three | Delhi NCR next (needs a cross-city area model), then a tier-2 wave |
| Accounts and cross-device saved lists | localStorage is enough to learn whether people come back |
