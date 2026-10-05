import { useMutation, useQuery } from "@tanstack/react-query";
import { Activity as ActivityIcon, AlertTriangle, ChevronRight, Gauge, Layers, Lock, Sparkles, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { api, type Summary } from "../lib/api";
import { ago, compact, percent, sourceLabel } from "../lib/format";
import { ActivityFeed } from "../components/ActivityFeed";
import { Funnel, ReasonBars, ThroughputChart } from "../components/Charts";
import { useCreatorDrawer } from "../components/CreatorDrawer";
import { Avatar, Badge, Button, Card, Empty, Skeleton, Stat, useToast } from "../components/ui";

export function AuthBanner({ summary }: { summary: Summary | undefined }) {
  const toast = useToast();
  const unblock = useMutation({
    mutationFn: api.unblock,
    onSuccess: (r) => toast("good", `Resumed — ${r.requeued} tasks back in the queue`),
    onError: (e) => toast("bad", String(e)),
  });
  if (!summary?.auth_blocked) return null;
  return (
    <div className="panel enter flex flex-wrap items-center gap-3 border-amber-500/40 bg-amber-500/8 p-4">
      <Lock size={18} className="text-amber-500" />
      <div className="min-w-0 flex-1">
        <div className="text-[14px] font-semibold">Instagram wants you to sign in again</div>
        <div className="text-[13px] muted">
          Collection is paused since {ago(summary.auth_blocked.since)}. Open the collector’s Chrome window, sign in, then resume. Runners also re-check every minute.
        </div>
      </div>
      <Button variant="primary" onClick={() => unblock.mutate()} disabled={unblock.isPending}>I’ve signed in — resume</Button>
    </div>
  );
}

export function LivePage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 30_000 });
  const series = useQuery({ queryKey: ["timeseries"], queryFn: api.timeseries });
  const latest = useQuery({ queryKey: ["creators", "latest"], queryFn: () => api.creators(new URLSearchParams({ sort: "recent", limit: "8" })) });
  const pipeline = useQuery({ queryKey: ["pipeline"], queryFn: api.pipeline });
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const open = useCreatorDrawer();
  const s = summary.data;
  const points = series.data ?? [];

  return (
    <div className="space-y-5">
      <AuthBanner summary={s} />
      {s?.focus && (
        <div className="panel flex flex-wrap items-center gap-2 px-4 py-2.5 text-[13px]">
          <span className="text-[var(--color-accent-soft)]"><span className="live-dot inline-block align-middle" /></span>
          <span className="muted">Now exploring</span>
          <span className="font-semibold capitalize">{s.focus.niche}</span><span className="muted">in</span>
          <span className="font-semibold capitalize">{s.focus.city}</span><span className="muted">·</span>
          <span className="font-semibold">{s.focus.band} followers</span>
          <span className="ml-auto text-[12px] muted">{s.focus.captured} captured in this focus · switches every 30 min or 25 creators</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {s ? (
          <>
            <Stat label="Creators captured" icon={<Sparkles size={13} />} tone="good" value={compact(s.eligible_total)}
              hint={<><span className="font-medium text-green-500">+{s.captured_today}</span> today · <span className="text-amber-500">{s.brand_ready} doing brand deals</span> · {s.funnel.dropped} dropped by guardrails</>}
              series={points.map((p) => p.eligible)} />
            <Stat label="Profiles scraped / hour" icon={<Gauge size={13} />} tone="info" value={compact(s.scraped_last_hour)}
              hint={`${s.in_flight} in progress right now`} series={points.map((p) => p.scraped)} />
            <Stat label="New candidates / hour" icon={<Users size={13} />} value={compact(s.found_last_hour)}
              hint="from similar accounts, mentions and search" series={points.map((p) => p.found)} />
            <Stat label="Queue" icon={<Layers size={13} />} tone="warn" value={compact(s.queue)}
              hint={s.scraped_last_hour ? `≈ ${Math.max(1, Math.round(s.queue / s.scraped_last_hour))}h of work at current pace` : "profiles waiting to be scraped"} />
          </>
        ) : Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[118px]" />)}
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Card title="Last 24 hours" action={<Legend />}>
            {points.length ? <ThroughputChart data={points} /> : <Skeleton className="h-64" />}
          </Card>

          <div className="grid gap-5 md:grid-cols-2">
            <Card title="Funnel">
              {s ? <Funnel {...s.funnel} /> : <Skeleton className="h-28" />}
            </Card>
            <Card title="Why profiles get skipped" action={<Link to="/pipeline" className="text-[12px] muted hover:underline">Details</Link>}>
              {pipeline.data?.skip_reasons.length ? <ReasonBars rows={pipeline.data.skip_reasons.slice(0, 5)} /> : <Empty title="Nothing skipped yet" />}
            </Card>
          </div>

          <Card title="Newest creators" action={<Link to="/creators" className="inline-flex items-center text-[12px] muted hover:underline">All creators <ChevronRight size={13} /></Link>}>
            {latest.data?.items.length ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {latest.data.items.map((creator) => (
                  <button key={creator.handle} onClick={() => open(creator.handle)} className="panel-2 flex items-center gap-3 rounded-xl p-3 text-left transition hover:ring-1 hover:ring-[var(--color-accent)]/40 cursor-pointer">
                    <Avatar handle={creator.handle} size={40} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px] font-medium">{creator.name || `@${creator.handle}`}</div>
                      <div className="truncate text-[12px] muted">@{creator.handle} · {creator.location || creator.category || "—"}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[14px] font-semibold num">{compact(creator.followers)}</div>
                      <div className="text-[11px] muted num">{percent(creator.engagement_rate, 1)} ER</div>
                    </div>
                  </button>
                ))}
              </div>
            ) : <Empty title="No eligible creators yet" hint="They show up here as soon as one passes all checks." />}
          </Card>
        </div>

        <div className="space-y-5">
          <Card title={<span className="inline-flex items-center gap-2"><ActivityIcon size={13} />Live activity</span>} padded={false} className="xl:sticky xl:top-[76px]">
            <div className="max-h-[560px] overflow-y-auto"><ActivityFeed onOpen={open} /></div>
          </Card>

          <Card title="Best sources">
            {sources.data?.types.length ? (
              <table className="w-full text-[13px]">
                <thead><tr className="text-left text-[11px] muted"><th className="pb-2 font-medium">Source</th><th className="pb-2 text-right font-medium">Found</th><th className="pb-2 text-right font-medium">Eligible</th><th className="pb-2 text-right font-medium">Hit rate</th></tr></thead>
                <tbody>
                  {sources.data.types.map((row) => (
                    <tr key={row.source} className="border-t hairline">
                      <td className="py-2 capitalize">{sourceLabel(`${row.source}:`).kind === "seed" ? "Seed / earlier" : row.source}</td>
                      <td className="py-2 text-right num">{compact(row.found)}</td>
                      <td className="py-2 text-right num">{compact(row.eligible)}</td>
                      <td className="py-2 text-right num">{row.eligible_rate === null ? "—" : percent(row.eligible_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty title="No source data yet" />}
          </Card>

          {s && s.watchdog_actions.length > 0 && (
            <Card title="Self-healing">
              <ul className="space-y-2 text-[13px]">
                {s.watchdog_actions.slice().reverse().map((action, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <AlertTriangle size={14} className="mt-0.5 text-amber-500" />
                    <span className="flex-1">{action.action === "restart" ? `Restarted ${action.worker}` : "Closed hung tabs"} <span className="muted">— {action.reason ?? ""}</span></span>
                    <Badge>{ago(action.ts)}</Badge>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3 text-[11px] muted">
      <span className="inline-flex items-center gap-1"><i className="size-2 rounded-full bg-[#6d5efc]" />Found</span>
      <span className="inline-flex items-center gap-1"><i className="size-2 rounded-full bg-[#38bdf8]" />Scraped</span>
      <span className="inline-flex items-center gap-1"><i className="size-2 rounded-full bg-[#22c55e]" />In scope</span>
    </div>
  );
}
