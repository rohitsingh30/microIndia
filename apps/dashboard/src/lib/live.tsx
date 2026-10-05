import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Activity, type Summary } from "./api";

type LiveState = { connected: boolean; activity: Activity[]; newest: Set<number> };

const LiveContext = createContext<LiveState>({ connected: false, activity: [], newest: new Set() });

/**
 * Live updates by short polling (activity every 3s, summary every 6s), paused in
 * background tabs. A permanently open stream per tab hit the browser's 6
 * connections-per-site limit once a few dashboard tabs were open, and every new
 * request then hung.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [connected, setConnected] = useState(true);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [newest, setNewest] = useState<Set<number>>(new Set());
  const cursor = useRef(0);
  const version = useRef("");

  useEffect(() => {
    let stopped = false;
    let tick = 0;
    const highlight = (ids: number[]) => {
      setNewest((current) => new Set([...current, ...ids]));
      window.setTimeout(() => setNewest((current) => {
        const next = new Set(current);
        ids.forEach((id) => next.delete(id));
        return next;
      }), 2000);
    };

    const poll = async () => {
      if (stopped || document.hidden) return;
      tick += 1;
      try {
        const fresh = await fetch(`/api/activity?limit=60&after=${cursor.current}`).then((r) => r.json()) as Activity[];
        if (fresh.length) {
          cursor.current = Math.max(cursor.current, ...fresh.map((a) => a.ts));
          setActivity((current) => {
            const seen = new Set(fresh.map((a) => `${a.id}-${a.ts}`));
            return [...fresh, ...current.filter((a) => !seen.has(`${a.id}-${a.ts}`))].slice(0, 200);
          });
          if (tick > 1) highlight(fresh.map((a) => a.id));
          if (fresh.some((a) => a.eligible)) client.invalidateQueries({ queryKey: ["creators"] });
        }
        if (tick % 2 === 1) {
          const summary: Summary = await api.summary();
          client.setQueryData(["summary"], summary);
          const next = JSON.stringify((summary as unknown as { version?: unknown }).version ?? "");
          if (next !== version.current) {
            if (version.current) {
              for (const key of ["timeseries", "pipeline", "sources"]) client.invalidateQueries({ queryKey: [key] });
            }
            version.current = next;
          }
        }
        setConnected(true);
      } catch {
        setConnected(false);
      }
    };

    poll();
    const timer = window.setInterval(poll, 3000);
    const onVisible = () => { if (!document.hidden) poll(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [client]);

  return <LiveContext.Provider value={{ connected, activity, newest }}>{children}</LiveContext.Provider>;
}

export const useLive = () => useContext(LiveContext);
