import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import clsx from "clsx";
import {
  ArrowLeft, ArrowUpRight, BadgeCheck, Briefcase, CalendarClock, ExternalLink, Heart, Lightbulb, MessageCircle,
  MapPin, Play, RefreshCw, Star, TrendingDown, TrendingUp, Users,
} from "lucide-react";
import { api, type CreatorDetail, type InsightPost } from "../lib/api";
import { ago, compact, percent } from "../lib/format";
import { Avatar, Badge, Button, Card, Empty, Skeleton, useToast } from "../components/ui";
import { useShortlist } from "../lib/shortlist";

export function CreatorPage() {
  const { handle = "" } = useParams();
  const { data, isLoading, error } = useQuery({ queryKey: ["creator", handle], queryFn: () => api.creator(handle) });
  if (isLoading) {
    return <div className="space-y-4"><Skeleton className="h-40" /><Skeleton className="h-28" /><Skeleton className="h-80" /></div>;
  }
  if (error || !data) {
    return <Card><Empty title={`@${handle} isn’t in the dataset yet`} hint={<Link to="/creators" className="underline">Back to all creators</Link>} /></Card>;
  }
  return <CreatorView data={data} />;
}

function CreatorView({ data }: { data: CreatorDetail }) {
  const navigate = useNavigate();
  const toast = useToast();
  const insight = data.insights;
  const rescrape = useMutation({
    mutationFn: () => api.seed({ usernames: data.handle, expand: true }),
    onSuccess: () => toast("good", `@${data.handle} queued for a fresh capture`),
  });
  const vsPeers = insight.engagement_rate && insight.peer_median_rate ? insight.engagement_rate / insight.peer_median_rate : null;
  const history = dedupeHistory(data.follower_history);
  const shortlist = useShortlist();
  const starred = shortlist.has(data.handle);

  return (
    <div className="space-y-5">
      <button onClick={() => navigate(-1)} className="inline-flex items-center gap-1 text-[13px] muted hover:text-[var(--text)] cursor-pointer"><ArrowLeft size={14} />Back</button>

      {/* Header */}
      <section className="panel flex flex-wrap items-start gap-5 p-6">
        <Avatar handle={data.handle} size={72} />
        <div className="min-w-[260px] flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-[24px] font-semibold tracking-tight">{data.name || `@${data.handle}`}</h1>
            {data.verified && <BadgeCheck size={20} className="text-sky-400" />}
          </div>
          <a href={data.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[14px] text-[var(--color-accent-soft)] hover:underline">@{data.handle}<ArrowUpRight size={14} /></a>
          {data.bio && <p className="mt-3 max-w-2xl text-[14px] leading-relaxed whitespace-pre-line">{data.bio}</p>}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Badge>{insight.band} followers</Badge>
            {data.city && <Badge><MapPin size={11} />{data.city}</Badge>}
            {data.category && data.category !== "other" && <Badge tone="accent">{data.category}</Badge>}
            {data.india_signals.length > 0 && <Badge tone="good">Indian creator</Badge>}
            {data.brand_ready && <Badge tone="warn"><Briefcase size={11} />{data.brand_posts} brand post{data.brand_posts === 1 ? "" : "s"}</Badge>}
            {!data.brand_ready && data.open_to_collabs && <Badge>Open to collabs</Badge>}
          </div>
        </div>
        <div className="flex w-full justify-end gap-2 md:w-auto md:flex-col">
          <a href={data.url} target="_blank" rel="noreferrer"><Button variant="primary" className="w-full"><ArrowUpRight size={14} />Instagram</Button></a>
          <Button onClick={() => shortlist.toggle(data)} className={starred ? "border-amber-500/50 text-amber-500" : ""}><Star size={14} fill={starred ? "currentColor" : "none"} />{starred ? "Shortlisted" : "Shortlist"}</Button>
          <Button onClick={() => rescrape.mutate()} disabled={rescrape.isPending}><RefreshCw size={14} />Refresh data</Button>
        </div>
      </section>

      {/* Takeaways */}
      {insight.summary.length > 0 && (
        <section className="panel border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.06] p-5">
          <h2 className="mb-2 flex items-center gap-2 text-[13px] font-semibold"><Lightbulb size={15} className="text-[var(--color-accent-soft)]" />Key takeaways</h2>
          <ul className="space-y-1.5 text-[14px]">{insight.summary.map((line) => <li key={line}>• {line}</li>)}</ul>
        </section>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi icon={<Users size={13} />} label="Followers" value={compact(data.followers)} hint={data.following ? `follows ${compact(data.following)}` : undefined} />
        <Kpi icon={<Heart size={13} />} label="Engagement (median)" value={percent(insight.engagement_rate, 1)}
          hint={vsPeers ? <span className={vsPeers >= 1 ? "text-green-500" : "text-amber-500"}>{vsPeers.toFixed(1)}× peers{insight.peer_percentile !== null ? ` · top ${Math.max(1, Math.round((1 - insight.peer_percentile) * 100))}%` : ""}</span> : `${insight.peer_count} peers`} />
        <Kpi icon={<Heart size={13} />} label="Median likes" value={compact(data.median_likes)} />
        <Kpi icon={<MessageCircle size={13} />} label="Comments / 100 likes" value={insight.comments_per_100_likes === null ? "—" : insight.comments_per_100_likes.toFixed(1)} hint="conversation depth" />
        <Kpi icon={<CalendarClock size={13} />} label="Posts / week" value={insight.cadence ? String(insight.cadence.posts_per_week) : "—"}
          hint={insight.cadence ? `last post ${Math.round(insight.cadence.last_post_days_ago)}d ago` : undefined} />
        <Kpi icon={<Play size={13} />} label="Reel reach" value={insight.reel_reach === null ? "—" : `${(insight.reel_reach * 100).toFixed(0)}%`} hint="median views ÷ followers" />
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Engagement per post over time" className="xl:col-span-2">
          {insight.timeline.length > 1 ? <Timeline data={insight.timeline} /> : <Empty title="Not enough dated posts yet" />}
        </Card>
        <Card title="What format works">
          {insight.formats.length ? (
            <table className="w-full text-[13px]">
              <thead><tr className="text-left text-[11px] uppercase muted"><th className="pb-2 font-medium">Format</th><th className="pb-2 text-right font-medium">Posts</th><th className="pb-2 text-right font-medium">Engagement</th><th className="pb-2 text-right font-medium">Views</th></tr></thead>
              <tbody>
                {insight.formats.map((format) => (
                  <tr key={format.type} className="border-t hairline">
                    <td className="py-2 capitalize">{format.type}</td>
                    <td className="py-2 text-right num">{format.posts}</td>
                    <td className="py-2 text-right num">{compact(format.median_engagement)} <span className="muted">({percent(format.median_rate, 1)})</span></td>
                    <td className="py-2 text-right num">{compact(format.median_views)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty title="No posts captured" />}
          {insight.consistency !== null && (
            <p className="mt-4 text-[12px] muted">
              Consistency {Math.round(insight.consistency * 100)}/100: {insight.consistency >= 0.6 ? "engagement spread across posts." : "a couple of posts carry most of the engagement."}
            </p>
          )}
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title={<span className="inline-flex items-center gap-2"><Briefcase size={13} />Brand activity</span>}>
          <div className="grid grid-cols-3 gap-2 text-center">
            <Mini label="Sponsored" value={String(insight.sponsored_posts)} />
            <Mini label="Share" value={insight.sponsored_share === null ? "—" : percent(insight.sponsored_share, 0)} />
            <Mini label="vs organic" value={insight.sponsored_lift === null ? "—" : `${insight.sponsored_lift > 0 ? "+" : ""}${Math.round(insight.sponsored_lift * 100)}%`}
              tone={insight.sponsored_lift === null ? undefined : insight.sponsored_lift > -0.25 ? "good" : "warn"} />
          </div>
          <p className="mt-3 text-[12px] muted">
            {insight.sponsored_posts
              ? "How the audience reacts to sponsored posts compared with organic ones. Close to 0% or above means ads don’t lose the audience."
              : data.open_to_collabs ? "No sponsored posts yet; bio invites collaborations." : "No sponsored posts detected in recent content."}
          </p>
        </Card>
        <Card title="Hashtags that lift engagement" className="xl:col-span-2">
          {insight.hashtags.length ? (
            <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {insight.hashtags.map((tag) => (
                <div key={tag.tag} className="flex items-center justify-between text-[13px]">
                  <span className="truncate">{tag.tag}</span>
                  <span className="flex items-center gap-2 num">
                    <span className="muted">{tag.posts} posts</span>
                    {tag.lift !== null && (
                      <span className={clsx("inline-flex items-center gap-0.5 font-medium", tag.lift >= 0 ? "text-green-500" : "text-amber-500")}>
                        {tag.lift >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}{Math.round(tag.lift * 100)}%
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          ) : <Empty title="Not enough repeated hashtags to compare" />}
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Best performing posts"><PostList posts={insight.best_posts} /></Card>
        <Card title="Weakest posts">{insight.weakest_posts.length ? <PostList posts={insight.weakest_posts} /> : <Empty title="Too few posts to compare" />}</Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Similar creators we found" className="xl:col-span-2">
          {data.similar.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {data.similar.map((peer) => (
                <Link key={peer.handle} to={`/creator/${peer.handle}`} className="panel-2 flex items-center gap-3 rounded-xl p-2.5 hover:ring-1 hover:ring-[var(--color-accent)]/40">
                  <Avatar handle={peer.handle} size={32} />
                  <div className="min-w-0 flex-1"><div className="truncate text-[13px] font-medium">{peer.name || peer.handle}</div><div className="truncate text-[11px] muted">@{peer.handle}</div></div>
                  <div className="text-right text-[12px] num"><div className="font-medium">{compact(peer.followers)}</div><div className="muted">{percent(peer.engagement_rate, 1)}</div></div>
                </Link>
              ))}
            </div>
          ) : <Empty title="No similar creators scraped yet" hint="They appear once this creator’s similar accounts have been expanded and scraped." />}
        </Card>
        <Card title="Profile & data">
          <dl className="space-y-2 text-[13px]">
            <Row label="Posts on profile" value={compact(data.posts)} />
            <Row label="Location" value={data.location || "—"} />
            {data.external_url && <Row label="Link" value={<a href={data.external_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[var(--color-accent-soft)] hover:underline">{new URL(data.external_url).hostname}<ExternalLink size={11} /></a>} />}
            <Row label="Found via" value={data.provenance.map((step) => step.via === "seed" ? "seed" : `${step.via} ${step.from}`).join(" ← ") || "—"} />
            <Row label="Captured" value={`${ago(data.captured_ts)} · ${data.captures} capture${data.captures === 1 ? "" : "s"}`} />
            <Row label="Posts analysed" value={String(data.posts_sample.length)} />
          </dl>
          {history.length > 1 && (
            <div className="mt-4 h-24">
              <ResponsiveContainer>
                <LineChart data={history}><XAxis dataKey="label" hide /><YAxis hide domain={["auto", "auto"]} /><Tooltip contentStyle={tooltipStyle} />
                  <Line type="monotone" dataKey="followers" stroke="#22c55e" strokeWidth={2} dot={false} /></LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

const tooltipStyle = { background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12 };

function dedupeHistory(points: { ts: number; followers: number }[]) {
  const distinct = new Set(points.map((p) => p.followers));
  if (distinct.size < 2) return [];
  return points.map((p) => ({ ...p, label: new Date(p.ts * 1000).toLocaleDateString() }));
}

function Timeline({ data }: { data: { ts: number; engagement: number; type: string; sponsored: boolean }[] }) {
  const color = (point: { type: string; sponsored: boolean }) => (point.sponsored ? "#f59e0b" : point.type === "reel" ? "#6d5efc" : "#38bdf8");
  return (
    <>
      <div className="h-64">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: -10 }}>
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="ts" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(ts) => new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}
              tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis dataKey="engagement" type="number" tickFormatter={(v) => compact(v)} tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
            <ZAxis range={[70, 70]} />
            <Tooltip contentStyle={tooltipStyle} formatter={(value: number, name: string) => [name === "ts" ? new Date(value * 1000).toLocaleDateString() : compact(value), name === "ts" ? "date" : "engagement"]} />
            <Scatter data={data}>{data.map((point, index) => <Cell key={index} fill={color(point)} />)}</Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex gap-4 text-[11px] muted">
        <span className="inline-flex items-center gap-1"><i className="size-2 rounded-full bg-[#6d5efc]" />Reel</span>
        <span className="inline-flex items-center gap-1"><i className="size-2 rounded-full bg-[#38bdf8]" />Post</span>
        <span className="inline-flex items-center gap-1"><i className="size-2 rounded-full bg-[#f59e0b]" />Sponsored</span>
      </div>
    </>
  );
}

function PostList({ posts }: { posts: InsightPost[] }) {
  if (!posts.length) return <Empty title="No posts with metrics" />;
  return (
    <div className="space-y-2">
      {posts.map((post, index) => (
        <a key={post.permalink ?? index} href={post.permalink ?? undefined} target="_blank" rel="noreferrer" className="panel-2 block rounded-xl p-3 hover:ring-1 hover:ring-[var(--color-accent)]/40">
          <div className="mb-1 flex items-center gap-3 text-[12px] muted num">
            <Badge>{post.type}</Badge>
            {post.sponsored && <Badge tone="warn"><Briefcase size={10} />{post.paid_partnership ? "paid partnership" : "sponsored"}</Badge>}
            <span className="inline-flex items-center gap-1"><Heart size={12} />{compact(post.likes)}</span>
            <span className="inline-flex items-center gap-1"><MessageCircle size={12} />{compact(post.comments)}</span>
            {post.views !== null && <span className="inline-flex items-center gap-1"><Play size={12} />{compact(post.views)}</span>}
            <span className="ml-auto font-medium text-[var(--text)]">{percent(post.rate, 1)}</span>
          </div>
          <p className="line-clamp-2 text-[13px]">{post.caption || <span className="muted">No caption</span>}</p>
          {post.published_ts && <div className="mt-1 text-[11px] muted">{new Date(post.published_ts * 1000).toLocaleDateString()}</div>}
        </a>
      ))}
    </div>
  );
}

function Kpi({ icon, label, value, hint }: { icon: React.ReactNode; label: string; value: string; hint?: React.ReactNode }) {
  return (
    <div className="panel p-4">
      <div className="flex items-center gap-1.5 text-[11px] font-medium muted">{icon}{label}</div>
      <div className="mt-1 text-[22px] font-semibold num">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] muted">{hint}</div>}
    </div>
  );
}

function Mini({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" }) {
  return (
    <div className="panel-2 rounded-lg py-2">
      <div className={clsx("text-[18px] font-semibold num", tone === "good" && "text-green-500", tone === "warn" && "text-amber-500")}>{value}</div>
      <div className="text-[10px] uppercase muted">{label}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex justify-between gap-4"><dt className="muted">{label}</dt><dd className="truncate text-right">{value}</dd></div>;
}
