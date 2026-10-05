import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleSlash, Lock, RotateCw, Search, Sparkles, Users } from "lucide-react";
import type { Activity } from "../lib/api";
import { ago, compact } from "../lib/format";
import { useLive } from "../lib/live";
import { Empty } from "./ui";

function describe(item: Activity): { icon: JSX.Element; title: JSX.Element; detail: string | null; tone: string } {
  const handle = <span className="font-semibold">@{item.key}</span>;
  if (item.kind === "scrape.profile") {
    if (item.state === "done" && item.eligible) {
      return { icon: <Sparkles size={15} />, tone: "text-green-500", title: <>New creator {handle}</>, detail: item.followers ? `${compact(item.followers)} followers` : null };
    }
    if (item.state === "skipped") {
      return { icon: <CircleSlash size={15} />, tone: "muted", title: <>Skipped {handle}</>, detail: shortReason(item.reason) };
    }
    if (item.state === "done") {
      return { icon: <CheckCircle2 size={15} />, tone: "text-sky-400", title: <>Refreshed {handle}</>, detail: null };
    }
  }
  if (item.kind === "source.similar" && item.state === "done") {
    return { icon: <Users size={15} />, tone: "text-[var(--color-accent-soft)]", title: <>Found {item.found} similar to {handle}</>, detail: null };
  }
  if (item.kind === "source.search" && item.state === "done") {
    return { icon: <Search size={15} />, tone: "text-[var(--color-accent-soft)]", title: <>Search “{item.key}” found {item.found}</>, detail: null };
  }
  if (item.state === "retrying") {
    return { icon: <RotateCw size={15} />, tone: "text-amber-500", title: <>Retrying {handle}</>, detail: shortReason(item.reason) };
  }
  if (item.state === "auth_blocked") {
    return { icon: <Lock size={15} />, tone: "text-amber-500", title: <>Login wall on {handle}</>, detail: "Collection paused until you sign in" };
  }
  if (item.state === "failed") {
    return { icon: <AlertTriangle size={15} />, tone: "text-red-500", title: <>Failed {handle}</>, detail: shortReason(item.reason) };
  }
  return { icon: <CircleSlash size={15} />, tone: "muted", title: <>{item.kind} {handle}</>, detail: shortReason(item.reason) };
}

export function shortReason(reason: string | null): string | null {
  if (!reason) return null;
  const first = reason.split(";")[0].replace(/^(ELIGIBILITY|QUALITY_GATE):/, "").trim();
  if (/outside .*(band|100K)/i.test(first)) return "Outside follower band";
  if (/no India/i.test(first)) return "No India signal";
  if (/INSUFFICIENT_CONTENT/.test(first)) return "Too few posts";
  if (/private/i.test(first)) return "Private account";
  return first.length > 70 ? `${first.slice(0, 70)}…` : first;
}

export function ActivityFeed({ onOpen, filter = "all", limit = 40 }: {
  onOpen: (handle: string) => void; filter?: "all" | "creators"; limit?: number;
}) {
  const { activity, newest } = useLive();
  const items = activity.filter((item) => filter === "all" || (item.kind === "scrape.profile" && item.eligible)).slice(0, limit);
  if (!items.length) return <Empty title="Waiting for activity" hint="Events appear here the moment a runner finishes a task." />;
  return (
    <ol className="divide-y divide-[var(--line)]">
      {items.map((item) => {
        const { icon, title, detail, tone } = describe(item);
        const clickable = item.kind === "scrape.profile" && item.state !== "failed";
        return (
          <li
            key={`${item.id}-${item.ts}`}
            onClick={clickable ? () => onOpen(item.key) : undefined}
            className={clsx("flex items-start gap-3 px-5 py-2.5", newest.has(item.id) && "enter flash", clickable && "cursor-pointer hover:bg-[var(--panel-2)]")}
          >
            <span className={clsx("mt-0.5", tone)}>{icon}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px]">{title}</div>
              {detail && <div className="truncate text-[12px] muted">{detail}</div>}
            </div>
            <span className="shrink-0 text-[11px] muted num">{ago(item.ts)}</span>
          </li>
        );
      })}
    </ol>
  );
}
