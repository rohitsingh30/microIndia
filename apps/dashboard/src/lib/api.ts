export type Runner = {
  runner_id: string;
  kinds: string;
  tabs: number;
  status: string;
  processed: number;
  last_heartbeat: number;
  last_error: string | null;
  age_seconds: number;
  alive: boolean;
};

export type Summary = {
  now: number;
  eligible_total: number;
  eligible_today: number;
  brand_ready: number;
  band: string;
  creators_seen: number;
  scraped_last_hour: number;
  found_last_hour: number;
  queue: number;
  in_flight: number;
  failed: number;
  funnel: { found: number; scraped: number; kept: number; dropped: number };
  focus: { niche: string; city: string; band: string; started_at: number; captured: number } | null;
  captured_today: number;
  auth_blocked: { reason: string; since: number } | null;
  chrome: { ok: boolean; consecutive_failures: number } | null;
  health_checked_at: number | null;
  watchdog_actions: { ts: number; action: string; worker?: string; reason?: string }[];
  runners: Runner[];
};

export type TimePoint = { ts: number; found: number; scraped: number; eligible: number };

export type Activity = {
  id: number;
  ts: number;
  kind: string;
  key: string;
  state: "done" | "skipped" | "failed" | "auth_blocked" | "retrying";
  reason: string | null;
  found: number | null;
  followers: number | null;
  eligible: boolean;
  found_via: string | null;
};

export type Creator = {
  handle: string;
  url: string;
  name: string | null;
  bio: string | null;
  location: string | null;
  external_url: string | null;
  verified: boolean;
  followers: number | null;
  following: number | null;
  posts: number | null;
  languages: string[];
  category: string | null;
  engagement_rate: number | null;
  median_likes: number | null;
  median_comments: number | null;
  median_views: number | null;
  top_hashtags: string[];
  content_mix: Record<string, number>;
  commercial_rate: number | null;
  data_quality: string | null;
  status: string;
  eligible: boolean;
  completeness: number | null;
  captured_at: string;
  captured_ts: number | null;
  first_seen_ts: number | null;
  first_eligible_ts: number | null;
  captures: number;
  found_via: string | null;
  brand_posts: number;
  paid_partnerships: number;
  disclosed_promos: number;
  open_to_collabs: boolean;
  brand_ready: boolean;
  kind?: "creator" | "small business";
  india_signals: string[];
  city: string | null;
  last_post_ts: number | null;
};

export type AssistantReply = {
  answer: string;
  engine: "llm" | "retrieval";
  matches: number;
  picks: { handle: string; why: string; creator: Creator }[];
  follow_ups: string[];
};

export type Fit = { score: number; reasons: string[]; misses: string[] };

export type InsightPost = {
  permalink: string | null; type: string; caption: string; published_ts: number | null;
  likes: number | null; comments: number | null; views: number | null; engagement: number | null;
  rate: number | null; sponsored: boolean; paid_partnership: boolean;
};

export type Insights = {
  band: string;
  engagement_rate: number | null;
  peer_median_rate: number | null;
  peer_percentile: number | null;
  peer_count: number;
  formats: { type: string; posts: number; median_engagement: number | null; median_rate: number | null; median_views: number | null }[];
  sponsored_posts: number;
  sponsored_share: number | null;
  sponsored_lift: number | null;
  cadence: { posts_per_week: number; median_gap_days: number; last_post_days_ago: number; span_days: number } | null;
  comments_per_100_likes: number | null;
  reel_reach: number | null;
  consistency: number | null;
  hashtags: { tag: string; posts: number; median_rate: number; lift: number | null }[];
  best_posts: InsightPost[];
  weakest_posts: InsightPost[];
  timeline: { ts: number; engagement: number; type: string; sponsored: boolean }[];
  summary: string[];
};

