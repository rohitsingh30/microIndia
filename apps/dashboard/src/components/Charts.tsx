import { Area, Bar, CartesianGrid, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TimePoint } from "../lib/api";
import { clock, compact, percent } from "../lib/format";
import { Meter } from "./ui";

export function ThroughputChart({ data }: { data: TimePoint[] }) {
  return (
    <div className="h-64">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id="g-found" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#6d5efc" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#6d5efc" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="g-scraped" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="ts" tickFormatter={clock} tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={36} />
          <YAxis yAxisId="left" tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => compact(v)} />
          <YAxis yAxisId="right" orientation="right" hide />
          <Tooltip
            cursor={{ stroke: "var(--line)" }}
            contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12 }}
            labelFormatter={(ts) => clock(Number(ts))}
          />
          <Area yAxisId="left" type="monotone" dataKey="found" name="Found" stroke="#6d5efc" strokeWidth={1.75} fill="url(#g-found)" />
          <Area yAxisId="left" type="monotone" dataKey="scraped" name="Scraped" stroke="#38bdf8" strokeWidth={1.75} fill="url(#g-scraped)" />
          <Bar yAxisId="right" dataKey="eligible" name="In scope" fill="#22c55e" radius={[4, 4, 0, 0]} maxBarSize={14} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Funnel({ found, scraped, kept }: { found: number; scraped: number; kept: number; dropped?: number }) {
  const steps = [
    { label: "Found", value: found, hint: "usernames discovered", tone: "accent" as const },
    { label: "Scraped", value: scraped, hint: found ? `${percent(scraped / found, 0)} of found` : "", tone: "info" as const },
    { label: "Kept", value: kept, hint: scraped ? `${percent(kept / scraped, 0)} passed the guardrails` : "", tone: "good" as const },
  ];
  return (
    <div className="space-y-4">
      {steps.map((step) => (
        <div key={step.label}>
          <div className="mb-1.5 flex items-baseline justify-between">
            <span className="text-[13px] font-medium">{step.label}</span>
            <span className="text-[13px] num"><span className="font-semibold">{compact(step.value)}</span> <span className="muted">{step.hint}</span></span>
          </div>
          <Meter value={step.value} max={Math.max(found, 1)} tone={step.tone} />
        </div>
      ))}
    </div>
  );
}

export function ReasonBars({ rows }: { rows: [string, number][] }) {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  const total = rows.reduce((sum, [, n]) => sum + n, 0);
  return (
    <div className="space-y-3">
      {rows.map(([reason, count]) => (
        <div key={reason}>
          <div className="mb-1 flex justify-between text-[13px]">
            <span className="truncate pr-3">{reason}</span>
            <span className="muted num">{count} · {percent(count / Math.max(total, 1), 0)}</span>
          </div>
          <Meter value={count} max={max} tone="neutral" />
        </div>
      ))}
    </div>
  );
}
