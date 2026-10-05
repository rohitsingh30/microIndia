import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import clsx from "clsx";
import {
  ArrowRight, ArrowUp, Briefcase, CalendarClock, Download, Dumbbell, Heart, MapPin, Plane, RotateCcw, Shirt,
  SlidersHorizontal, Sparkles, Star, UtensilsCrossed, X,
} from "lucide-react";
import { api, type AssistantReply, type Creator } from "../lib/api";
import { compact, languageName, percent } from "../lib/format";
import { useShortlist } from "../lib/shortlist";
import { Avatar, Skeleton } from "../components/ui";

const EXAMPLES = [
  { icon: UtensilsCrossed, text: "Food creators in Hyderabad under 50K who have done brand deals" },
  { icon: Shirt, text: "Fashion and saree creators in Mumbai with high engagement" },
  { icon: Plane, text: "Telugu travel creators who posted recently" },
  { icon: Dumbbell, text: "Nano fitness creators in Bangalore" },
];
const LABELS: Record<string, (v: string) => string> = {
  q: (v) => `“${v}”`, city: (v) => v, category: (v) => v[0].toUpperCase() + v.slice(1), language: (v) => languageName(v),
  min_followers: (v) => `${compact(Number(v))}+ followers`, max_followers: (v) => `up to ${compact(Number(v))} followers`,
  min_engagement: (v) => `${Math.round(Number(v) * 100)}%+ engagement`, india: () => "India confirmed",
  brand: () => "Brand-deal experience", active_days: (v) => `Active in last ${v} days`,
};
type Turn = { role: "user" | "assistant"; text: string; reply?: AssistantReply };
const FILTER_KEYS = ["city", "category", "language", "min_followers", "max_followers", "min_engagement", "brand", "active_days"];
const THREAD_KEY = "find-thread";

function loadThread(): Turn[] {
  try { return JSON.parse(sessionStorage.getItem(THREAD_KEY) || "[]"); } catch { return []; }
}

/** Brand-facing assistant: describe the campaign, get recommended creators with reasons. */
export function FindPage() {
  const [params, setParams] = useSearchParams();
  const [turns, setTurns] = useState<Turn[]>(loadThread);
  const filters = useMemo(() => Object.fromEntries(FILTER_KEYS.filter((k) => params.get(k)).map((k) => [k, params.get(k)!])), [params]);
  const filterKey = JSON.stringify(filters);
  useEffect(() => { try { sessionStorage.setItem(THREAD_KEY, JSON.stringify(turns)); } catch { /* ignore */ } }, [turns]);

  const ask = useMutation({
    mutationFn: ({ message, history }: { message: string; history: Turn[] }) =>
      api.assistant(message, history.map(({ role, text }) => ({ role, text })), filters),
    onSuccess: (reply) => setTurns((t) => [...t, { role: "assistant", text: reply.answer, reply }]),
    onError: (e) => setTurns((t) => [...t, { role: "assistant", text: `Something went wrong: ${String(e)}` }]),
  });
  const send = (text: string) => {
    const message = text.trim();
    if (!message || ask.isPending) return;
    const history = turns;
    setTurns((t) => [...t, { role: "user", text: message }]);
    ask.mutate({ message, history });
  };
  // Changing a filter re-runs the latest request inside the new filters.
  const lastFilters = useRef(filterKey);
  useEffect(() => {
    if (lastFilters.current === filterKey) return;
    lastFilters.current = filterKey;
    const lastUser = [...turns].reverse().find((t) => t.role === "user");
    if (lastUser && !ask.isPending) ask.mutate({ message: lastUser.text, history: turns.slice(0, turns.lastIndexOf(lastUser)) });
  }, [filterKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const reset = () => { setTurns([]); setParams(new URLSearchParams(), { replace: true }); };
  const setFilter = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null || value === "") next.delete(key); else next.set(key, value);
    setParams(next, { replace: true });
  };

  return turns.length
    ? <Conversation turns={turns} pending={ask.isPending} filters={filters} setFilter={setFilter} onSend={send} onReset={reset} />
    : <Landing onSend={send} />;
}

