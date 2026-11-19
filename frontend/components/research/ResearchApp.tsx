"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle, ArrowLeft, BookOpenCheck, Check, CircleSlash, Download, ExternalLink, FileText, Globe, Loader2, Plus,
  Scale, Search, ShieldAlert, ShieldCheck, Trash2, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ModelInfo } from "@/lib/types";
import {
  linkCitations, PHASES, TERMINAL, type ResearchClaim, type ResearchDetail, type ResearchEvent, type ResearchRun, type ResearchSource,
} from "@/lib/research";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/chat/Markdown";

type Config = { available: boolean; reason: string; depths: Record<string, { sources: number; subquestions: number; verification: number }> };
type ListItem = Pick<ResearchRun, "id" | "question" | "depth" | "status" | "summary" | "error" | "created_at" | "finished_at">;

const DEPTH_TEXT: Record<string, string> = {
  quick: "A fast overview from a handful of sources.",
  standard: "Broad coverage, cross-checked, with verification of key single-source claims.",
  deep: "The most sources and sub-questions, and the most verification. Takes longest.",
};
const REL_STYLE: Record<string, string> = {
  high: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400", medium: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  low: "bg-red-500/15 text-red-700 dark:text-red-400", unknown: "bg-muted text-muted-foreground",
};
const CLAIM_STYLE: Record<string, { label: string; cls: string; icon: any }> = {
  corroborated: { label: "Corroborated", cls: "text-emerald-700 dark:text-emerald-400", icon: ShieldCheck },
  single_source: { label: "Single source", cls: "text-amber-700 dark:text-amber-400", icon: ShieldAlert },
  conflicting: { label: "Conflicting", cls: "text-red-700 dark:text-red-400", icon: Scale },
  unverified: { label: "Unverified", cls: "text-muted-foreground", icon: CircleSlash },
};

