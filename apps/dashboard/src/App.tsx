import { useEffect } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Activity, Command as CommandIcon, Gauge, Target, Users, Workflow } from "lucide-react";
import { api } from "./lib/api";
import { compact } from "./lib/format";
import { useLive } from "./lib/live";
import { CommandPalette } from "./components/CommandPalette";
import { ThemeToggle, useTheme } from "./components/ui";
import { CreatorsPage } from "./pages/Creators";
import { CreatorPage } from "./pages/Creator";
import { FindPage } from "./pages/Find";
import { LivePage } from "./pages/Live";
import { PipelinePage } from "./pages/Pipeline";
import { SystemPage } from "./pages/System";

const NAV = [
  { to: "/", label: "Live", icon: Activity, key: "1" },
  { to: "/find", label: "Find", icon: Target, key: "2" },
  { to: "/creators", label: "All creators", icon: Users, key: "3" },
  { to: "/pipeline", label: "Pipeline", icon: Workflow, key: "4" },
  { to: "/system", label: "System", icon: Gauge, key: "5" },
];

function StatusPill() {
  const { connected } = useLive();
  const { data } = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const runnersAlive = data?.runners.filter((r) => r.alive).length ?? 0;
  let tone = "text-green-500";
  let text = `Collecting · ${runnersAlive} runner${runnersAlive === 1 ? "" : "s"}`;
  if (!connected) { tone = "text-[var(--muted)]"; text = "Reconnecting…"; }
  else if (data?.auth_blocked) { tone = "text-amber-500"; text = "Paused · sign-in needed"; }
  else if (data && data.chrome && !data.chrome.ok) { tone = "text-red-500"; text = "Chrome down · auto-restarting"; }
  else if (data && runnersAlive === 0) { tone = "text-red-500"; text = "No runners"; }
  return (
    <div className="panel flex items-center gap-2 px-3 py-1.5 text-[12px] font-medium">
      <span className={tone}><span className="live-dot block" /></span>
      <span>{text}</span>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [, toggleTheme] = useTheme();
  const { data } = useQuery({ queryKey: ["summary"], queryFn: api.summary });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName ?? "");
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
      const nav = NAV.find((item) => item.key === event.key);
      if (nav) navigate(nav.to);
      if (event.key.toLowerCase() === "t") toggleTheme();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, toggleTheme]);

  useEffect(() => {
    if (location.hash) document.querySelector(location.hash)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [location]);

  useEffect(() => {
    document.title = data ? `${compact(data.eligible_total)} creators · microIndia` : "microIndia · Live";
  }, [data?.eligible_total]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-30 border-b hairline bg-[var(--bg)]/80 backdrop-blur-xl">
        <div className="mx-auto flex h-[60px] max-w-[1480px] items-center gap-6 px-6">
          <div className="flex items-center gap-2.5">
            <div className="grid size-7 place-items-center rounded-lg bg-[var(--color-accent)]"><div className="size-2.5 rounded-full bg-white" /></div>
            <span className="text-[15px] font-semibold tracking-tight">microIndia</span>
          </div>
          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label, icon: Icon, key }) => (
              <NavLink key={to} to={to} end={to === "/"}
                className={({ isActive }) => clsx("inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors", isActive ? "bg-[var(--panel-2)] text-[var(--text)]" : "muted hover:text-[var(--text)]")}
                title={`${label} (${key})`}>
                <Icon size={15} />{label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <StatusPill />
            <button onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
              className="panel hidden items-center gap-2 px-3 py-1.5 text-[12px] muted hover:text-[var(--text)] md:inline-flex cursor-pointer">
              <CommandIcon size={13} />Search<kbd className="ml-2 rounded border hairline px-1 text-[10px]">⌘K</kbd>
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1480px] px-6 py-6">
        <Routes>
          <Route path="/" element={<LivePage />} />
          <Route path="/find" element={<FindPage />} />
          <Route path="/creators" element={<CreatorsPage />} />
          <Route path="/creator/:handle" element={<CreatorPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<LivePage />} />
        </Routes>
      </main>
      <CommandPalette />
    </div>
  );
}