/* ---------- landing: one big prompt ---------- */
function Landing({ onSend }: { onSend: (text: string) => void }) {
  const [draft, setDraft] = useState("");
  const { data } = useQuery({ queryKey: ["creators", "find-stats"], queryFn: () => api.creators(new URLSearchParams({ scope: "eligible", limit: "1" })) });
  return (
    <div className="mx-auto flex min-h-[calc(100vh-140px)] max-w-3xl flex-col justify-center pb-16">
      <div className="mb-3 inline-flex items-center gap-2 self-center rounded-full border hairline px-3 py-1 text-[12px] muted">
        <Sparkles size={12} className="text-[var(--color-accent-soft)]" />Creator discovery for brands
      </div>
      <h1 className="text-center text-[40px] leading-tight font-semibold tracking-tight">Find the right creators<br />for your brand</h1>
      <p className="mx-auto mt-3 max-w-xl text-center text-[15px] muted">
        Describe your campaign in plain words. We search {data ? <b className="text-[var(--text)]">{data.total.toLocaleString()}</b> : "our"} Indian creators and show who fits, and why.
      </p>
      <PromptBox value={draft} onChange={setDraft} onSubmit={() => onSend(draft)} big placeholder="e.g. Food creators in Pune under 30K who post reels and have worked with brands" />
      <div className="mt-6 grid gap-2.5 sm:grid-cols-2">
        {EXAMPLES.map(({ icon: Icon, text }) => (
          <button key={text} onClick={() => onSend(text)} className="panel group flex items-center gap-3 p-3.5 text-left text-[13.5px] transition hover:border-[var(--color-accent)]/50 cursor-pointer">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[var(--panel-2)]"><Icon size={15} className="text-[var(--color-accent-soft)]" /></span>
            <span className="flex-1">{text}</span>
            <ArrowRight size={14} className="muted opacity-0 transition group-hover:opacity-100" />
          </button>
        ))}
      </div>
      {data && (
        <div className="mt-8 flex flex-wrap justify-center gap-x-6 gap-y-2 text-[12.5px] muted">
          <span><b className="text-[var(--text)]">{data.facets.city?.length ?? 0}+</b> cities</span>
          <span><b className="text-[var(--text)]">{data.facets.brand_ready}</b> with brand-deal experience</span>
          <span><b className="text-[var(--text)]">{data.facets.india}</b> confirmed Indian</span>
          <span>updated live</span>
        </div>
      )}
    </div>
  );
}

function PromptBox({ value, onChange, onSubmit, placeholder, big, pending }: {
  value: string; onChange: (v: string) => void; onSubmit: () => void; placeholder: string; big?: boolean; pending?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
      className={clsx("panel flex items-end gap-2 shadow-[0_8px_40px_-12px_rgba(109,94,252,0.35)] focus-within:border-[var(--color-accent)]/60", big ? "mt-8 p-3" : "p-2")}>
      <textarea ref={ref} value={value} onChange={(e) => onChange(e.target.value)} rows={big ? 2 : 1} placeholder={placeholder}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(); } }}
        className={clsx("w-full resize-none bg-transparent px-2 outline-none placeholder:text-[var(--muted)]", big ? "py-1.5 text-[16px]" : "py-2 text-[14px]")} />
      <button type="submit" disabled={!value.trim() || pending} aria-label="Search"
        className="grid size-10 shrink-0 place-items-center rounded-xl bg-[var(--color-accent)] text-white transition hover:bg-[var(--color-accent-soft)] disabled:opacity-35 cursor-pointer">
        <ArrowUp size={18} />
      </button>
    </form>
  );
}

