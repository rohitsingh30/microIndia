import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, Cpu, Plus, RotateCw } from "lucide-react";
import { api } from "../lib/api";
import { ago, compact } from "../lib/format";
import { ReasonBars } from "../components/Charts";
import { AuthBanner } from "./Live";
import { Badge, Button, Card, Empty, Skeleton, useToast } from "../components/ui";

const STATE_COLORS: Record<string, string> = {
  queued: "#6d5efc", leased: "#38bdf8", done: "#22c55e", skipped: "#8a8aa0", failed: "#ef4444", auth_blocked: "#f59e0b",
};
const KIND_HELP: Record<string, string> = {
  "scrape.profile": "Full profile + recent posts capture, then the eligibility check",
  "source.similar": "Instagram’s similar accounts for an eligible creator",
  "source.search": "Instagram search for an India niche query",
  "source.list": "Usernames from a file you added",
};

export function PipelinePage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const pipeline = useQuery({ queryKey: ["pipeline"], queryFn: api.pipeline, refetchInterval: 15_000 });
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const client = useQueryClient();
  const toast = useToast();
  const retry = useMutation({
    mutationFn: api.retry,
    onSuccess: (r) => { toast("good", `${r.requeued} task(s) back in the queue`); client.invalidateQueries({ queryKey: ["pipeline"] }); },
    onError: (e) => toast("bad", String(e)),
  });
  const data = pipeline.data;

  return (
    <div className="space-y-5">
      <AuthBanner summary={summary.data} />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {data ? Object.entries(data.kinds).sort().map(([kind, states]) => {
          const total = Object.values(states).reduce((a, b) => a + b, 0);
          return (
            <div key={kind} className="panel p-4">
              <div className="flex items-baseline justify-between">
                <code className="text-[13px] font-semibold">{kind}</code>
                <span className="text-[12px] muted num">{compact(total)} total</span>
              </div>
              <p className="mt-1 min-h-[32px] text-[12px] muted">{KIND_HELP[kind] ?? ""}</p>
              <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-[var(--panel-2)]">
                {Object.entries(states).map(([state, n]) => (
                  <div key={state} title={`${state}: ${n}`} style={{ width: `${(n / total) * 100}%`, background: STATE_COLORS[state] ?? "#888" }} />
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[12px]">
                {Object.entries(states).map(([state, n]) => (
                  <span key={state} className="inline-flex items-center gap-1 num"><i className="size-2 rounded-full" style={{ background: STATE_COLORS[state] }} />{state} <span className="font-medium">{compact(n)}</span></span>
                ))}
              </div>
            </div>
          );
        }) : Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-36" />)}
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Runners" className="xl:col-span-2">
          {summary.data?.runners.length ? (
            <table className="w-full text-[13px]">
              <thead><tr className="text-left text-[11px] uppercase muted"><th className="pb-2 font-medium">Runner</th><th className="pb-2 font-medium">Works on</th><th className="pb-2 font-medium">Tabs</th><th className="pb-2 font-medium">Status</th><th className="pb-2 text-right font-medium">Processed</th><th className="pb-2 text-right font-medium">Heartbeat</th></tr></thead>
              <tbody>
                {summary.data.runners.map((runner) => (
                  <tr key={runner.runner_id} className="border-t hairline">
                    <td className="py-2.5 font-medium"><span className="inline-flex items-center gap-2"><Cpu size={14} className="muted" />{runner.runner_id}</span></td>
                    <td className="py-2.5"><code className="text-[12px] muted">{runner.kinds}</code></td>
                    <td className="py-2.5 num">{runner.tabs}</td>
                    <td className="py-2.5">{runner.alive ? <Badge tone={runner.status === "auth_blocked" ? "warn" : "good"}>{runner.status}</Badge> : <Badge tone="neutral">{runner.status}</Badge>}</td>
                    <td className="py-2.5 text-right num">{compact(runner.processed)}</td>
                    <td className="py-2.5 text-right muted num">{ago(runner.last_heartbeat)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty title="No runners reporting" hint="Start the supervisor: run/start-public-collection.sh --background" />}
          {summary.data?.chrome && (
            <div className="mt-4 flex items-center gap-2 text-[13px]">
              {summary.data.chrome.ok ? <CircleCheck size={15} className="text-green-500" /> : <CircleDashed size={15} className="text-red-500" />}
              Chrome {summary.data.chrome.ok ? "reachable" : `unreachable (${summary.data.chrome.consecutive_failures} checks) — the watchdog restarts it automatically`}
              <span className="muted">· checked {ago(summary.data.health_checked_at)}</span>
            </div>
          )}
        </Card>

        <SeedBox />
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Failures" action={data?.failures.length ? <Button onClick={() => retry.mutate({})}><RotateCw size={13} />Retry all</Button> : undefined}>
          {data?.failures.length ? (
            <ul className="space-y-2">
              {data.failures.map(([reason, count]) => (
                <li key={reason} className="flex items-center gap-2 text-[13px]">
                  <span className="flex-1 truncate">{reason}</span>
                  <Badge tone="bad">{count}</Badge>
                  <button className="text-[12px] text-[var(--color-accent-soft)] hover:underline cursor-pointer" onClick={() => retry.mutate({ reason })}>retry</button>
                </li>
              ))}
            </ul>
          ) : <Empty icon={<CircleCheck size={20} />} title="No failures" hint="Transient errors retry automatically with backoff." />}
        </Card>
        <Card title="Skip reasons (last 2,000)">
          {data?.skip_reasons.length ? <ReasonBars rows={data.skip_reasons} /> : <Empty title="Nothing skipped yet" />}
        </Card>
        <Card title="Up next">
          {data?.upcoming.length ? (
            <ul className="space-y-1.5 text-[13px]">
              {data.upcoming.map((task) => (
                <li key={`${task.kind}-${task.key}`} className="flex items-center gap-2">
                  <code className="text-[11px] muted">{task.kind.split(".")[1]}</code>
                  <span className="flex-1 truncate">{task.key}</span>
                  {task.attempts > 0 && <Badge tone="warn">retry {task.attempts}</Badge>}
                  <span className="text-[11px] muted num">p{task.priority}</span>
                </li>
              ))}
            </ul>
          ) : <Empty title="Queue is empty" />}
        </Card>
      </div>

      <Card title="Top seeds by eligible creators">
        {sources.data?.seeds.length ? (
          <table className="w-full text-[13px]">
            <thead><tr className="text-left text-[11px] uppercase muted"><th className="pb-2 font-medium">Seed</th><th className="pb-2 text-right font-medium">Found</th><th className="pb-2 text-right font-medium">Scraped</th><th className="pb-2 text-right font-medium">Eligible</th><th className="pb-2 text-right font-medium">Hit rate</th></tr></thead>
            <tbody>
              {sources.data.seeds.map((row) => (
                <tr key={row.source} className="border-t hairline">
                  <td className="py-2">{row.source}</td>
                  <td className="py-2 text-right num">{compact(row.found)}</td>
                  <td className="py-2 text-right num">{compact(row.scraped)}</td>
                  <td className="py-2 text-right font-medium num">{compact(row.eligible)}</td>
                  <td className="py-2 text-right num">{row.eligible_rate === null ? "—" : `${(row.eligible_rate * 100).toFixed(1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty title="No seeds yet" />}
      </Card>
    </div>
  );
}

function SeedBox() {
  const [usernames, setUsernames] = useState("");
  const [queries, setQueries] = useState("");
  const toast = useToast();
  const client = useQueryClient();
  const seed = useMutation({
    mutationFn: () => api.seed({ usernames, queries }),
    onSuccess: (r) => {
      const q = r.queued;
      toast("good", `Queued ${q["scrape.profile"] ?? 0} profiles, ${q["source.similar"] ?? 0} expansions, ${q["source.search"] ?? 0} searches`);
      setUsernames(""); setQueries("");
      client.invalidateQueries({ queryKey: ["pipeline"] });
    },
    onError: (e) => toast("bad", String(e)),
  });
  return (
    <Card title={<span id="seed">Add seeds</span>}>
      <p className="mb-3 text-[12px] muted">Creators you already like: they’re scraped first, and their similar accounts get expanded.</p>
      <textarea value={usernames} onChange={(e) => setUsernames(e.target.value)} rows={3} placeholder={"@creator_one\nhttps://instagram.com/creator_two"}
        className="panel-2 w-full rounded-lg border hairline p-2.5 text-[13px] outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40" />
      <input value={queries} onChange={(e) => setQueries(e.target.value)} placeholder="Search query, e.g. lucknow food blogger"
        className="panel-2 mt-2 w-full rounded-lg border hairline p-2.5 text-[13px] outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40" />
      <Button variant="primary" className="mt-3 w-full" disabled={seed.isPending || (!usernames.trim() && !queries.trim())} onClick={() => seed.mutate()}>
        <Plus size={14} />Queue
      </Button>
    </Card>
  );
}