export type Post = {
  permalink: string | null;
  type: string | null;
  caption: string | null;
  published_at: string | null;
  likes: number | null;
  comments: number | null;
  views: number | null;
  hashtags: string[];
  mentions: string[];
  location: string | null;
  paid_partnership: boolean;
};

export type CreatorDetail = Creator & {
  metrics: Record<string, unknown>;
  posts_sample: Post[];
  warnings: string[];
  missing_fields: string[];
  reasons: string[];
  provenance: { via: string; from: string }[];
  follower_history: { ts: number; followers: number }[];
  insights: Insights;
  similar: { handle: string; name: string | null; followers: number | null; engagement_rate: number | null; category: string | null; eligible: boolean }[];
};

export type CreatorPage = {
  total: number;
  items: Creator[];
  facets: { category: [string, number][]; language: [string, number][]; source: [string, number][]; brand_ready: number; india: number; city?: [string, number][] };
};

export type Pipeline = {
  kinds: Record<string, Record<string, number>>;
  failures: [string, number][];
  skip_reasons: [string, number][];
  runners: Runner[];
  upcoming: { kind: string; key: string; priority: number; run_at: number; attempts: number }[];
};

export type Stats = {
  hours: number;
  series: { ts: number; kept: number; dropped: number; page_loads: number; api_calls: number; kept_median_s: number | null; dropped_median_s: number | null; ig_ok: number; ig_429: number }[];
  pace: { delay_s: number | null; cooling_down: boolean; cooldown_left_s: number; last_429: number | null; throttled_last_hour: number; requests_last_hour: number; throttle_rate_24h: number };
  metrics: Record<string, { ts: number; value: number }[]>;
  latest: Partial<Record<"chrome_mb" | "chrome_cpu" | "runners_mb" | "runners_cpu" | "api_mb" | "api_cpu" | "swap_used_mb" | "memory_free_pct" | "chrome_tabs" | "db_mb", number>>;
  speed: { profiles_last_hour: number; kept_last_hour: number; kept_median_s: number | null; dropped_median_s: number | null };
  requests: { page_loads: number; api_calls: number; per_kept_creator: number | null };
  cost: { llm_calls_total: number; llm_calls_today: number; llm_usd_total: number; scraping_ai_calls: number; notes: string };
};

export type SourceRow = { source: string; found: number; scraped: number; eligible: number; eligible_rate: number | null };
export type Sources = { types: SourceRow[]; seeds: SourceRow[] };

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? response.statusText);
  return data as T;
}

export const api = {
  summary: () => get<Summary>("/api/summary"),
  timeseries: () => get<TimePoint[]>("/api/timeseries"),
  activity: () => get<Activity[]>("/api/activity?limit=80"),
  creators: (params: URLSearchParams) => get<CreatorPage>(`/api/creators?${params}`),
  creator: (handle: string) => get<CreatorDetail>(`/api/creators/${encodeURIComponent(handle)}`),
  pipeline: () => get<Pipeline>("/api/pipeline"),
  sources: () => get<Sources>("/api/sources"),
  stats: (hours = 24) => get<Stats>(`/api/stats?hours=${hours}`),
  csvUrl: (params: URLSearchParams) => {
    const copy = new URLSearchParams(params);
    copy.set("format", "csv");
    return `/api/creators?${copy}`;
  },
  chat: (message: string, criteria: Record<string, string>) =>
    post<{ criteria: Record<string, string>; reply: string; engine: "llm" | "rules"; matches: number }>("/api/search/chat", { message, criteria }),
  assistant: (message: string, history: { role: string; text: string }[], filters: Record<string, string>) =>
    post<AssistantReply>("/api/assistant", { message, history, filters }),
  unblock: () => post<{ requeued: number }>("/api/actions/unblock"),
  retry: (body: { task_id?: number; reason?: string }) => post<{ requeued: number }>("/api/actions/retry", body),
  seed: (body: { usernames?: string; queries?: string; expand?: boolean }) =>
    post<{ queued: Record<string, number> }>("/api/actions/seed", body),
};