/* ---------- conversation ---------- */
function Conversation({ turns, pending, filters, setFilter, onSend, onReset }: {
  turns: Turn[]; pending: boolean; filters: Record<string, string>; setFilter: (k: string, v: string | null) => void;
  onSend: (t: string) => void; onReset: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [refineOpen, setRefineOpen] = useState(false);
  const shortlist = useShortlist();
  const endRef = useRef<HTMLDivElement>(null);
  const { data: facetsData } = useQuery({ queryKey: ["creators", "facets"], queryFn: () => api.creators(new URLSearchParams({ scope: "eligible", limit: "1" })), staleTime: 60_000 });
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns.length, pending]);

  return (
    <div className="mx-auto max-w-6xl pb-32">
      <div className="sticky top-[60px] z-10 -mx-6 flex flex-wrap items-center gap-2 border-b hairline bg-[var(--bg)]/90 px-6 py-3 backdrop-blur-xl">
        <button onClick={onReset} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[13px] muted hover:text-[var(--text)] cursor-pointer"><RotateCcw size={13} />New conversation</button>
        <span className="mx-1 h-4 w-px bg-[var(--line)]" />
        {Object.entries(filters).map(([key, value]) => (
          <button key={key} onClick={() => setFilter(key, null)}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-accent)]/35 bg-[var(--color-accent)]/10 px-3 py-1 text-[12.5px] text-[var(--color-accent-soft)] hover:bg-[var(--color-accent)]/20 cursor-pointer">
            {LABELS[key]?.(value) ?? value}<X size={12} className="opacity-70" />
          </button>
        ))}
        <button onClick={() => setRefineOpen((v) => !v)} className={clsx("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12.5px] cursor-pointer", refineOpen ? "border-[var(--text)]/40" : "hairline muted hover:text-[var(--text)]")}>
          <SlidersHorizontal size={12} />Filters
        </button>
        <div className="ml-auto"><ShortlistButton shortlist={shortlist} /></div>
        {refineOpen && facetsData && <div className="w-full"><Refine criteria={filters} set={setFilter} facets={facetsData.facets} /></div>}
      </div>

      <div className="mt-6 space-y-8">
        {turns.map((turn, index) => turn.role === "user" ? (
          <div key={index} className="flex justify-end">
            <div className="max-w-[75%] rounded-2xl rounded-br-md bg-[var(--color-accent)] px-4 py-2.5 text-[15px] text-white">{turn.text}</div>
          </div>
        ) : (
          <AssistantTurn key={index} turn={turn} shortlist={shortlist} onFollowUp={onSend} latest={index === turns.length - 1} />
        ))}
        {pending && (
          <div className="flex items-start gap-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[var(--color-accent)]/15"><Sparkles size={15} className="text-[var(--color-accent-soft)]" /></span>
            <div className="w-full space-y-3">
              <div className="h-4 w-2/3 animate-pulse rounded bg-[var(--panel-2)]" />
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-56" />)}</div>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-[var(--bg)] via-[var(--bg)] to-transparent pt-8 pb-5">
        <div className="mx-auto max-w-3xl px-6">
          <PromptBox value={draft} onChange={setDraft} pending={pending} onSubmit={() => { onSend(draft); setDraft(""); }}
            placeholder="Ask a follow-up: “which of these does best on reels?”, “anyone in Pune?”, “compare the top 3”…" />
        </div>
      </div>
    </div>
  );
}

function AssistantTurn({ turn, shortlist, onFollowUp, latest }: {
  turn: Turn; shortlist: ReturnType<typeof useShortlist>; onFollowUp: (t: string) => void; latest: boolean;
}) {
  const reply = turn.reply;
  return (
    <div className="flex items-start gap-3">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[var(--color-accent)]/15"><Sparkles size={15} className="text-[var(--color-accent-soft)]" /></span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] leading-relaxed">{turn.text}</p>
        {reply?.engine === "retrieval" && (
          <p className="mt-1.5 text-[12px] text-amber-500/90">AI assistant not connected yet. These are ranked by relevance to your brief, engagement and brand experience, without AI reasoning.</p>
        )}
        {reply && reply.picks.length > 0 && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {reply.picks.map((pick, rank) => (
              <CreatorCard key={pick.handle} creator={pick.creator} why={pick.why} rank={rank + 1}
                starred={shortlist.has(pick.handle)} onStar={() => shortlist.toggle(pick.creator)} />
            ))}
          </div>
        )}
        {reply && latest && reply.follow_ups.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {reply.follow_ups.map((question) => (
              <button key={question} onClick={() => onFollowUp(question)} className="rounded-full border hairline px-3 py-1 text-[12.5px] muted hover:text-[var(--text)] hover:bg-[var(--panel-2)] cursor-pointer">{question}</button>
            ))}
          </div>
        )}
        {reply && <p className="mt-3 text-[12px] muted">Considered {reply.matches} matching creator{reply.matches === 1 ? "" : "s"}.</p>}
      </div>
    </div>
  );
}

