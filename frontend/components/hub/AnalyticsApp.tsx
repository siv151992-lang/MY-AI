"use client";

import { useEffect, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { duration } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { Card, ErrorNote, Field, inputCls, Shell } from "./Shell";

type Prefs = { routing: { priority: string; local_only: boolean; pinned: Record<string, string> }; categories: string[];
  models: { id: string; label: string; tier: string; provider: string }[]; health: Record<string, { calls: number; error_rate: number; p50_ms: number | null }> };

const fmtMs = (v: number | null | undefined) => (v == null ? "–" : v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`);

export function AnalyticsApp() {
  const [days, setDays] = useState(30);
  const [scope, setScope] = useState<"me" | "all">("me");
  const [d, setD] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setD(null);
    api<any>(`/api/analytics?days=${days}&scope=${scope}`).then(setD).catch((e) => setError(e.message));
  }, [days, scope]);
  const t = d?.totals;
  const maxDay = Math.max(1, ...(d?.by_day ?? []).map((x: any) => x.calls));
  return (
    <Shell title="Analytics" icon={BarChart3} wide actions={<>
      {d?.can_view_all && <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={scope} onChange={(e) => setScope(e.target.value as any)}><option value="me">My usage</option><option value="all">Everyone</option></select>}
      <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>
        {[1, 7, 30, 90].map((n) => <option key={n} value={n}>Last {n} day{n > 1 ? "s" : ""}</option>)}
      </select></>}>
      <ErrorNote error={error} />
      {!d ? <Loader2 className="animate-spin text-muted-foreground" /> : <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-6">
          {[["Model calls", t.calls], ["Input tokens", t.input_tokens.toLocaleString()], ["Output tokens", t.output_tokens.toLocaleString()],
            ["Error rate", `${(t.error_rate * 100).toFixed(1)}%`], ["Latency p50 / p95", `${fmtMs(t.p50_ms)} / ${fmtMs(t.p95_ms)}`],
            ["Cost", t.cost_usd != null ? `$${t.cost_usd.toFixed(2)}` : "not set"]].map(([k, v]) => (
            <Card key={k as string} className="p-3"><div className="text-xs text-muted-foreground">{k}</div><div className="text-lg font-medium">{v}</div></Card>))}
        </div>
        {t.cost_note && <p className="text-xs text-muted-foreground">{t.cost_note}</p>}
        <Card>
          <h2 className="mb-3 font-medium">Calls per day</h2>
          {d.by_day.length === 0 ? <p className="text-sm text-muted-foreground">No model calls in this period.</p> : (
            <div className="flex h-36 items-stretch gap-1">
              {d.by_day.map((x: any) => (
                <div key={x.day} className="group relative flex h-full max-w-16 flex-1 flex-col justify-end" title={`${x.day}: ${x.calls} calls, ${x.errors} errors`}>
                  <div className="rounded-t bg-brand/70" style={{ height: `${(x.calls - x.errors) / maxDay * 100}%` }} />
                  {x.errors > 0 && <div className="bg-red-500" style={{ height: `${x.errors / maxDay * 100}%` }} />}
                </div>))}
            </div>)}
        </Card>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <h2 className="mb-2 font-medium">Models</h2>
            <table className="w-full text-sm"><thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Model</th><th>Calls</th><th>Errors</th><th>p50</th><th>First token</th></tr></thead>
              <tbody>{d.by_model.map((m: any) => <tr key={m.model_id} className="border-t"><td className="py-1.5">{m.model_id}</td><td>{m.calls}</td><td className={m.error_rate > 0.1 ? "text-destructive" : ""}>{(m.error_rate * 100).toFixed(0)}%</td><td>{fmtMs(m.p50_ms)}</td><td>{fmtMs(m.ttft_p50_ms)}</td></tr>)}</tbody></table>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">Features</h2>
            <table className="w-full text-sm"><thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Feature</th><th>Calls</th><th>Tokens</th><th>Errors</th></tr></thead>
              <tbody>{d.by_feature.map((f: any) => <tr key={f.feature} className="border-t"><td className="py-1.5">{f.feature.replace("_", " ")}</td><td>{f.calls}</td><td>{f.tokens.toLocaleString()}</td><td>{f.errors}</td></tr>)}</tbody></table>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">Task outcomes</h2>
            <table className="w-full text-sm"><thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Kind</th><th>Runs</th><th>Outcomes</th><th>Avg time</th></tr></thead>
              <tbody>{d.tasks.map((k: any) => <tr key={k.kind} className="border-t"><td className="py-1.5">{k.kind}</td><td>{k.total}</td><td className="text-xs">{Object.entries(k.by_status).map(([s, n]) => `${s.replace(/_/g, " ")} ${n}`).join(" · ") || "–"}</td><td>{k.total ? duration(k.avg_seconds) : "–"}</td></tr>)}</tbody></table>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">Adaptive routing</h2>
            <p className="text-sm text-muted-foreground">{d.routing.decisions} auto-routed answers{d.routing.with_fallback ? `, ${d.routing.with_fallback} needed a fallback model` : ""}.</p>
            <ul className="mt-2 space-y-1 text-sm">{Object.entries(d.routing.by_category).map(([cat, ms]: any) => <li key={cat}><b>{cat}</b>: {Object.entries(ms).map(([m, n]) => `${m} ×${n}`).join(", ")}</li>)}</ul>
          </Card>
        </div>
        <RoutingPrefs />
      </div>}
    </Shell>
  );
}

function RoutingPrefs() {
  const [p, setP] = useState<Prefs | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Prefs>("/api/router/prefs").then(setP).catch(() => {}); }, []);
  if (!p) return null;
  const r = p.routing;
  const set = (patch: Partial<Prefs["routing"]>) => setP({ ...p, routing: { ...r, ...patch } });
  async function save() {
    setError(null);
    try { await api("/api/router/prefs", { method: "PUT", body: p!.routing }); setSaved(true); setTimeout(() => setSaved(false), 1500); }
    catch (e: any) { setError(e.message); }
  }
  return (
    <Card className="space-y-3">
      <h2 className="font-medium">Routing preferences</h2>
      <p className="text-sm text-muted-foreground">Used when the model is set to Auto. Aria classifies each request (task type and complexity), scores the connected models (tier, strengths, measured error rate and speed over the last 24 h) and falls back to the next best model if one fails before answering.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Optimise for"><select className={inputCls} value={r.priority} onChange={(e) => set({ priority: e.target.value })}>
          <option value="balanced">Balanced</option><option value="quality">Quality (bigger models)</option><option value="speed">Speed</option><option value="cost">Cost (smaller / local models)</option></select></Field>
        <label className="flex items-center gap-2 self-end text-sm"><input type="checkbox" checked={r.local_only} onChange={(e) => set({ local_only: e.target.checked })} /> Only local models (Ollama) — nothing leaves this computer</label>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {p.categories.map((c) => (
          <Field key={c} label={`Pin a model for ${c}`}><select className={inputCls} value={r.pinned[c] ?? ""} onChange={(e) => { const pinned = { ...r.pinned }; if (e.target.value) pinned[c] = e.target.value; else delete pinned[c]; set({ pinned }); }}>
            <option value="">Automatic</option>{p.models.map((m) => <option key={m.id} value={m.id}>{m.label}{p.health[m.id] ? ` · ${(p.health[m.id].error_rate * 100).toFixed(0)}% errors` : ""}</option>)}</select></Field>
        ))}
      </div>
      <ErrorNote error={error} />
      <Button onClick={save}>{saved ? "Saved" : "Save preferences"}</Button>
    </Card>
  );
}
