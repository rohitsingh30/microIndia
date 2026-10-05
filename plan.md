# Plan: Micro-Influencer Platform (microIndia)

## Overview

Build a platform connecting micro-influencers with brands for campaign collaborations. The platform has three user types: **Influencers**, **Brands**, and **Admins**.

The initial product is a high-quality influencer discovery dataset for India. Discovered accounts are **unverified prospects** until the creator claims, reviews, and verifies the profile. The system preserves provenance, freshness, confidence, opt-out status, and collection history.

## Architecture (Monorepo)

```text
microIndia/
├── packages/
│   ├── database/       # SQLite for MVP; migrations and repositories
│   └── shared-types/   # Zod schemas, API types, pipeline contracts
├── apps/
│   ├── frontend/       # Next.js 14 App Router
│   ├── backend/        # Express.js API
│   └── scraper/        # Browser navigation and dataset pipeline
└── docs/               # Data-source, privacy, operations, and API docs
```

### Browser and authentication architecture

The scraper uses a provider abstraction:

```ts
type AuthMode = "local-chrome-cdp" | "browser-use-managed-session";
```

- **Default for development/pilot:** connect to an explicitly launched local Chrome/Chromium instance using a persistent profile already signed in by the operator.
- **Deployment option:** use a Browser Use managed browser session, with manual sign-in/bootstrap inside that session.
- **Agent runs:** use Browser Use agent workflows only for supervised exploration, debugging, or exceptional navigation—not as the source of truth for structured metrics.
- **Deterministic collection:** use explicit browser workflows with typed extraction and validation for repeatable dataset jobs.
- Passwords, cookies, session tokens, and browser profiles must never be stored in Git, SQLite records, logs, screenshots, or `.env.example`.
- If Instagram requests login, verification, or a challenge, stop the job and mark it `needs_manual_auth`; do not attempt to bypass the control.
- Track only safe operational metadata such as `account_alias`, `auth_mode`, session health, last successful use, and re-authentication state.
- Managed Browser Use sessions must be stopped explicitly after completion.

## Phase 0: Dataset, Access, and Policy Design

- Define permitted data sources and the public/consented data boundary.
- Define retention, deletion, opt-out, suppression, creator claim, and verification policies.
- Do not collect private content, DMs, non-public contact details, or data revealed only by an authentication challenge.
- Define the initial India cohort: regions, languages, verticals, follower bands, activity requirements, and launch coverage target.
- Define the controlled taxonomy: beauty, fashion, food, fitness, travel, technology, parenting, finance, regional/language categories, and `other`.
- Define metric formulas, freshness windows, minimum valid observations, confidence scoring, and quality thresholds.
- Define lifecycle states: `discovered`, `eligible`, `needs_review`, `unverified`, `claimed`, `verified`, `opted_out`, `suppressed`.
- Define pilot KPIs: extraction completeness, duplicate rate, relevance rate, stale-record rate, challenge/error rate, and creator-claim conversion.

## Phase 1: Foundation and Dataset Schema (Weeks 1-2)

- Create the monorepo packages and applications.
- Build SQLite migrations and repositories for platform and collection entities.
- Add Zod schemas for persisted observations and pipeline events.
- Keep raw/source observations separate from normalized influencer profiles.
- Add fixtures and database tests before connecting to Instagram.

### Core data entities

- `users`, `influencer_profiles`, `brand_profiles`, `categories`, `campaigns`, `quotes`, `contracts`
- `influencer_candidates` — canonical discovered accounts and lifecycle state
- `source_profiles` — source-specific handle, URL, and platform identity
- `profile_captures` — one visit/checkpoint with schema version, status, progress, and completeness
- `profile_snapshots` — immutable profile-header observations saved as soon as the header is parsed
- `content_snapshots` — one immutable observation per recent item, saved immediately after parsing
- `metric_snapshots` — computed metrics derived from persisted observations and safely recomputable
- `discovery_events` — seed, query, source URL, and collection run that found a record
- `collection_runs`, `collection_attempts` — status, provider, account alias, timing, error class, and cost
- `collection_jobs`, `worker leases`, `account_health` — bounded local parallel execution with idempotency and cooldown state
- `creators`, `posts`, `post_observations` — canonical entities linked to immutable raw captures
- `post_features`, `creator_features` — versioned, explainable derived intelligence recomputable without a browser
- `category_predictions` — category, confidence, classifier version, and reviewer override
- `identity_resolution` — duplicate decisions and confidence
- `profile_claims`, `profile_verifications`, `suppression_requests`

## Phase 2: Collection Platform and Instagram Pilot (Weeks 2-4) - PRIORITY

- Implement `BrowserProvider` with `LocalChromeCdpProvider` and `BrowserUseProvider` adapters.
- Implement explicit session lifecycle, manual authentication bootstrap, health checks, cleanup, and `needs_manual_auth` handling.
- Start with a small, bounded pilot using the signed local Chrome profile; do not begin a broad six-hour crawl.
- Discovery inputs: curated hashtags, seed accounts, public location/language signals, referrals, and later official connected-account imports.
- Create one versioned `ProfileCapture` per profile visit. Capture the profile header plus the 18 most-recent accessible content items in the same visit.
- Save the candidate, discovery event, and collection attempt as soon as discovery begins.
- Save the profile snapshot immediately after the profile header is parsed.
- Save each content snapshot immediately after that individual item is parsed; do not wait for the full profile crawl to finish.
- Store immutable raw observations with source, timestamp, parser version, capture ID, and collection run ID.
- Persist item-level unavailable/error states so one failed item does not discard successful items.
- Calculate engagement from the latest 12 eligible non-pinned items that are already persisted; save metric snapshots after capture and allow recomputation.
- Use `null` for unavailable values; never convert unavailable metrics to zero or guessed values.
- On interruption, retain all saved observations and mark the capture `partial`, `needs_manual_auth`, `failed`, or `quarantined` as appropriate.
- Resume an interrupted capture by reusing its capture ID and collecting only missing or unsaved items.
- A later profile visit creates a new immutable capture; it never overwrites historical observations.
- Normalize handles and URLs, parse counts safely, preserve unknown values, and quarantine malformed records.
- Add minimum-valid-post rules, pinned/unavailable-content handling, and confidence scoring.
- Deduplicate by platform ID/handle/normalized URL and route uncertain matches to review.
- Classify into the controlled taxonomy with confidence, model/rule version, and `other/unclassified` fallback.