function CreatorCard({ creator, starred, onStar, why, rank }: { creator: Creator; starred: boolean; onStar: () => void; why?: string; rank?: number }) {
  const navigate = useNavigate();
  return (
    <article onClick={() => navigate(`/creator/${creator.handle}`)}
      className="panel group flex cursor-pointer flex-col p-4 transition hover:-translate-y-0.5 hover:border-[var(--color-accent)]/45 hover:shadow-[0_12px_40px_-16px_rgba(109,94,252,0.45)]">
      {why && (
        <p className="mb-3 rounded-lg bg-[var(--color-accent)]/8 px-3 py-2 text-[12.5px] leading-snug">
          {rank && <span className="mr-1.5 font-semibold text-[var(--color-accent-soft)]">#{rank}</span>}{why}
        </p>
      )}
      <div className="flex items-start gap-3">
        <Avatar handle={creator.handle} size={46} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-semibold">{creator.name || creator.handle}</div>
          <div className="truncate text-[12.5px] muted">@{creator.handle}</div>
        </div>
        <button onClick={(e) => { e.stopPropagation(); onStar(); }} aria-label="Shortlist"
          className={clsx("grid size-8 place-items-center rounded-lg transition hover:bg-[var(--panel-2)] cursor-pointer", starred ? "text-amber-400" : "muted")}>
          <Star size={16} fill={starred ? "currentColor" : "none"} />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12px] muted">
        {creator.city && <span className="inline-flex items-center gap-1"><MapPin size={11} />{creator.city}</span>}
        {creator.category && creator.category !== "other" && <span className="capitalize">{creator.category}</span>}
        {creator.last_post_ts && <span className="inline-flex items-center gap-1"><CalendarClock size={11} />posted {Math.max(0, Math.round((Date.now() / 1000 - creator.last_post_ts) / 86400))}d ago</span>}
      </div>
      {creator.bio && <p className="mt-2.5 line-clamp-2 text-[13px] leading-relaxed">{creator.bio}</p>}
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Metric label="Followers" value={compact(creator.followers)} />
        <Metric label="Engagement" value={percent(creator.engagement_rate, 1)} icon={<Heart size={10} />} />
        <Metric label="Brand deals" value={creator.brand_posts ? String(creator.brand_posts) : creator.open_to_collabs ? "Open" : "—"} icon={<Briefcase size={10} />} highlight={creator.brand_posts > 0} />
      </div>

    </article>
  );
}

function Metric({ label, value, icon, highlight }: { label: string; value: string; icon?: React.ReactNode; highlight?: boolean }) {
  return (
    <div className={clsx("rounded-lg px-2 py-1.5", highlight ? "bg-amber-500/10" : "bg-[var(--panel-2)]")}>
      <div className={clsx("text-[15px] font-semibold num", highlight && "text-amber-400")}>{value}</div>
      <div className="flex items-center gap-1 text-[10.5px] muted">{icon}{label}</div>
    </div>
  );
}

