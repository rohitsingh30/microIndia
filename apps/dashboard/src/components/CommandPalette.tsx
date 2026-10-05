import { useEffect, useState } from "react";
import { Command } from "cmdk";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Activity, Moon, Plus, RotateCw, Search, Unlock, Users, Workflow } from "lucide-react";
import { api } from "../lib/api";
import { compact } from "../lib/format";
import { useCreatorDrawer } from "./CreatorDrawer";
import { Avatar, useTheme, useToast } from "./ui";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const navigate = useNavigate();
  const openCreator = useCreatorDrawer();
  const toast = useToast();
  const [, toggleTheme] = useTheme();
  const creators = useQuery({
    queryKey: ["creators", "palette", search],
    queryFn: () => api.creators(new URLSearchParams({ q: search, scope: "all", limit: "8", sort: "followers" })),
    enabled: open && search.length > 1,
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const run = (action: () => void) => () => { setOpen(false); setSearch(""); action(); };
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[70] grid place-items-start bg-black/50 pt-[14vh] backdrop-blur-[2px]" onClick={() => setOpen(false)}>
      <Command className="panel mx-auto w-full max-w-xl overflow-hidden shadow-2xl" onClick={(e) => e.stopPropagation()} shouldFilter={!creators.data}>
        <div className="flex items-center gap-2 border-b hairline px-4">
          <Search size={16} className="muted" />
          <Command.Input autoFocus value={search} onValueChange={setSearch} placeholder="Search creators or type a command…" className="w-full bg-transparent py-3.5 text-[14px] outline-none" />
          <kbd className="rounded border hairline px-1.5 text-[11px] muted">esc</kbd>
        </div>
        <Command.List className="max-h-[50vh] overflow-y-auto p-2">
          <Command.Empty className="px-3 py-6 text-center text-[13px] muted">No results</Command.Empty>
          {creators.data && creators.data.items.length > 0 && (
            <Command.Group heading="Creators" className="text-[11px] muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
              {creators.data.items.map((creator) => (
                <Item key={creator.handle} value={`creator ${creator.handle} ${creator.name ?? ""}`} onSelect={run(() => openCreator(creator.handle))}>
                  <Avatar handle={creator.handle} size={24} />
                  <span className="flex-1 truncate text-[var(--text)]">{creator.name || creator.handle} <span className="muted">@{creator.handle}</span></span>
                  <span className="muted num">{compact(creator.followers)}</span>
                </Item>
              ))}
            </Command.Group>
          )}
          <Command.Group heading="Go to" className="text-[11px] muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
            <Item value="live overview" onSelect={run(() => navigate("/"))}><Activity size={15} />Live overview</Item>
            <Item value="find creators assistant" onSelect={run(() => navigate("/find"))}><Users size={15} />Find creators</Item>
            <Item value="all creators explorer" onSelect={run(() => navigate("/creators"))}><Users size={15} />All creators</Item>
            <Item value="pipeline queue runners" onSelect={run(() => navigate("/pipeline"))}><Workflow size={15} />Pipeline</Item>
          </Command.Group>
          <Command.Group heading="Actions" className="text-[11px] muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
            <Item value="add seed creators" onSelect={run(() => navigate("/pipeline#seed"))}><Plus size={15} />Add seed creators</Item>
            <Item value="retry all failures" onSelect={run(() => api.retry({}).then((r) => toast("good", `${r.requeued} task(s) requeued`)))}><RotateCw size={15} />Retry all failures</Item>
            <Item value="signed in resume unblock" onSelect={run(() => api.unblock().then((r) => toast("good", `Resumed, ${r.requeued} requeued`)))}><Unlock size={15} />I’ve signed in — resume</Item>
            <Item value="toggle theme dark light" onSelect={run(toggleTheme)}><Moon size={15} />Toggle theme</Item>
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}

function Item({ children, value, onSelect }: { children: React.ReactNode; value: string; onSelect: () => void }) {
  return (
    <Command.Item value={value} onSelect={onSelect} className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-[var(--text)]">
      {children}
    </Command.Item>
  );
}
