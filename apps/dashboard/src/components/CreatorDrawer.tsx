import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Bar, BarChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowUpRight, BadgeCheck, Briefcase, ChevronRight, ExternalLink, Heart, MapPin, MessageCircle, Play, RefreshCw, Users, X } from "lucide-react";
import { api } from "../lib/api";
import { ago, compact, languageName, percent, sourceLabel } from "../lib/format";
import { Avatar, Badge, Button, Skeleton, useToast } from "./ui";
import { shortReason } from "./ActivityFeed";

export function useCreatorDrawer() {
  const navigate = useNavigate();
  return (handle: string) => navigate(`/creator/${encodeURIComponent(handle)}`);
}

export function CreatorDrawer() {
  const [params, setParams] = useSearchParams();
  const handle = params.get("creator");
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("creator");
    setParams(next);
  };
  return (
    <Dialog.Root open={!!handle} onOpenChange={(open) => !open && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" />
        <Dialog.Content className="drawer fixed top-0 right-0 z-50 flex h-full w-full max-w-[640px] flex-col border-l hairline bg-[var(--bg)] shadow-2xl outline-none">
          <Dialog.Title className="sr-only">Creator {handle}</Dialog.Title>
          {handle && <CreatorBody handle={handle} onClose={close} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function CreatorBody({ handle, onClose }: { handle: string; onClose: () => void }) {
  const toast = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["creator", handle], queryFn: () => api.creator(handle) });
  const rescrape = useMutation({
    mutationFn: () => api.seed({ usernames: handle, expand: true }),
    onSuccess: () => toast("good", `@${handle} queued for a fresh capture and similar-account expansion`),
    onError: (e) => toast("bad", String(e)),
  });

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-16 w-full" /><Skeleton className="h-24 w-full" /><Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return <div className="p-6 text-[14px]">Couldn’t load @{handle}. <button className="underline" onClick={onClose}>Close</button></div>;
  }

  const posts = data.posts_sample;
  const engagement = posts.map((post, index) => ({
    index: index + 1, likes: post.likes ?? 0, comments: post.comments ?? 0,
  }));
  const history = data.follower_history.map((point) => ({ ...point, label: new Date(point.ts * 1000).toLocaleDateString() }));

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start gap-4 border-b hairline p-6">
        <Avatar handle={data.handle} size={56} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[20px] font-semibold">{data.name || `@${data.handle}`}</h2>
            {data.verified && <BadgeCheck size={18} className="text-sky-400" />}
          </div>
          <a href={data.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] text-[var(--color-accent-soft)] hover:underline">
            @{data.handle} <ArrowUpRight size={13} />
          </a>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {data.eligible ? <Badge tone="good">Eligible</Badge> : <Badge tone="neutral">Not eligible</Badge>}
            {data.brand_ready && <Badge tone="warn"><Briefcase size={11} />Doing brand deals</Badge>}
            {!data.brand_ready && data.open_to_collabs && <Badge>Open to collabs</Badge>}
            {data.category && <Badge tone="accent">{data.category}</Badge>}
            {data.languages.map((code) => <Badge key={code}>{languageName(code)}</Badge>)}
            {data.location && <Badge><MapPin size={11} />{data.location.slice(0, 40)}</Badge>}
          </div>
        </div>
        <Dialog.Close className="grid size-8 place-items-center rounded-lg hover:bg-[var(--panel-2)] cursor-pointer" aria-label="Close"><X size={16} /></Dialog.Close>
      </header>

      <div className="flex-1 space-y-6 overflow-y-auto p-6">
        {data.bio && <p className="text-[14px] leading-relaxed whitespace-pre-line">{data.bio}</p>}

        <div className="grid grid-cols-3 gap-3">
          <Metric icon={<Users size={14} />} label="Followers" value={compact(data.followers)} />
          <Metric icon={<Heart size={14} />} label="Engagement" value={percent(data.engagement_rate, 2)} />
          <Metric icon={<Heart size={14} />} label="Median likes" value={compact(data.median_likes)} />
          <Metric icon={<MessageCircle size={14} />} label="Median comments" value={compact(data.median_comments)} />
          <Metric icon={<Play size={14} />} label="Median views" value={compact(data.median_views)} />
          <Metric label="Posts" value={compact(data.posts)} />
        </div>

        {!data.eligible && data.reasons.length > 0 && (
          <Section title="Why not eligible">
            <ul className="space-y-1 text-[13px]">
              {data.reasons.map((reason) => <li key={reason} className="muted">• {shortReason(reason)}</li>)}
            </ul>
          </Section>
        )}

        {engagement.length > 1 && (
          <Section title="Engagement per recent post">
            <div className="h-40">
              <ResponsiveContainer>
                <BarChart data={engagement} margin={{ top: 4, right: 0, left: -22, bottom: 0 }}>
                  <XAxis dataKey="index" tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => compact(v)} />
                  <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12 }} cursor={{ fill: "var(--panel-2)" }} />
                  <Bar dataKey="likes" stackId="e" fill="#6d5efc" name="Likes" />
                  <Bar dataKey="comments" stackId="e" fill="#38bdf8" name="Comments" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Section>
        )}

        {history.length > 1 && (
          <Section title="Followers over time">
            <div className="h-32">
              <ResponsiveContainer>
                <LineChart data={history} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
                  <XAxis dataKey="label" tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => compact(v)} domain={["auto", "auto"]} />
                  <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12 }} />
                  <Line type="monotone" dataKey="followers" stroke="#22c55e" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Section>
        )}

        <Section title="Brand activity">
          <div className="grid grid-cols-3 gap-3">
            <Metric icon={<Briefcase size={14} />} label="Paid partnerships" value={String(data.paid_partnerships)} />
            <Metric label="Disclosed promos" value={String(data.disclosed_promos)} />
            <Metric label="Open to collabs" value={data.open_to_collabs ? "Yes" : "—"} />
          </div>
        </Section>

        <Section title="India evidence">
          {data.india_signals.length ? (
            <div className="flex flex-wrap gap-1.5">{data.india_signals.map((s) => <Badge key={s} tone="good">{s}</Badge>)}</div>
          ) : <p className="text-[13px] muted">None found yet. Not a rejection, just unknown.</p>}
        </Section>

        {data.top_hashtags.length > 0 && (
          <Section title="Top hashtags">
            <div className="flex flex-wrap gap-1.5">{data.top_hashtags.map((tag) => <Badge key={tag}>{tag.startsWith("#") ? tag : `#${tag}`}</Badge>)}</div>
          </Section>
        )}

        <Section title="How we found them">
          <ol className="flex flex-wrap items-center gap-1.5 text-[13px]">
            <li><Badge tone="good">@{data.handle}</Badge></li>
            {data.provenance.map((step, index) => {
              const label = step.via === "seed" ? "seed list / earlier dataset" : step.via === "search" ? `search “${step.from}”` : `${sourceLabel(`${step.via}:x`).label.toLowerCase()} @${step.from}`;
              return (
                <li key={index} className="flex items-center gap-1.5 muted"><ChevronRight size={13} />{label}</li>
              );
            })}
          </ol>
        </Section>

        {posts.length > 0 && (
          <Section title={`Recent posts (${posts.length})`}>
            <div className="space-y-2">
              {posts.map((post, index) => (
                <a key={post.permalink ?? index} href={post.permalink ?? undefined} target="_blank" rel="noreferrer" className="panel block p-3 hover:border-[var(--color-accent)]/40">
                  <div className="mb-1 flex items-center gap-3 text-[12px] muted num">
                    <Badge>{post.type ?? "post"}</Badge>
                    {post.paid_partnership && <Badge tone="warn"><Briefcase size={10} />paid partnership</Badge>}
                    <span className="inline-flex items-center gap-1"><Heart size={12} />{compact(post.likes)}</span>
                    <span className="inline-flex items-center gap-1"><MessageCircle size={12} />{compact(post.comments)}</span>
                    {post.views !== null && <span className="inline-flex items-center gap-1"><Play size={12} />{compact(post.views)}</span>}
                    <ExternalLink size={12} className="ml-auto" />
                  </div>
                  <p className="line-clamp-2 text-[13px]">{post.caption || <span className="muted">No caption captured</span>}</p>
                </a>
              ))}
            </div>
          </Section>
        )}

        <Section title="Data quality">
          <div className="text-[13px] muted">
            Captured {ago(data.captured_ts)} · {data.captures} capture{data.captures === 1 ? "" : "s"} · completeness {data.completeness ?? "—"}
            {data.missing_fields.length > 0 && <> · missing {data.missing_fields.join(", ")}</>}
          </div>
        </Section>
      </div>

      <footer className="flex items-center justify-end gap-2 border-t hairline p-4">
        <Button onClick={() => rescrape.mutate()} disabled={rescrape.isPending}><RefreshCw size={14} />Re-scrape &amp; expand</Button>
        <a href={data.url} target="_blank" rel="noreferrer"><Button variant="primary"><ArrowUpRight size={14} />Open on Instagram</Button></a>
      </footer>
    </div>
  );
}

function Metric({ label, value, icon }: { label: string; value: string; icon?: JSX.Element }) {
  return (
    <div className="panel p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium muted">{icon}{label}</div>
      <div className="mt-1 text-[18px] font-semibold num">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[12px] font-semibold tracking-wide uppercase muted">{title}</h3>
      {children}
    </section>
  );
}