function Refine({ criteria, set, facets }: {
  criteria: Record<string, string>; set: (k: string, v: string | null) => void;
  facets: { category: [string, number][]; language: [string, number][]; city?: [string, number][]; brand_ready: number; india: number };
}) {
  const pill = (active: boolean, label: React.ReactNode, onClick: () => void) => (
    <button onClick={onClick} className={clsx("rounded-full border px-2.5 py-1 text-[12px] cursor-pointer",
      active ? "border-[var(--color-accent)] bg-[var(--color-accent)]/15 text-[var(--color-accent-soft)]" : "hairline hover:bg-[var(--panel-2)]")}>{label}</button>
  );
  const sizes: [string, string, string][] = [["1000", "10000", "1K–10K"], ["10000", "50000", "10K–50K"], ["50000", "100000", "50K–100K"]];
  return (
    <div className="panel enter mt-3 grid gap-5 p-4 md:grid-cols-2 lg:grid-cols-4">
      <Group title="Audience size">{sizes.map(([lo, hi, label]) => pill(criteria.min_followers === lo && criteria.max_followers === hi, label, () => { set("min_followers", lo); set("max_followers", hi); }))}</Group>
      <Group title="Must have">
        {pill(!!criteria.brand, "Brand-deal experience", () => set("brand", criteria.brand ? null : "1"))}
        {pill(!!criteria.active_days, "Active last 30 days", () => set("active_days", criteria.active_days ? null : "30"))}
        {pill(criteria.min_engagement === "0.03", "3%+ engagement", () => set("min_engagement", criteria.min_engagement === "0.03" ? null : "0.03"))}
      </Group>
      <Group title="City">{(facets.city ?? []).slice(0, 10).map(([c]) => pill(criteria.city === c, c, () => set("city", criteria.city === c ? null : c)))}</Group>
      <Group title="Niche">{facets.category.filter(([c]) => c !== "other").map(([c]) => pill(criteria.category === c, <span className="capitalize">{c}</span>, () => set("category", criteria.category === c ? null : c)))}
        {facets.language.filter(([l]) => l !== "en").slice(0, 4).map(([l]) => pill(criteria.language === l, languageName(l), () => set("language", criteria.language === l ? null : l)))}</Group>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><div className="mb-2 text-[11px] font-semibold tracking-wide uppercase muted">{title}</div><div className="flex flex-wrap gap-1.5">{children}</div></div>;
}

function ShortlistButton({ shortlist }: { shortlist: ReturnType<typeof useShortlist> }) {
  const navigate = useNavigate();
  return (
    <Dialog.Root>
      <Dialog.Trigger className="panel inline-flex items-center gap-2 px-3.5 py-2 text-[13px] font-medium hover:border-amber-500/50 cursor-pointer">
        <Star size={14} className="text-amber-400" fill={shortlist.count ? "currentColor" : "none"} />Shortlist<span className="muted num">{shortlist.count}</span>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="drawer fixed top-0 right-0 z-50 flex h-full w-full max-w-md flex-col border-l hairline bg-[var(--bg)] outline-none">
          <div className="flex items-center justify-between border-b hairline p-5">
            <Dialog.Title className="text-[16px] font-semibold">Your shortlist</Dialog.Title>
            <Dialog.Close className="muted hover:text-[var(--text)] cursor-pointer"><X size={16} /></Dialog.Close>
          </div>
          <div className="flex-1 space-y-2 overflow-y-auto p-5">
            {Object.values(shortlist.items).length === 0 && <p className="text-[13px] muted">Star creators in the results to collect them here.</p>}
            {Object.values(shortlist.items).map((item) => (
              <div key={item.handle} className="panel flex items-center gap-3 p-3">
                <Avatar handle={item.handle} size={36} />
                <button onClick={() => navigate(`/creator/${item.handle}`)} className="min-w-0 flex-1 text-left cursor-pointer">
                  <div className="truncate text-[14px] font-medium">{item.name || item.handle}</div>
                  <div className="truncate text-[12px] muted">{compact(item.followers)} followers · {percent(item.engagement_rate, 1)}{item.city ? ` · ${item.city}` : ""}</div>
                </button>
                <button onClick={() => shortlist.toggle(item as Creator)} className="text-amber-400 cursor-pointer" aria-label="Remove"><Star size={15} fill="currentColor" /></button>
              </div>
            ))}
          </div>
          <div className="flex gap-2 border-t hairline p-4">
            <button onClick={shortlist.exportCsv} disabled={!shortlist.count} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--color-accent)] py-2 text-[13px] font-medium text-white disabled:opacity-40 cursor-pointer"><Download size={14} />Export CSV</button>
            <button onClick={shortlist.clear} disabled={!shortlist.count} className="rounded-lg border hairline px-3 py-2 text-[13px] muted disabled:opacity-40 cursor-pointer">Clear</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
