import { createContext, useCallback, useContext, useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import clsx from "clsx";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { CheckCircle2, Info, Moon, Sun, XCircle } from "lucide-react";
import { hashHue } from "../lib/format";

export function Card({ title, action, children, className, padded = true }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={clsx("panel overflow-hidden", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 px-5 pt-4 pb-3">
          <h2 className="text-[13px] font-semibold tracking-wide uppercase muted">{title}</h2>
          {action}
        </header>
      )}
      <div className={clsx(padded && "px-5 pb-5", !title && padded && "pt-5")}>{children}</div>
    </section>
  );
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "accent";
const TONES: Record<Tone, string> = {
  neutral: "bg-[var(--panel-2)] text-[var(--muted)] border-[var(--line)]",
  good: "bg-green-500/10 text-green-500 border-green-500/20",
  warn: "bg-amber-500/10 text-amber-500 border-amber-500/20",
  bad: "bg-red-500/10 text-red-500 border-red-500/20",
  info: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  accent: "bg-[var(--color-accent)]/12 text-[var(--color-accent-soft)] border-[var(--color-accent)]/25",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Button({ variant = "ghost", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  return (
    <button
      {...props}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer",
        variant === "primary" && "bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-soft)]",
        variant === "ghost" && "border hairline hover:bg-[var(--panel-2)]",
        variant === "danger" && "bg-red-500/10 text-red-500 border border-red-500/30 hover:bg-red-500/20",
        className,
      )}
    />
  );
}

export function Avatar({ handle, size = 36 }: { handle: string; size?: number }) {
  const hue = hashHue(handle);
  const letters = handle.replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "?";
  return (
    <div
      className="grid shrink-0 place-items-center rounded-full font-semibold text-white"
      style={{
        width: size, height: size, fontSize: size * 0.36,
        background: `linear-gradient(135deg, hsl(${hue} 70% 55%), hsl(${(hue + 40) % 360} 70% 42%))`,
      }}
    >
      {letters}
    </div>
  );
}

export function Stat({ label, value, hint, series, tone = "accent", icon }: {
  label: string; value: ReactNode; hint?: ReactNode; series?: number[]; tone?: "accent" | "good" | "info" | "warn"; icon?: ReactNode;
}) {
  const color = { accent: "#6d5efc", good: "#22c55e", info: "#38bdf8", warn: "#f59e0b" }[tone];
  return (
    <div className="panel relative overflow-hidden p-4">
      <div className="flex items-center gap-2 text-[12px] font-medium muted">{icon}{label}</div>
      <div className="mt-1.5 text-[30px] leading-none font-semibold tracking-tight num">{value}</div>
      {hint && <div className="mt-2 text-[12px] muted">{hint}</div>}
      {series && series.length > 1 && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 opacity-80">
          <ResponsiveContainer>
            <AreaChart data={series.map((v, i) => ({ i, v }))} margin={{ top: 0, bottom: 0, left: 0, right: 0 }}>
              <defs>
                <linearGradient id={`spark-${label}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={`url(#spark-${label})`} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export function Meter({ value, max, tone = "accent" }: { value: number; max: number; tone?: Tone }) {
  const width = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const color = { accent: "bg-[var(--color-accent)]", good: "bg-green-500", warn: "bg-amber-500", bad: "bg-red-500", info: "bg-sky-400", neutral: "bg-[var(--muted)]" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--panel-2)]">
      <div className={clsx("h-full rounded-full transition-all duration-700", color)} style={{ width: `${width}%` }} />
    </div>
  );
}

export function Empty({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: ReactNode }) {
  return (
    <div className="grid place-items-center gap-2 py-10 text-center">
      <div className="muted">{icon}</div>
      <div className="text-[14px] font-medium">{title}</div>
      {hint && <div className="max-w-sm text-[13px] muted">{hint}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-md bg-[var(--panel-2)]", className)} />;
}

/* --- toasts ------------------------------------------------------------- */
type Toast = { id: number; tone: "good" | "bad" | "info"; text: string };
const ToastContext = createContext<(tone: Toast["tone"], text: string) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, text }]);
    window.setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 4200);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="fixed right-5 bottom-5 z-[60] flex flex-col gap-2">
        {toasts.map((toast) => (
          <div key={toast.id} className="panel enter flex items-center gap-2 px-4 py-3 text-[13px] shadow-2xl">
            {toast.tone === "good" ? <CheckCircle2 size={16} className="text-green-500" /> : toast.tone === "bad" ? <XCircle size={16} className="text-red-500" /> : <Info size={16} className="text-sky-400" />}
            {toast.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);

/* --- theme --------------------------------------------------------------- */
export function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch { /* private mode */ }
  }, [dark]);
  return [dark, () => setDark((value) => !value)];
}

export function ThemeToggle() {
  const [dark, toggle] = useTheme();
  return (
    <button onClick={toggle} className="grid size-8 place-items-center rounded-lg border hairline hover:bg-[var(--panel-2)] cursor-pointer" title="Toggle theme (T)">
      {dark ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}
