import { useCallback, useEffect, useState } from "react";
import type { Creator } from "./api";

const KEY = "microindia-shortlist";
type Entry = Pick<Creator, "handle" | "name" | "followers" | "engagement_rate" | "city" | "category" | "url" | "brand_posts">;

function read(): Record<string, Entry> {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
}

/** A brand's shortlist, kept in this browser. */
export function useShortlist() {
  const [items, setItems] = useState<Record<string, Entry>>(read);
  useEffect(() => {
    const sync = () => setItems(read());
    window.addEventListener("storage", sync);
    window.addEventListener("shortlist", sync);
    return () => { window.removeEventListener("storage", sync); window.removeEventListener("shortlist", sync); };
  }, []);
  const save = (next: Record<string, Entry>) => {
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* private mode */ }
    setItems(next);
    window.dispatchEvent(new Event("shortlist"));
  };
  const toggle = useCallback((creator: Creator) => {
    const next = { ...read() };
    if (next[creator.handle]) delete next[creator.handle];
    else next[creator.handle] = {
      handle: creator.handle, name: creator.name, followers: creator.followers, engagement_rate: creator.engagement_rate,
      city: creator.city, category: creator.category, url: creator.url, brand_posts: creator.brand_posts,
    };
    save(next);
  }, []);
  const exportCsv = () => {
    const rows = Object.values(read());
    const header = ["handle", "name", "followers", "engagement_rate", "city", "category", "brand_posts", "url"];
    const csv = [header.join(","), ...rows.map((r) => header.map((h) => JSON.stringify((r as Record<string, unknown>)[h] ?? "")).join(","))].join("\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    link.download = "creator-shortlist.csv";
    link.click();
  };
  return { items, has: (handle: string) => !!items[handle], toggle, count: Object.keys(items).length, exportCsv, clear: () => save({}) };
}
