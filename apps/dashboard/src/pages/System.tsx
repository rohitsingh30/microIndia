import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Cpu, DollarSign, Gauge, HardDrive, MemoryStick, MousePointerClick, ShieldAlert, Timer, Turtle } from "lucide-react";
import { api } from "../lib/api";
import { clock, compact } from "../lib/format";
import { Card, Empty, Skeleton, Stat } from "../components/ui";

const tooltip = { background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12 };
const axis = { fill: "var(--muted)", fontSize: 11 };

/** Speed, memory and cost of the collector, so it can be tuned with numbers instead of guesses. */
export function SystemPage() {
  const { data } = useQuery({ queryKey: ["stats"], queryFn: () => api.stats(24), refetchInterval: 30_000 });
  if (!data) return <div className="grid gap-4 md:grid-cols-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-28" />)}</div>;
  const latest = data.latest;
  const series = data.series.map((b) => ({ ...b, profiles: b.kept + b.dropped }));
  const memory = mergeMetrics(data.metrics, ["chrome_mb", "runners_mb", "api_mb", "swap_used_mb"]);
  const cpu = mergeMetrics(data.metrics, ["chrome_cpu", "runners_cpu"]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[20px] font-semibold">System</h1>
        <p className="text-[13px] muted">How fast the collector runs, what it costs, and how much of this Mac it uses. Last 24 hours, refreshed every 30s.</p>
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat label="Profiles / hour" icon={<Gauge size={13} />} tone="info" value={compact(data.speed.profiles_last_hour)}
          hint={`${data.speed.kept_last_hour} kept this hour`} series={series.map((b) => b.profiles)} />
        <Stat label="Seconds per profile" icon={<Timer size={13} />} value={data.speed.kept_median_s ? `${Math.round(data.speed.kept_median_s)}s` : "—"}
          hint={`kept (with posts) · dropped ${data.speed.dropped_median_s ? `${Math.round(data.speed.dropped_median_s)}s` : "—"}`} />
        <Stat label="Instagram requests / kept creator" icon={<MousePointerClick size={13} />} tone="warn"
          value={data.requests.per_kept_creator ?? "—"} hint={`${compact(data.requests.page_loads)} page loads · ${compact(data.requests.api_calls)} API calls (24h)`} />
        <Stat label="AI cost" icon={<DollarSign size={13} />} tone="good" value={`$${data.cost.llm_usd_total.toFixed(2)}`}
          hint={`${data.cost.llm_calls_total} LLM calls total · scraping uses none`} />
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat label="Instagram 429s (this hour)" icon={<ShieldAlert size={13} />} tone={data.pace.throttled_last_hour ? "warn" : "good"}
          value={String(data.pace.throttled_last_hour)}
          hint={`${compact(data.pace.requests_last_hour)} Instagram requests this hour · ${(data.pace.throttle_rate_24h * 100).toFixed(2)}% throttled (24h)`} />
        <Stat label="Pace (delay per tab)" icon={<Turtle size={13} />} tone={data.pace.cooling_down ? "warn" : "info"}
          value={data.pace.delay_s ? `${data.pace.delay_s.toFixed(1)}s` : "—"}
          hint={data.pace.cooling_down ? `cooling down: ${data.pace.cooldown_left_s}s left after a 429` : data.pace.last_429 ? `last 429 ${clock(data.pace.last_429)} · eases off 15% per 5 clean min` : "no 429 yet · eases off 15% per 5 clean min"} />
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat label="Chrome memory" icon={<MemoryStick size={13} />} value={latest.chrome_mb ? `${(latest.chrome_mb / 1024).toFixed(1)} GB` : "—"}
          hint={latest.chrome_tabs ? `${latest.chrome_tabs} tabs open` : "waiting for first sample"} />
        <Stat label="Chrome CPU" icon={<Cpu size={13} />} value={latest.chrome_cpu !== undefined ? `${Math.round(latest.chrome_cpu)}%` : "—"}
          hint={latest.runners_cpu !== undefined ? `runners ${Math.round(latest.runners_cpu)}% · 100% = one core` : undefined} />
        <Stat label="Mac memory" icon={<MemoryStick size={13} />} tone={latest.memory_free_pct !== undefined && latest.memory_free_pct < 20 ? "warn" : "good"}
          value={latest.memory_free_pct !== undefined ? `${latest.memory_free_pct}% free` : "—"}
          hint={latest.swap_used_mb ? `swap ${(latest.swap_used_mb / 1024).toFixed(1)} GB used` : undefined} />
        <Stat label="Dataset size" icon={<HardDrive size={13} />} value={latest.db_mb ? `${Math.round(latest.db_mb)} MB` : "—"}
          hint={`runners ${latest.runners_mb ? Math.round(latest.runners_mb) : "—"} MB · API ${latest.api_mb ? Math.round(latest.api_mb) : "—"} MB`} />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Profiles per hour (kept vs dropped)">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={series} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="ts" tickFormatter={clock} tick={axis} axisLine={false} tickLine={false} minTickGap={30} />
                <YAxis tick={axis} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltip} labelFormatter={(ts) => clock(Number(ts))} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="kept" stackId="p" name="Kept" fill="#22c55e" />
                <Bar dataKey="dropped" stackId="p" name="Dropped" fill="#8a8aa0" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Seconds per profile (median)">
          <div className="h-64">
            <ResponsiveContainer>
              <LineChart data={series} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="ts" tickFormatter={clock} tick={axis} axisLine={false} tickLine={false} minTickGap={30} />
                <YAxis tick={axis} axisLine={false} tickLine={false} unit="s" />
                <Tooltip contentStyle={tooltip} labelFormatter={(ts) => clock(Number(ts))} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="kept_median_s" name="Kept (with posts)" stroke="#22c55e" strokeWidth={2} dot={false} connectNulls />
                <Line type="monotone" dataKey="dropped_median_s" name="Dropped" stroke="#8a8aa0" strokeWidth={2} dot={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Memory (MB)">
          {memory.length ? (
            <div className="h-64">
              <ResponsiveContainer>
                <AreaChart data={memory} margin={{ top: 8, right: 8, left: -6, bottom: 0 }}>
                  <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                  <XAxis dataKey="ts" tickFormatter={clock} tick={axis} axisLine={false} tickLine={false} minTickGap={30} />
                  <YAxis tick={axis} axisLine={false} tickLine={false} tickFormatter={(v) => compact(v)} />
                  <Tooltip contentStyle={tooltip} labelFormatter={(ts) => clock(Number(ts))} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area type="monotone" dataKey="chrome_mb" name="Chrome" stroke="#6d5efc" fill="#6d5efc" fillOpacity={0.15} />
                  <Area type="monotone" dataKey="runners_mb" name="Runners" stroke="#38bdf8" fill="#38bdf8" fillOpacity={0.15} />
                  <Area type="monotone" dataKey="swap_used_mb" name="Swap used" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.08} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : <Empty title="Collecting memory samples" hint="The watchdog records one every 30 seconds." />}
        </Card>
        <Card title="Instagram requests per hour">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={series} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="ts" tickFormatter={clock} tick={axis} axisLine={false} tickLine={false} minTickGap={30} />
                <YAxis tick={axis} axisLine={false} tickLine={false} tickFormatter={(v) => compact(v)} />
                <Tooltip contentStyle={tooltip} labelFormatter={(ts) => clock(Number(ts))} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="page_loads" stackId="r" name="Page loads (profiles + posts)" fill="#6d5efc" />
                <Bar dataKey="api_calls" stackId="r" name="Search / similar calls" fill="#38bdf8" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Instagram responses per hour: OK vs 429" className="xl:col-span-2">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={series} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="ts" tickFormatter={clock} tick={axis} axisLine={false} tickLine={false} minTickGap={30} />
                <YAxis yAxisId="ok" tick={axis} axisLine={false} tickLine={false} tickFormatter={(v) => compact(v)} />
                <YAxis yAxisId="bad" orientation="right" tick={axis} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltip} labelFormatter={(ts) => clock(Number(ts))} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar yAxisId="ok" dataKey="ig_ok" name="OK responses" fill="#38bdf8" />
                <Bar yAxisId="bad" dataKey="ig_429" name="429 (throttled)" fill="#ef4444" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-[12px] muted">The safe pace is the highest OK-responses-per-hour that stays free of red bars. The pacer finds it on its own: a 429 doubles the delay and pauses all tabs for 2 minutes; every clean 5 minutes it speeds up 15%.</p>
        </Card>
        <Card title="CPU (% of one core)" className="xl:col-span-2">
          {cpu.length ? (
            <div className="h-48">
              <ResponsiveContainer>
                <LineChart data={cpu} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
                  <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                  <XAxis dataKey="ts" tickFormatter={clock} tick={axis} axisLine={false} tickLine={false} minTickGap={30} />
                  <YAxis tick={axis} axisLine={false} tickLine={false} unit="%" />
                  <Tooltip contentStyle={tooltip} labelFormatter={(ts) => clock(Number(ts))} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line type="monotone" dataKey="chrome_cpu" name="Chrome" stroke="#6d5efc" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="runners_cpu" name="Runners" stroke="#38bdf8" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : <Empty title="Collecting CPU samples" />}
          <p className="mt-3 text-[12px] muted">{data.cost.notes} Images, video and fonts are blocked in collector tabs, and tabs are recycled every 25 profiles to keep memory flat.</p>
        </Card>
      </div>
    </div>
  );
}

function mergeMetrics(metrics: Record<string, { ts: number; value: number }[]>, names: string[]) {
  const byTs = new Map<number, Record<string, number>>();
  for (const name of names) {
    for (const point of metrics[name] ?? []) {
      const row = byTs.get(point.ts) ?? { ts: point.ts };
      row[name] = point.value;
      byTs.set(point.ts, row);
    }
  }
  return [...byTs.values()].sort((a, b) => a.ts - b.ts);
}
