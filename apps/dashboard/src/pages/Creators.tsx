import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import * as Slider from "@radix-ui/react-slider";
import clsx from "clsx";
import { ArrowDownUp, Briefcase, Download, LayoutGrid, Rows3, Search, SlidersHorizontal, X } from "lucide-react";
import { api, type Creator } from "../lib/api";
import { ago, compact, languageName, percent, sourceLabel } from "../lib/format";
import { useCreatorDrawer } from "../components/CreatorDrawer";
import { Avatar, Badge, Button, Card, Empty, Meter, Skeleton } from "../components/ui";

const SORTS = [
  { value: "recent", label: "Newest" },
  { value: "followers", label: "Followers" },
  { value: "engagement", label: "Engagement" },
  { value: "likes", label: "Median likes" },
  { value: "brand", label: "Brand deals" },
  { value: "handle", label: "Handle" },
];
const FOLLOWER_STEPS = [0, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 1_000_000, 10_000_000];

export function CreatorsPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [showFilters, setShowFilters] = useState(false);
  const [view, setView] = useState<"table" | "grid">(() => (localStorage.getItem("creators-view") as "grid") || "table");
  const open = useCreatorDrawer();
  const searchRef = useRef<HTMLInputElement>(null);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    next.delete("offset");
    setParams(next, { replace: true });
  };

  useEffect(() => {
    const handle = window.setTimeout(() => { if ((params.get("q") ?? "") !== query) update({ q: query }); }, 220);
    return () => window.clearTimeout(handle);
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => { try { localStorage.setItem("creators-view", view); } catch { /* ignore */ } }, [view]);

  const apiParams = useMemo(() => {
    const next = new URLSearchParams(params);
    next.delete("creator");
    if (!next.get("limit")) next.set("limit", "120");
    return next;
  }, [params]);
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["creators", apiParams.toString()],
    queryFn: () => api.creators(apiParams),
    placeholderData: keepPreviousData,
  });

  const scope = params.get("scope") ?? "all";
  const sort = params.get("sort") ?? "recent";
  const order = params.get("order") ?? "desc";
  const activeFilters = ["min_followers", "max_followers", "min_engagement", "category", "language", "source", "fresh_days", "brand", "india"].filter((key) => params.get(key));
  const seenFirst = useRef<Set<string> | null>(null);
  const fresh = useMemo(() => {
    const handles = new Set((data?.items ?? []).map((c) => c.handle));
    if (seenFirst.current === null) { seenFirst.current = handles; return new Set<string>(); }
    const added = new Set([...handles].filter((h) => !seenFirst.current!.has(h)));
    for (const h of handles) seenFirst.current.add(h);
    return added;
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[260px] flex-1">
          <Search size={15} className="absolute top-1/2 left-3 -translate-y-1/2 muted" />
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search handle, name, bio, city, hashtag…   ( / )"
            className="panel w-full py-2 pr-3 pl-9 text-[14px] outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40"
          />
        </div>
        <Segmented value={scope} onChange={(value) => update({ scope: value === "all" ? null : value })}
          options={[{ value: "all", label: "All scraped" }, { value: "eligible", label: "Kept" }]} />
        <Segmented value={params.get("kind") ?? ""} onChange={(value) => update({ kind: value || null })}
          options={[{ value: "", label: "Everyone" }, { value: "creator", label: "Creators" }, { value: "small business", label: "Small businesses" }]} />
        <div className="panel flex items-center gap-1 px-1 py-1">
          <ArrowDownUp size={14} className="ml-2 muted" />
          <select value={sort} onChange={(e) => update({ sort: e.target.value === "recent" ? null : e.target.value })} className="bg-transparent px-1 py-1 text-[13px] outline-none">
            {SORTS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <button onClick={() => update({ order: order === "desc" ? "asc" : null })} className="rounded-md px-2 py-1 text-[12px] muted hover:bg-[var(--panel-2)] cursor-pointer">{order === "desc" ? "↓" : "↑"}</button>
        </div>
        <button onClick={() => update({ brand: params.get("brand") ? null : "1" })}
          className={clsx("panel inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] cursor-pointer", params.get("brand") && "border-amber-500/60 text-amber-500")}>
          <Briefcase size={14} />Doing brand deals{data && <span className="muted">{data.facets.brand_ready}</span>}
        </button>
        <Button onClick={() => setShowFilters((v) => !v)} className={clsx(activeFilters.length && "border-[var(--color-accent)]/50")}>
          <SlidersHorizontal size={14} />Filters{activeFilters.length > 0 && <Badge tone="accent">{activeFilters.length}</Badge>}
        </Button>
        <div className="panel flex p-1">
          <button onClick={() => setView("table")} className={clsx("rounded-md p-1.5 cursor-pointer", view === "table" && "bg-[var(--panel-2)]")} title="Table"><Rows3 size={15} /></button>
          <button onClick={() => setView("grid")} className={clsx("rounded-md p-1.5 cursor-pointer", view === "grid" && "bg-[var(--panel-2)]")} title="Cards"><LayoutGrid size={15} /></button>
        </div>
        <a href={api.csvUrl(apiParams)}><Button><Download size={14} />CSV</Button></a>
      </div>

      {showFilters && data && <FilterPanel params={params} update={update} facets={data.facets} scope={scope} />}

      <div className="flex items-center justify-between text-[13px] muted">
        <span><span className="font-semibold text-[var(--text)] num">{data ? data.total.toLocaleString() : "…"}</span> creators{isFetching && " · updating…"}</span>
        {activeFilters.length > 0 && (
          <button className="inline-flex items-center gap-1 hover:underline cursor-pointer" onClick={() => update(Object.fromEntries(activeFilters.map((k) => [k, null])))}>
            <X size={13} />Clear filters
          </button>
        )}
      </div>

      {isLoading ? (
        <Skeleton className="h-96" />
      ) : !data?.items.length ? (
        <Card><Empty icon={<Search size={22} />} title="No creators match" hint="Try widening the follower range or clearing filters." /></Card>
      ) : view === "table" ? (
        <CreatorTable items={data.items} fresh={fresh} onOpen={open} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {data.items.map((creator) => <CreatorCard key={creator.handle} creator={creator} fresh={fresh.has(creator.handle)} onOpen={open} />)}
        </div>
      )}
    </div>
  );
}

function CreatorTable({ items, fresh, onOpen }: { items: Creator[]; fresh: Set<string>; onOpen: (h: string) => void }) {
  const maxEr = Math.max(0.0001, ...items.map((c) => c.engagement_rate ?? 0));
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-[980px] text-[13px]">
        <thead>
          <tr className="border-b hairline text-left text-[11px] tracking-wide uppercase muted">
            <th className="px-4 py-3 font-medium">Creator</th>
            <th className="px-3 py-3 text-right font-medium">Followers</th>
            <th className="px-3 py-3 font-medium">Engagement</th>
            <th className="px-3 py-3 text-right font-medium">Med. likes</th>
            <th className="px-3 py-3 font-medium">Brand deals</th>
            <th className="px-3 py-3 font-medium">Category</th>
            <th className="px-3 py-3 font-medium">Location</th>
            <th className="px-3 py-3 font-medium">Found via</th>
            <th className="px-4 py-3 text-right font-medium">Captured</th>
          </tr>
        </thead>
        <tbody>
          {items.map((creator) => {
            const source = sourceLabel(creator.found_via);
            return (
              <tr key={creator.handle} onClick={() => onOpen(creator.handle)}
                className={clsx("cursor-pointer border-b hairline last:border-0 hover:bg-[var(--panel-2)]", fresh.has(creator.handle) && "flash")}>
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <Avatar handle={creator.handle} size={32} />
                    <div className="min-w-0">
                      <div className="truncate font-medium">{creator.name || creator.handle}</div>
                      <div className="truncate text-[12px] muted">@{creator.handle}</div>
                    </div>
                    {creator.india_signals.length > 0 && <Badge tone="good" className="!px-1.5" >IN</Badge>}
                    {creator.kind === "small business" && <Badge tone="info">small business</Badge>}
                    {!creator.eligible && <Badge>out of scope</Badge>}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-right font-medium num">{compact(creator.followers)}</td>
                <td className="px-3 py-2.5">
                  <div className="flex w-32 items-center gap-2">
                    <span className="w-12 num">{percent(creator.engagement_rate, 1)}</span>
                    <Meter value={creator.engagement_rate ?? 0} max={maxEr} tone="good" />
                  </div>
                </td>
                <td className="px-3 py-2.5 text-right num">{compact(creator.median_likes)}</td>
                <td className="px-3 py-2.5"><BrandCell creator={creator} /></td>
                <td className="px-3 py-2.5">{creator.category ? <Badge tone="accent">{creator.category}</Badge> : <span className="muted">—</span>}</td>
                <td className="max-w-[180px] truncate px-3 py-2.5 muted">{creator.location || "—"}</td>
                <td className="px-3 py-2.5 text-[12px] muted">{source.kind === "seed" ? "seed" : <>{source.label} <span className="text-[var(--text)]">{source.kind === "search" ? `“${source.from}”` : `@${source.from}`}</span></>}</td>
                <td className="px-4 py-2.5 text-right text-[12px] muted num">{ago(creator.captured_ts)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CreatorCard({ creator, fresh, onOpen }: { creator: Creator; fresh: boolean; onOpen: (h: string) => void }) {
  return (
    <button onClick={() => onOpen(creator.handle)} className={clsx("panel flex flex-col gap-3 p-4 text-left transition hover:ring-1 hover:ring-[var(--color-accent)]/40 cursor-pointer", fresh && "flash")}>
      <div className="flex items-center gap-3">
        <Avatar handle={creator.handle} size={42} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold">{creator.name || creator.handle}</div>
          <div className="truncate text-[12px] muted">@{creator.handle}</div>
        </div>
      </div>
      <p className="line-clamp-2 min-h-[36px] text-[12.5px] muted">{creator.bio || "No bio"}</p>
      <div className="grid grid-cols-3 gap-2 text-center">
        <Mini label="Followers" value={compact(creator.followers)} />
        <Mini label="ER" value={percent(creator.engagement_rate, 1)} />
        <Mini label="Likes" value={compact(creator.median_likes)} />
      </div>
      <div className="flex flex-wrap gap-1">
        {creator.brand_ready && <Badge tone="warn"><Briefcase size={11} />{creator.brand_posts} brand post{creator.brand_posts === 1 ? "" : "s"}</Badge>}
        {creator.category && <Badge tone="accent">{creator.category}</Badge>}
        {creator.languages.slice(0, 3).map((l) => <Badge key={l}>{languageName(l)}</Badge>)}
      </div>
    </button>
  );
}

function BrandCell({ creator }: { creator: Creator }) {
  if (creator.brand_ready) {
    return (
      <span className="inline-flex items-center gap-1" title={`${creator.paid_partnerships} paid partnership label(s), ${creator.disclosed_promos} disclosed promo(s)`}>
        <Badge tone="warn"><Briefcase size={11} />{creator.brand_posts}</Badge>
        {creator.paid_partnerships > 0 && <span className="text-[11px] muted">paid</span>}
      </span>
    );
  }
  if (creator.open_to_collabs) return <Badge>open to collabs</Badge>;
  return <span className="muted">—</span>;
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel-2 rounded-lg py-1.5">
      <div className="text-[14px] font-semibold num">{value}</div>
      <div className="text-[10px] muted uppercase">{label}</div>
    </div>
  );
}

function Segmented({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <div className="panel flex p-1">
      {options.map((option) => (
        <button key={option.value} onClick={() => onChange(option.value)}
          className={clsx("rounded-md px-3 py-1 text-[13px] cursor-pointer", value === option.value ? "bg-[var(--panel-2)] font-medium" : "muted")}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

function FilterPanel({ params, update, facets, scope }: {
  params: URLSearchParams; update: (c: Record<string, string | null>) => void; scope: string;
  facets: { category: [string, number][]; language: [string, number][]; source: [string, number][]; brand_ready: number; india: number };
}) {
  const steps = scope === "eligible" ? FOLLOWER_STEPS.slice(1, 8) : FOLLOWER_STEPS;
  const minIdx = Math.max(0, steps.findIndex((s) => s >= Number(params.get("min_followers") ?? 0)));
  const maxParam = params.get("max_followers");
  const maxIdx = maxParam ? Math.max(0, steps.findIndex((s) => s >= Number(maxParam))) : steps.length - 1;
  const [range, setRange] = useState<[number, number]>([minIdx, maxIdx]);
  const chip = (key: string, value: string, label: string, count?: number) => {
    const active = params.get(key) === value;
    return (
      <button key={value} onClick={() => update({ [key]: active ? null : value })}
        className={clsx("rounded-full border px-2.5 py-1 text-[12px] cursor-pointer", active ? "border-[var(--color-accent)] bg-[var(--color-accent)]/15 text-[var(--color-accent-soft)]" : "hairline hover:bg-[var(--panel-2)]")}>
        {label}{count !== undefined && <span className="ml-1 muted">{count}</span>}
      </button>
    );
  };
  return (
    <div className="panel enter grid gap-6 p-5 md:grid-cols-2 xl:grid-cols-4">
      <div>
        <div className="mb-3 flex justify-between text-[12px] font-medium muted"><span>Followers</span><span className="num">{compact(steps[range[0]])} – {compact(steps[range[1]])}</span></div>
        <Slider.Root className="relative flex h-5 touch-none items-center select-none" min={0} max={steps.length - 1} step={1} value={range}
          onValueChange={(v) => setRange([v[0], v[1]])}
          onValueCommit={(v) => update({ min_followers: v[0] === 0 ? null : String(steps[v[0]]), max_followers: v[1] === steps.length - 1 ? null : String(steps[v[1]]) })}>
          <Slider.Track className="relative h-1.5 grow rounded-full bg-[var(--panel-2)]"><Slider.Range className="absolute h-full rounded-full bg-[var(--color-accent)]" /></Slider.Track>
          {[0, 1].map((i) => <Slider.Thumb key={i} className="block size-4 rounded-full border-2 border-[var(--color-accent)] bg-white shadow outline-none" />)}
        </Slider.Root>
        <div className="mt-4 mb-2 text-[12px] font-medium muted">Min engagement</div>
        <div className="flex flex-wrap gap-1.5">{["0.01", "0.02", "0.05"].map((v) => chip("min_engagement", v, `≥ ${Number(v) * 100}%`))}</div>
      </div>
      <div>
        <div className="mb-2 text-[12px] font-medium muted">Category</div>
        <div className="flex flex-wrap gap-1.5">{facets.category.map(([c, n]) => chip("category", c, c, n))}</div>
      </div>
      <div>
        <div className="mb-2 text-[12px] font-medium muted">Language</div>
        <div className="flex flex-wrap gap-1.5">{facets.language.map(([l, n]) => chip("language", l, languageName(l), n))}</div>
        <div className="mt-4 mb-2 text-[12px] font-medium muted">Signals</div>
        <div className="flex flex-wrap gap-1.5">{chip("brand", "1", "Doing brand deals", facets.brand_ready)}{chip("india", "1", "India evidence", facets.india)}</div>
        <div className="mt-4 mb-2 text-[12px] font-medium muted">Freshness</div>
        <div className="flex flex-wrap gap-1.5">{[["1", "24h"], ["7", "7 days"], ["30", "30 days"]].map(([v, l]) => chip("fresh_days", v, l))}</div>
      </div>
      <div>
        <div className="mb-2 text-[12px] font-medium muted">Found via</div>
        <div className="flex flex-wrap gap-1.5">{facets.source.map(([s, n]) => chip("source", s, s === "seed" ? "seed / earlier" : s, n))}</div>
      </div>
    </div>
  );
}