export function ResearchApp({ initialId }: { initialId: string | null }) {
  const [runId, setRunId] = useState<string | null>(initialId);
  const [list, setList] = useState<ListItem[]>([]);
  const [config, setConfig] = useState<Config | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);

  const loadList = useCallback(() => api<ListItem[]>("/api/research").then(setList).catch(() => {}), []);
  useEffect(() => {
    loadList();
    api<Config>("/api/research/config").then(setConfig).catch(() => {});
    api<{ default: string | null; models: ModelInfo[] }>("/api/models").then((m) => { setModels(m.models); setModelId(m.default); }).catch(() => {});
    const onPop = () => setRunId(location.pathname.match(/^\/research\/([\w-]+)/)?.[1] ?? null);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [loadList]);

  const select = (id: string | null) => {
    window.history.pushState(null, "", id ? `/research/${id}` : "/research");
    setRunId(id);
  };

  return (
    <div className="flex h-dvh overflow-hidden">
      <aside className="hidden w-72 shrink-0 flex-col border-r bg-muted/30 md:flex">
        <div className="flex h-12 items-center gap-2 px-3">
          <Button variant="ghost" size="icon-sm" asChild title="Back to chat"><Link href="/chat"><ArrowLeft /></Link></Button>
          <BookOpenCheck className="h-5 w-5 text-brand" /><span className="font-serif text-lg">Deep research</span>
        </div>
        <div className="px-2"><Button variant="ghost" className="w-full justify-start text-brand" onClick={() => select(null)}><Plus /> New research</Button></div>
        <nav className="thin-scroll mt-1 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {list.map((r) => (
            <button key={r.id} onClick={() => select(r.id)}
              className={cn("block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-accent", runId === r.id && "bg-accent")}>
              <span className="line-clamp-2">{r.question}</span>
              <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground"><StatusDot status={r.status} /> {r.status.replace("_", " ")} · {r.depth}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main className="thin-scroll min-w-0 flex-1 overflow-y-auto">
        {runId ? <RunView key={runId} runId={runId} onChanged={loadList} onDeleted={() => { loadList(); select(null); }} />
          : <NewResearch config={config} models={models} modelId={modelId} onModel={setModelId}
              onStarted={(id) => { loadList(); select(id); }} />}
      </main>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const cls = status === "completed" ? "bg-emerald-500" : status === "failed" ? "bg-red-500" : status === "cancelled" ? "bg-muted-foreground" : "animate-pulse bg-brand";
  return <span className={cn("inline-block h-2 w-2 rounded-full", cls)} />;
}

function NewResearch({ config, models, modelId, onModel, onStarted }: {
  config: Config | null; models: ModelInfo[]; modelId: string | null; onModel: (id: string) => void; onStarted: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  useEffect(() => { const p = new URLSearchParams(window.location.search).get("prompt"); if (p) setQ(p); }, []);
  const [depth, setDepth] = useState<"quick" | "standard" | "deep">("standard");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setBusy(true); setError(null);
    try { onStarted((await api<ResearchRun>("/api/research", { method: "POST", body: { question: q.trim(), depth, model_id: modelId } })).id); }
    catch (e: any) { setError(e.message); setBusy(false); }
  };
  return (
    <div className="mx-auto max-w-2xl space-y-5 px-4 py-12">
      <div className="space-y-1">
        <h1 className="font-serif text-3xl">What should Aria research?</h1>
        <p className="text-sm text-muted-foreground">Aria searches the web, reads the sources, cross-checks claims, verifies the important ones and writes a cited report. Every cited source is a page it actually read; every claim is backed by a quote found in that page.</p>
      </div>
      {config && !config.available && (
        <div className="flex gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />{config.reason}</div>
      )}
      <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={4} maxLength={2000}
        placeholder="e.g. How do heat pumps compare with gas boilers on running cost and emissions in the UK?"
        className="w-full resize-none rounded-xl border bg-card px-4 py-3 text-[15px] outline-none focus:ring-1 focus:ring-ring" />
      <div className="grid gap-2 sm:grid-cols-3">
        {(["quick", "standard", "deep"] as const).map((d) => (
          <button key={d} onClick={() => setDepth(d)} className={cn("rounded-xl border p-3 text-left text-sm", depth === d ? "border-brand bg-brand/5" : "hover:bg-accent")}>
            <span className="font-medium capitalize">{d}</span>
            {config?.depths[d] && <span className="block text-xs text-muted-foreground">up to {config.depths[d].sources} sources · {config.depths[d].subquestions} sub-questions</span>}
            <span className="mt-1 block text-xs text-muted-foreground">{DEPTH_TEXT[d]}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <select value={modelId ?? ""} onChange={(e) => onModel(e.target.value)} className="h-9 rounded-lg border border-input bg-background px-2 text-sm">
          {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
        <Button onClick={start} disabled={busy || q.trim().length < 5 || !config?.available}>{busy ? <Loader2 className="animate-spin" /> : <Search />} Start research</Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

function RunView({ runId, onChanged, onDeleted }: { runId: string; onChanged: () => void; onDeleted: () => void }) {
  const [run, setRun] = useState<ResearchDetail | null>(null);
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [tab, setTab] = useState<"progress" | "report" | "sources" | "claims">("progress");
  const [error, setError] = useState<string | null>(null);
  const refetch = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    const r = await api<ResearchDetail>(`/api/research/${runId}`);
    setRun(r);
    return r;
  }, [runId]);
  const scheduleLoad = useCallback(() => {
    if (refetch.current) return;
    refetch.current = setTimeout(() => { refetch.current = null; load().catch(() => {}); }, 400);
  }, [load]);

  useEffect(() => {
    let closed = false;
    let last = 0;
    let es: EventSource | null = null;
    load().then((r) => { if (r.status === "completed") setTab("report"); }).catch((e) => setError(e.message));
    const connect = () => {
      es = new EventSource(`/api/research/${runId}/events?after=${last}`);
      const on = (m: MessageEvent) => {
        const ev: ResearchEvent = JSON.parse(m.data);
        if (ev.seq <= last) return;
        last = ev.seq;
        setEvents((x) => [...x, ev]);
        if (ev.type !== "search") scheduleLoad();
        if (ev.type === "status") { onChanged(); if (ev.data.status === "completed") setTab("report"); }
      };
      ["created", "phase", "plan", "search", "source", "extract", "analysis", "verify", "status"].forEach((t) => es!.addEventListener(t, on as EventListener));
      es.addEventListener("end", () => { es?.close(); scheduleLoad(); });
      es.onerror = () => { es?.close(); if (!closed) setTimeout(() => { if (!closed) load().then((r) => { if (r.active) connect(); }).catch(() => {}); }, 2000); };
    };
    connect();
    return () => { closed = true; es?.close(); };
  }, [runId, load, scheduleLoad, onChanged]);

  if (!run) return <div className="flex h-full items-center justify-center">{error ? <p className="text-sm text-destructive">{error}</p> : <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}</div>;
  const read = run.sources.filter((s) => s.n);
  const failed = run.sources.filter((s) => !s.n);
  const running = !TERMINAL.includes(run.status);

  const cancel = () => api(`/api/research/${runId}/cancel`, { method: "POST" }).then(load).catch((e) => setError(e.message));
  const remove = async () => {
    if (!confirm("Delete this research run and its report?")) return;
    await api(`/api/research/${runId}`, { method: "DELETE" }).catch((e) => setError(e.message));
    onDeleted();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-4 py-6">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="font-serif text-2xl leading-tight">{run.question}</h1>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground"><StatusDot status={run.status} /> {run.status.replace("_", " ")} · {run.depth} · {read.length} sources read · {run.claims.length} verified claims</p>
        </div>
        <div className="flex gap-1">
          {running && <Button variant="outline" size="sm" onClick={cancel}><X /> Cancel</Button>}
          {run.status === "completed" && (
            <>
              <Button variant="outline" size="sm" asChild><a href={`/api/research/${runId}/export?format=md`} download><Download /> Markdown</a></Button>
              <Button variant="outline" size="sm" asChild><a href={`/api/research/${runId}/export?format=pdf`} download><FileText /> PDF</a></Button>
            </>
          )}
          {!running && <Button variant="ghost" size="icon-sm" title="Delete" onClick={remove}><Trash2 /></Button>}
        </div>
      </div>
      <PhaseBar status={run.status} />
      {run.status === "failed" && (
        <div className="flex gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <span><b>No report was produced.</b> {run.error}</span></div>
      )}
      {run.status === "cancelled" && <p className="text-sm text-muted-foreground">Cancelled. No report was produced.</p>}
      <div className="flex gap-1 border-b">
        {([["progress", "Progress"], ["report", "Report"], ["sources", `Sources (${read.length})`], ["claims", `Claims (${run.claims.length})`]] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === id ? "border-brand font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>{label}</button>
        ))}
      </div>
      {tab === "progress" && <Progress run={run} events={events} />}
      {tab === "report" && (run.report
        ? <article className="answer text-[15px]"><Markdown content={linkCitations(run.report, run.sources)} /></article>
        : <p className="py-8 text-center text-sm text-muted-foreground">{running ? "The report is written after the evidence has been collected and checked." : "There is no report for this run."}</p>)}
      {tab === "sources" && <Sources read={read} failed={failed} claims={run.claims} />}
      {tab === "claims" && <Claims run={run} />}
    </div>
  );
}

function PhaseBar({ status }: { status: string }) {
  const idx = PHASES.findIndex((p) => p.id === status);
  const done = status === "completed";
  return (
    <ol className="flex flex-wrap gap-1 text-xs">
      {PHASES.map((p, i) => {
        const state = done || (idx >= 0 && i < idx) ? "done" : i === idx ? "active" : "todo";
        return (
          <li key={p.id} className={cn("flex items-center gap-1 rounded-full border px-2.5 py-1",
            state === "done" && "border-emerald-500/40 text-emerald-700 dark:text-emerald-400", state === "active" && "border-brand bg-brand/10 font-medium", state === "todo" && "text-muted-foreground")}>
            {state === "done" ? <Check className="h-3 w-3" /> : state === "active" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}{p.label}
          </li>
        );
      })}
    </ol>
  );
}

function Progress({ run, events }: { run: ResearchDetail; events: ResearchEvent[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-[1fr_1.3fr]">
      <section className="space-y-2">
        <h2 className="text-sm font-medium">Research plan</h2>
        {run.plan.length ? run.plan.map((p, i) => (
          <div key={i} className="rounded-lg border p-2.5 text-sm">
            <p className="font-medium">{i + 1}. {p.question}</p>
            {p.queries?.map((q) => <p key={q} className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground"><Search className="h-3 w-3" />{q}</p>)}
          </div>
        )) : <p className="text-sm text-muted-foreground">Planning…</p>}
        {!!Object.keys(run.stats).length && (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg border p-2.5 text-xs">
            {([["Searches", run.stats.queries], ["Search errors", run.stats.search_errors], ["Pages found", run.stats.candidates], ["Pages read", run.stats.read],
              ["Unreadable", run.stats.failed], ["Claims kept", run.stats.claims], ["Quotes rejected", run.stats.quotes_rejected], ["Verification searches", run.stats.verification_searches]] as const)
              .map(([k, v]) => <div key={k} className="flex justify-between"><dt className="text-muted-foreground">{k}</dt><dd className="font-medium">{v ?? 0}</dd></div>)}
          </dl>
        )}
      </section>
      <section>
        <h2 className="mb-2 text-sm font-medium">Activity</h2>
        <ol className="thin-scroll max-h-[60vh] space-y-1 overflow-y-auto text-xs">
          {events.map((e) => <EventRow key={e.seq} e={e} />)}
        </ol>
      </section>
    </div>
  );
}

function EventRow({ e }: { e: ResearchEvent }) {
  const d = e.data;
  let icon = <Check className="h-3.5 w-3.5 text-muted-foreground" />, text: React.ReactNode = e.type;
  switch (e.type) {
    case "created": text = "Research started"; break;
    case "phase": icon = <Loader2 className="h-3.5 w-3.5 text-brand" />; text = <b>{d.text}</b>; break;
    case "plan": text = `Planned ${d.subquestions?.length ?? 0} sub-questions`; break;
    case "search": icon = <Search className="h-3.5 w-3.5" />; text = d.ok ? <>Searched “{d.query}” — {d.results} results{d.top?.length ? <span className="text-muted-foreground"> ({d.top.map((t: any) => t.domain).join(", ")})</span> : null}</>
      : <span className="text-destructive">Search “{d.query}” failed: {d.error}</span>; break;
    case "source": icon = <Globe className="h-3.5 w-3.5" />; text = d.status === "read" ? <>Read <b>S{d.n}</b> {d.title || d.domain} <span className="text-muted-foreground">({d.domain}, {d.reliability})</span></>
      : <span className="text-muted-foreground">Could not read {d.domain}: {d.error}</span>; break;
    case "extract": text = d.ok ? <>S{d.n}: {d.claims} claims with verified quotes{d.rejected ? <span className="text-amber-600"> · {d.rejected} rejected (quote not in page)</span> : null}</> : <span className="text-destructive">S{d.n}: extraction failed</span>; break;
    case "analysis": icon = <Scale className="h-3.5 w-3.5" />; text = d.ok ? `Sub-question ${d.subquestion + 1}: ${d.groups} agreeing group(s), ${d.conflicts} conflict(s)` : "Comparison failed"; break;
    case "verify": icon = <ShieldCheck className="h-3.5 w-3.5" />; text = d.ok ? `Verified C${d.claim}: ${String(d.result).replace("_", " ")}` : `Verification of C${d.claim} failed: ${d.error}`; break;
    case "status": icon = d.status === "completed" ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <AlertTriangle className="h-3.5 w-3.5 text-destructive" />;
      text = d.status === "completed" ? <b>Report ready</b> : <b>{d.status}: {d.error}</b>; break;
  }
  return <li className="flex items-start gap-2"><span className="mt-0.5 shrink-0">{icon}</span><span className="min-w-0">{text}</span></li>;
}

function Sources({ read, failed, claims }: { read: ResearchSource[]; failed: ResearchSource[]; claims: ResearchClaim[] }) {
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">#</th><th className="p-2">Source</th><th className="p-2">Reliability</th><th className="p-2">Claims</th><th className="p-2">Status</th></tr></thead>
          <tbody>
            {read.map((s) => {
              const mine = claims.filter((c) => c.source === s.n);
              return (
                <tr key={s.url} className="border-t align-top">
                  <td className="p-2 font-mono text-xs">S{s.n}</td>
                  <td className="p-2"><a href={s.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">{s.title || s.domain} <ExternalLink className="inline h-3 w-3" /></a>
                    <span className="block text-xs text-muted-foreground">{s.domain}{s.purpose === "verification" ? " · found while verifying a claim" : s.query ? ` · via “${s.query}”` : ""}</span></td>
                  <td className="p-2"><span title={s.reliability_reason} className={cn("rounded px-1.5 py-0.5 text-xs", REL_STYLE[s.reliability_label])}>{s.reliability_label}</span></td>
                  <td className="p-2 text-xs">{mine.length} <span className="text-muted-foreground">({mine.filter((c) => c.status === "corroborated").length} corroborated, {mine.filter((c) => c.status === "conflicting").length} conflicting)</span></td>
                  <td className="p-2 text-xs">{s.status === "irrelevant" ? <span className="text-muted-foreground">read, no usable evidence</span> : "read"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {failed.length > 0 && (
        <div className="text-sm">
          <h3 className="mb-1 font-medium">Found but not used ({failed.length})</h3>
          <ul className="space-y-0.5 text-xs text-muted-foreground">{failed.map((s) => <li key={s.url}><a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">{s.domain}</a> — {s.error || s.status}</li>)}</ul>
        </div>
      )}
      <p className="text-xs text-muted-foreground">Reliability is a transparent heuristic based on the site (hover a badge for the reason), not a fact-check.</p>
    </div>
  );
}

function Claims({ run }: { run: ResearchDetail }) {
  const urls = useMemo(() => new Map(run.sources.filter((s) => s.n).map((s) => [s.n, s] as const)), [run.sources]);
  const conflicts = run.analysis.conflicts ?? [];
  const cite = (ns: number[]) => ns.map((n) => <a key={n} href={urls.get(n)?.url} target="_blank" rel="noreferrer" title={urls.get(n)?.title} className="mr-1 font-mono text-brand hover:underline">S{n}</a>);
  return (
    <div className="space-y-6">
      {conflicts.length > 0 && (
        <section className="space-y-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium"><Scale className="h-4 w-4 text-red-600" /> Conflicting information ({conflicts.length})</h2>
          {conflicts.map((c, i) => (
            <div key={i} className="rounded-xl border border-red-500/30 p-3 text-sm">
              <p className="font-medium">{c.topic}</p>
              <ul className="mt-1 space-y-1">{c.positions.map((p, j) => (
                <li key={j}>• {p.summary} <span className="text-xs">{cite([...new Set(p.claims.map((cn) => run.claims.find((x) => x.n === cn)?.source).filter(Boolean) as number[])])}</span></li>
              ))}</ul>
              {c.assessment && <p className="mt-1.5 text-xs text-muted-foreground">Assessment: {c.assessment}</p>}
            </div>
          ))}
        </section>
      )}
      {run.plan.map((p, i) => {
        const items = run.claims.filter((c) => c.subq === i);
        return (
          <section key={i} className="space-y-2">
            <h2 className="text-sm font-medium">{i + 1}. {p.question}</h2>
            {!items.length && <p className="text-xs text-muted-foreground">No verifiable evidence found for this sub-question.</p>}
            {items.map((c) => {
              const st = CLAIM_STYLE[c.status];
              const Icon = st.icon;
              return (
                <div key={c.n} className="rounded-lg border p-2.5 text-sm">
                  <p><span className="mr-1 font-mono text-xs text-muted-foreground">C{c.n}</span>{c.text}</p>
                  <blockquote className="mt-1 border-l-2 pl-2 text-xs italic text-muted-foreground">“{c.quote}” — {cite([c.source])}</blockquote>
                  <p className={cn("mt-1 flex flex-wrap items-center gap-1 text-xs", st.cls)}><Icon className="h-3.5 w-3.5" /> {st.label}
                    <span className="text-muted-foreground">· supported by {cite(c.supporting)}{c.conflicting.length ? <>· contradicted by {cite(c.conflicting)}</> : null}{c.importance === "high" ? " · key claim" : ""}</span></p>
                  {c.note && <p className="mt-0.5 text-xs text-muted-foreground">{c.note}</p>}
                </div>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}
