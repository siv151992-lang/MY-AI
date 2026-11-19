"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/chat/Logo";
import { NAV } from "./nav";

/** Page frame shared by the platform pages: navigation rail (desktop) / scrollable tab bar (mobile). */
export function Shell({ title, icon: Icon, actions, children, wide }: {
  title: string; icon?: any; actions?: React.ReactNode; children: React.ReactNode; wide?: boolean;
}) {
  const path = usePathname() || "";
  return (
    <div className="flex h-dvh overflow-hidden">
      <aside className="hidden w-56 shrink-0 flex-col border-r bg-sidebar md:flex">
        <Link href="/home" className="flex h-12 items-center gap-2 px-4"><Logo className="h-6 w-6" /><span className="font-serif text-lg">Aria</span></Link>
        <nav className="thin-scroll flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href}
              className={cn("flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-sm hover:bg-accent",
                path.startsWith(n.href) && "bg-accent font-medium text-foreground")}>
              <n.icon className="h-4 w-4 text-muted-foreground" />{n.label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="thin-scroll flex gap-1 overflow-x-auto border-b px-2 py-1.5 md:hidden">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={cn("shrink-0 rounded-full px-3 py-1 text-xs", path.startsWith(n.href) ? "bg-accent font-medium" : "text-muted-foreground")}>{n.label}</Link>
          ))}
        </div>
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
          {Icon && <Icon className="h-5 w-5 text-brand" />}
          <h1 className="font-serif text-lg">{title}</h1>
          <div className="ml-auto flex items-center gap-2">{actions}</div>
        </header>
        <main className="thin-scroll flex-1 overflow-y-auto">
          <div className={cn("mx-auto w-full px-4 py-6", wide ? "max-w-6xl" : "max-w-4xl")}>{children}</div>
        </main>
      </div>
    </div>
  );
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("rounded-2xl border bg-card p-4", className)}>{children}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring";

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p> : null;
}

export function StatusPill({ status }: { status: string }) {
  const tone = /fail|error/.test(status) ? "bg-red-500/10 text-red-700 dark:text-red-400"
    : /succeed|complet|done/.test(status) ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
    : /cancel|skip/.test(status) ? "bg-muted text-muted-foreground" : "bg-amber-500/10 text-amber-700 dark:text-amber-400";
  return <span className={cn("rounded-full px-2 py-0.5 text-xs", tone)}>{status.replace(/_/g, " ")}</span>;
}

export function ToolPicker({ tools, value, onChange }: { tools: { name: string; description: string }[]; value: string[]; onChange: (v: string[]) => void }) {
  if (!tools.length) return <p className="text-xs text-muted-foreground">No tools are configured on this server.</p>;
  return (
    <div className="grid gap-1.5 sm:grid-cols-2">
      {tools.map((t) => (
        <label key={t.name} className="flex items-start gap-2 rounded-lg border px-2.5 py-1.5 text-sm">
          <input type="checkbox" className="mt-1" checked={value.includes(t.name)}
            onChange={(e) => onChange(e.target.checked ? [...value, t.name] : value.filter((x) => x !== t.name))} />
          <span><span className="font-mono text-xs">{t.name}</span><span className="block text-xs text-muted-foreground">{t.description}</span></span>
        </label>
      ))}
    </div>
  );
}

export function ModelSelect({ models, value, onChange, allowAuto = true }: {
  models: { id: string; label: string }[]; value: string | null; onChange: (v: string | null) => void; allowAuto?: boolean;
}) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} className={inputCls}>
      {allowAuto && <option value="">Auto (adaptive routing)</option>}
      {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
    </select>
  );
}