## Phase 3: Collection Operations and Quality (Weeks 4-5)

- Replace one global cron with queued, idempotent collection jobs.
- Add checkpointed profile jobs so every successful crawl step creates durable value.
- Make profile/content writes idempotent using capture ID plus platform profile/content ID or permalink, with content index as a fallback.
- Recompute metrics from persisted snapshots rather than relying on in-memory crawl state.
- Prioritize new discovery, high-value validation, and stale records rather than refreshing every profile every six hours.
- Add bounded concurrency, adaptive backoff, retryable/non-retryable error classes, dead-letter states, and per-provider/account limits.
- Add account/session health tracking, kill switches, structured logs, alerts, and operational dashboards.
- Add quality review queues and human sampling before profiles become searchable by brands.
- Track candidate count, eligibility rate, completeness, duplicate rate, freshness, challenge rate, cost/profile, and claim/verification outcomes.
- Add budget controls for Browser Use managed sessions and explicitly stop completed sessions.
- The local pilot now uses WAL-safe SQLite job leasing and requires one isolated browser profile/session per worker; do not share a live Chrome profile directory.
- Profile jobs now dispatch through the existing Browser Use E2E runner, record attempts, retry bounded transient failures, and stop terminally on manual-auth/challenge errors.
- Workers update non-secret account health (`busy`, `healthy`, `cooldown`, `needs_manual_auth`, `error`) using only an operator-provided account alias.
- Creator exploration is based on aggregate public engagement/content signals; it does not create identity-level follower/commenter dossiers.

## Phase 4: Backend API (Weeks 5-7)

- Auth endpoints: register, login, refresh, and current user.
- Influencer and brand profile CRUD.
- Candidate review, source/provenance, freshness, confidence, suppression, and claim/verification endpoints.
- Campaign CRUD and search/filter.
- Quote workflow: brand sends → influencer accepts/negotiates.
- Contract workflow: create from quote → dual signature → PDF.
- Admin endpoints for collection runs, quality review, account health, and dataset controls.

## Phase 5: Frontend (Weeks 7-9)

- Landing page and auth pages.
- **Influencer Dashboard:** claim/verification, profile review, profile wizard, campaigns, contracts, quotes, earnings, consent, and opt-out controls.
- **Brand Dashboard:** campaign wizard, influencer discovery, provenance/freshness/confidence indicators, quotes, and analytics.
- **Admin Dashboard:** candidate review, duplicate resolution, collection health, dataset quality, suppression requests, and platform analytics.

## Phase 6: Integration, Security, and Deployment (Week 9+)

- Test collection workflows with fixtures and a small approved pilot cohort.
- Store secrets in a secrets manager; never commit session data.
- Keep scraper workers separate from the API/frontend.
- Use SQLite for local MVP only; evaluate a managed relational database for multi-worker production use.
- Add Docker Compose, CI/CD, backups, encryption, access controls, audit logs, deletion jobs, monitoring, and documentation.
- Establish source/provider kill switches and incident response procedures.

---

## Key Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Priority: scraper first or platform first? | **Dataset and scraper first** ✅ |
| 2 | Local authentication path? | **Operator-signed persistent local Chrome profile via CDP** ✅ |
| 3 | Cloud browser alternative? | **Browser Use managed session with manual sign-in** ✅ |
| 4 | Browser Use agent role? | **Supervised exploration/fallback, not primary structured extraction** ✅ |
| 5 | Account/session challenge handling? | **Stop and require manual authentication; never bypass** ✅ |
| 6 | Categories? | **Controlled taxonomy with dynamic predictions and confidence** ✅ |
| 7 | Dataset records? | **Immutable observations plus normalized candidate/profile views** ✅ |
| 8 | Payments? | **Manual for MVP** ✅ |
| 9 | Contract PDF? | **Server-side later** ✅ |
| 10 | Notifications? | **Email later** ✅ |
| 11 | Production database? | **SQLite locally; decide managed relational database before scale** ✅ |

---

## Next Steps

1. 🔄 Define source, retention, consent/claim, opt-out, and challenge-stop policies.
2. 🔄 Finalize the MVP creator cohort, taxonomy, metric formulas, and quality KPIs.
3. Create the expanded database migrations and shared Zod schemas.
4. Implement the browser-provider interface and local signed Chrome CDP bootstrap.
5. Implement Browser Use managed-session adapter with explicit cleanup and budget limits.
6. Build a small deterministic discovery/profile workflow with fixture-based parsing tests.
7. Add provenance, snapshots, validation, deduplication, classification, and quality review.
8. Run a limited pilot and measure quality before enabling recurring scheduling.
9. Build creator claim/verification and admin review before exposing prospects broadly to brands.
10. Build backend, frontend, and campaign workflows.