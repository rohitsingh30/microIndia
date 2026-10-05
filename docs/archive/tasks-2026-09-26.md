# Task List: Micro-Influencer Platform (microIndia)

## Phase 0: Dataset, Access, and Policy Design

- [ ] Define permitted sources and the public/consented data boundary
- [ ] Define retention, deletion, opt-out, suppression, claim, and verification policies
- [ ] Define challenge/login failure behavior: stop and mark `needs_manual_auth`
- [ ] Define India MVP cohort: regions, languages, verticals, follower bands, and activity threshold
- [ ] Define controlled category taxonomy and `other/unclassified` fallback
- [ ] Define engagement-rate formula, content window, minimum valid posts, and confidence scoring
- [ ] Define candidate lifecycle states and brand-search eligibility rules
- [ ] Define pilot KPIs and quality acceptance thresholds

## Phase 1: Foundation - Database and Shared Types

### Database Package (`packages/database`)

- [ ] Create `package.json` with `better-sqlite3`, `bcryptjs`, `jsonwebtoken`, and `zod`
- [ ] Create `tsconfig.json`
- [ ] Create `src/db.ts` - SQLite connection singleton
- [ ] Create migrations for users, platform profiles, campaigns, quotes, and contracts
- [ ] Create migrations for candidates, source profiles, profile captures, profile/content/metric snapshots
- [ ] Create migrations for discovery events, collection runs/attempts, predictions, and identity resolution
- [ ] Create migrations for claims, verification, suppression, audit, and operational state
- [ ] Create seed data for categories, test users, and fixture candidates
- [ ] Create repositories for platform entities
  - [ ] UserRepository
  - [ ] InfluencerProfileRepository
  - [ ] BrandProfileRepository
  - [ ] CategoryRepository
  - [ ] CampaignRepository
  - [ ] QuoteRepository
  - [ ] ContractRepository
- [ ] Create repositories for dataset entities
  - [ ] InfluencerCandidateRepository
  - [ ] SourceProfileRepository
  - [ ] SnapshotRepository
  - [ ] CollectionRunRepository
  - [ ] DiscoveryEventRepository
  - [ ] ClaimRepository
  - [ ] SuppressionRepository
- [ ] Add capture status/progress fields: `running`, `complete`, `partial`, `needs_manual_auth`, `failed`, `quarantined`
- [ ] Add checkpoint fields: requested item count, observed item count, last completed item, missing fields, warnings, and completeness score
- [ ] Add idempotency keys and unique constraints for captures, observations, and collection attempts
- [ ] Ensure later captures create immutable history and never overwrite prior snapshots
- [ ] Add database tests for initialization, migrations, snapshots, deduplication, and suppression
- [x] Add canonical creator/post records and versioned derived feature tables

### Shared Types Package (`packages/shared-types`)

- [ ] Create `package.json` with `zod` and `typescript`
- [ ] Create schemas for users, influencer profiles, brands, campaigns, quotes, contracts, and categories
- [ ] Create schemas for candidates, source profiles, `ProfileCapture`, snapshots, metrics, collection runs, and discovery events
- [ ] Create schemas for predictions, identity resolution, claims, verification, and suppression
- [ ] Create enums/types for `AuthMode`, lifecycle states, run states, error classes, and confidence levels
- [ ] Create inferred TypeScript types and API request/response types
- [ ] Create fixtures for valid, incomplete, duplicate, stale, and suppressed records
- [ ] Create `src/index.ts` exports

## Phase 2: Collection Platform and Instagram Pilot

### Scraper Package (`apps/scraper`)

- [ ] Create `package.json` with approved browser/runtime dependencies, `better-sqlite3`, `dotenv`, and queue/scheduling dependencies
- [ ] Create `tsconfig.json`
- [ ] Create `src/config.ts` for auth mode, account aliases, bounded limits, seed inputs, and feature flags
- [ ] Create `src/browser/BrowserProvider.ts` interface
- [ ] Create `src/browser/LocalChromeCdpProvider.ts` for an explicitly launched signed local Chrome profile
- [ ] Create `src/browser/BrowserUseProvider.ts` for Browser Use managed browser sessions
- [ ] Add manual authentication bootstrap and health-check workflow
- [ ] Ensure credentials, cookies, profiles, and tokens are excluded from logs, database, and source control
- [ ] Add explicit Browser Use session cleanup and budget/concurrency limits
- [ ] Add stop conditions for login challenges, verification prompts, unexpected access changes, and policy violations

### Collection workflows

- [ ] Create `src/workflows/discoverProfiles.ts` for bounded seed/hashtag discovery
- [ ] Create `src/workflows/collectPublicProfile.ts` for structured profile observations
- [ ] Create `src/workflows/collectRecentContent.ts` for eligible recent-content observations
- [ ] Create `src/workflows/validateAccess.ts` for public/consented access checks
- [ ] Capture the profile header and create the capture record before collecting recent content
- [ ] Configure a bounded content window of 18 recent accessible items
- [ ] Save the profile snapshot immediately after parsing the header
- [ ] Save each content snapshot immediately after parsing that item
- [ ] Persist item-level unavailable/error states and continue when safe
- [ ] Resume interrupted captures using the existing capture ID and skip already-persisted items
- [ ] Add parser fixtures and tests for count formats, missing values, unavailable content, and changed layouts

### Pipeline

- [ ] Create `src/pipeline/validate.ts` with schema and impossible-value checks
- [ ] Create `src/pipeline/normalize.ts` for handles, URLs, counts, timestamps, and locations
- [ ] Create `src/pipeline/metrics.ts` for documented engagement calculations and denominators
- [ ] Calculate metrics from the latest 12 persisted eligible non-pinned items
- [ ] Recompute and persist metrics without requiring a browser visit
- [ ] Create `src/pipeline/classify.ts` for taxonomy predictions, confidence, and versioning
- [ ] Create `src/pipeline/deduplicate.ts` for deterministic and review-based identity resolution
- [ ] Create `src/pipeline/persist.ts` for immutable observations and normalized candidate updates
- [ ] Add checkpoint-safe persistence after every profile/content observation
- [ ] Use `null` for unavailable values and never treat missing metrics as zero
- [ ] Create quarantine handling for malformed or low-confidence observations
- [ ] Add provenance to every observation: source, run ID, account alias, provider, parser version, and timestamp

## Phase 3: Collection Operations and Quality

- [ ] Create collection-run state machine: queued, running, completed, partial, failed, `needs_manual_auth`, quarantined
- [ ] Add profile-capture state machine and resume logic for interrupted crawls
- [ ] Add idempotent queued workers and bounded concurrency
- [x] Add SQLite WAL-safe job enqueueing and atomic worker leases for the local pilot
- [x] Connect `capture_creator_profile` jobs to the Browser Use E2E runner with attempt records, retries, and manual-auth terminal state
- [x] Track non-secret worker/account health and cooldown state for bounded parallel collection
- [ ] Add retryable/non-retryable error classes, backoff, dead-letter state, and kill switches
- [ ] Add account/session health tracking without storing secrets
- [ ] Add freshness-priority scheduling instead of a global six-hour full refresh
- [x] Add creator explorer filters for query, category, language, follower range, and engagement threshold
- [ ] Add structured logs, metrics, alerts, and collection dashboards
- [ ] Add Browser Use usage/cost tracking and managed-session stop verification
- [ ] Add admin quality-review queue and human sampling workflow
- [ ] Define and test minimum quality thresholds before brand search visibility
- [ ] Run a limited pilot and record completeness, duplicate rate, relevance, freshness, challenge rate, and cost/profile

## Phase 4: Creator Claim and Backend API

- [ ] Create Express app with CORS, Helmet, Zod validation, auth, error handling, and rate limiting
- [ ] Create auth endpoints: register, login, refresh, and me
- [ ] Create influencer and brand profile CRUD
- [ ] Create candidate discovery/search endpoints with freshness, confidence, and verification states
- [ ] Create admin endpoints for collection runs, quality review, suppression, and account health
- [ ] Create creator claim, profile review, verification, consent, and opt-out endpoints
- [ ] Create campaign CRUD and search/filter
- [ ] Create quote workflow: send, accept, negotiate, list
- [ ] Create contract workflow: create, sign, and PDF generation
- [ ] Add services that use repositories and shared schemas

## Phase 5: Frontend

- [ ] Create Next.js 14 App Router project with TypeScript and Tailwind
- [ ] Create auth pages
- [ ] Create influencer dashboard: claim, verify, edit profile, campaigns, contracts, quotes, earnings, consent, opt-out
- [ ] Create brand dashboard: campaigns, discovery, quality indicators, quotes, analytics
- [ ] Create admin dashboard: users, candidate review, duplicate review, suppression, collection health, analytics
- [ ] Display freshness, confidence, verification, and provenance status clearly
- [ ] Never present unverified or stale estimates as creator-confirmed facts

## Phase 6: Integration, Security, and Deployment

- [ ] Add Docker Compose for local development
- [ ] Add GitHub Actions CI/CD and lint/test/build checks
- [ ] Add secrets-manager integration and secret scanning
- [ ] Keep scraper workers separate from API/frontend services
- [ ] Add backups, encryption, access controls, audit logs, and deletion jobs
- [ ] Evaluate managed relational database before multi-worker production scale
- [ ] Add monitoring, incident response, source/provider kill switches, and operational documentation
- [ ] Add README, data policy, API docs, runbooks, and Browser Use/local Chrome setup instructions
- [ ] Run end-to-end tests with fixtures and a small approved pilot cohort