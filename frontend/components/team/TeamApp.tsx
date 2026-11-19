"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle, ArrowLeft, BarChart3, Check, ChevronDown, ChevronRight, Code2, Compass, Download, FlaskConical, Globe,
  Loader2, Network, PenLine, Plus, Scale, Search, ShieldCheck, Trash2, Users, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ModelInfo } from "@/lib/types";
import { agentStates, waves, type AgentInfo, type AgentName, type TeamEvent, type TeamRun, type TeamTask } from "@/lib/team";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/chat/Markdown";

const ICON: Record<AgentName, any> = { planning: Compass, research: Search, coding: Code2, data_analyst: BarChart3,
  writing: PenLine, browser: Globe, verification: ShieldCheck };
const LABEL: Record<AgentName, string> = { planning: "Planning", research: "Research", coding: "Coding", data_analyst: "Data Analyst",
  writing: "Writing", browser: "Browser", verification: "Verification" };
const ORDER: AgentName[] = ["planning", "research", "coding", "data_analyst", "writing", "browser", "verification"];
const TERMINAL = ["completed", "completed_with_issues", "failed", "cancelled"];
type ListItem = { id: string; goal: string; status: string; created_at: string };

export function TeamApp({ initialId }: { initialId: string | null }) {
  const [runId, setRunId] = useState(initialId);
  const [list, setList] = useState<ListItem[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const loadList = useCallback(() => api<ListItem[]>("/api/team/runs").then(setList).catch(() => {}), []);
  useEffect(() => { loadList(); api<AgentInfo[]>("/api/team/agents").then(setAgents).catch(() => {}); }, [loadList]);
  const select = (id: string | null) => { window.history.pushState(null, "", id ? `/team/${id}` : "/team"); setRunId(id); };

  return (
    <div className="flex h-dvh overflow-hidden">
      <aside className="hidden w-72 shrink-0 flex-col border-r bg-muted/30 md:flex">
        <div className="flex h-12 items-center gap-2 px-3">
          <Button variant="ghost" size="icon-sm" asChild title="Back to chat"><Link href="/chat"><ArrowLeft /></Link></Button>
          <Users className="h-5 w-5 text-brand" /><span className="font-serif text-lg">Agent team</span>
        </div>
        <div className="px-2"><Button variant="ghost" className="w-full justify-start text-brand" onClick={() => select(null)}><Plus /> New task</Button></div>
        <nav className="thin-scroll mt-1 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {list.map((r) => (
            <button key={r.id} onClick={() => select(r.id)} className={cn("block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-accent", runId === r.id && "bg-accent")}>
              <span className="line-clamp-2">{r.goal}</span>
              <span className="text-xs text-muted-foreground">{r.status.replace(/_/g, " ")}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main className="thin-scroll min-w-0 flex-1 overflow-y-auto">
        {runId ? <RunView key={runId} runId={runId} agents={agents} onChanged={loadList} onDeleted={() => { loadList(); select(null); }} />
          : <NewRun agents={agents} onStarted={(id) => { loadList(); select(id); }} />}
      </main>
    </div>
  );
}

function NewRun({ agents, onStarted }: { agents: AgentInfo[]; onStarted: (id: string) => void }) {
  const [goal, setGoal] = useState("");
  useEffect(() => { const p = new URLSearchParams(window.location.search).get("prompt"); if (p) setGoal(p); }, []);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<{ default: string | null; models: ModelInfo[] }>("/api/models").then((m) => { setModels(m.models); setModelId(m.default); }).catch(() => {}); }, []);
  const start = async () => {
    setBusy(true); setError(null);
    try { onStarted((await api<{ id: string }>("/api/team/runs", { method: "POST", body: { goal: goal.trim(), model_id: modelId } })).id); }
    catch (e: any) { setError(e.message); setBusy(false); }
  };
  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-10">
      <div className="space-y-1">
        <h1 className="font-serif text-3xl">Give the team a goal</h1>
        <p className="text-sm text-muted-foreground">The Planning Agent splits it into tasks for specialist agents, independent tasks run in parallel, conflicting results are checked, and the Verification Agent reviews the final answer before you see it.</p>
      </div>
      <textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={5} maxLength={8000}
        placeholder="e.g. Research the three most popular Python web frameworks, compare their performance and learning curve, write a sample hello-world for each, and produce a recommendation brief."
        className="w-full resize-none rounded-xl border bg-card px-4 py-3 text-[15px] outline-none focus:ring-1 focus:ring-ring" />
      <div className="flex items-center gap-3">
        <select value={modelId ?? ""} onChange={(e) => setModelId(e.target.value)} className="h-9 rounded-lg border bg-background px-2 text-sm">
          {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
        <Button onClick={start} disabled={busy || goal.trim().length < 5}>{busy ? <Loader2 className="animate-spin" /> : <Network />} Start</Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        {agents.map((a) => {
          const Icon = ICON[a.name];
          return (
            <div key={a.name} className={cn("flex gap-2 rounded-xl border p-3 text-sm", !a.available && "opacity-50")}>
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
              <div><p className="font-medium">{a.label}</p><p className="text-xs text-muted-foreground">{a.description}</p>
                {!a.available && <p className="text-xs text-amber-700">Unavailable: its tools aren't configured on this server.</p>}
                {a.available && !!a.missing?.length && <p className="text-xs text-muted-foreground">Not configured: {a.missing.join(", ")}</p>}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RunView({ runId, agents, onChanged, onDeleted }: { runId: string; agents: AgentInfo[]; onChanged: () => void; onDeleted: () => void }) {
  const [run, setRun] = useState<TeamRun | null>(null);
  const [events, setEvents] = useState<TeamEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const load = useCallback(() => api<TeamRun>(`/api/team/runs/${runId}`).then((r) => { setRun(r); return r; }), [runId]);
  const soon = useCallback(() => { if (!timer.current) timer.current = setTimeout(() => { timer.current = null; load().catch(() => {}); }, 300); }, [load]);

  useEffect(() => {
    let closed = false, last = 0;
    let es: EventSource | null = null;
    load().catch((e) => setError(e.message));
    const connect = () => {
      es = new EventSource(`/api/team/runs/${runId}/events?after=${last}`);
      const on = (m: MessageEvent) => {
        const ev: TeamEvent = JSON.parse(m.data);
        if (ev.seq <= last) return;
        last = ev.seq;
        setEvents((x) => [...x, ev]);
        if (!["agent_tool_start", "agent_tool_end", "agent_note", "agent_status"].includes(ev.type)) soon();
        if (ev.type === "status") onChanged();
      };
      ["created", "status", "plan", "agent_status", "task_status", "agent_tool_start", "agent_tool_end", "agent_note", "conflicts",
        "conflict_resolved", "final_draft", "verification", "final"].forEach((t) => es!.addEventListener(t, on as EventListener));
      es.addEventListener("end", () => { es?.close(); soon(); });
      es.onerror = () => { es?.close(); if (!closed) setTimeout(() => { if (!closed) load().then((r) => { if (r.active) connect(); }).catch(() => {}); }, 2000); };
    };
    connect();
    return () => { closed = true; es?.close(); };
  }, [runId, load, soon, onChanged]);

  const states = useMemo(() => agentStates(events), [events]);
  const activity = useMemo(() => {
    const by: Record<string, { text: string; ok?: boolean; running: boolean }[]> = {};
    for (const e of events) {
      if (!e.data?.task) continue;
      const arr = (by[e.data.task] ??= []);
      if (e.type === "agent_tool_start") arr.push({ text: e.data.summary, running: true });
      if (e.type === "agent_tool_end") { const x = [...arr].reverse().find((a) => a.running); if (x) { x.running = false; x.ok = e.data.ok; } }
      if (e.type === "agent_note") arr.push({ text: `“${e.data.text.slice(0, 140)}”`, running: false, ok: true });
    }
    return by;
  }, [events]);

  if (!run) return <div className="p-10 text-center">{error ? <p className="text-sm text-destructive">{error}</p> : <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />}</div>;
  const running = !TERMINAL.includes(run.status);
  const statusText = [...events].reverse().find((e) => e.type === "status")?.data.text;
  const remove = async () => { if (confirm("Delete this run?")) { await api(`/api/team/runs/${runId}`, { method: "DELETE" }).catch(() => {}); onDeleted(); } };

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="font-serif text-2xl leading-tight">{run.goal}</h1>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground">
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" /> : run.status === "completed" ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />}
            {running ? statusText ?? run.status : run.status.replace(/_/g, " ")} · {run.tasks.length} task{run.tasks.length === 1 ? "" : "s"}
          </p>
        </div>
        {running && <Button variant="outline" size="sm" onClick={() => api(`/api/team/runs/${runId}/cancel`, { method: "POST" }).then(load)}><X /> Cancel</Button>}
        {run.final && <Button variant="outline" size="sm" asChild><a href={`/api/team/runs/${runId}/export`} download><Download /> Markdown</a></Button>}
        {!running && <Button variant="ghost" size="icon-sm" title="Delete" onClick={remove}><Trash2 /></Button>}
      </div>

      <section className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {ORDER.map((name) => {
          const st = states[name];
          const Icon = ICON[name];
          const working = st?.state === "working";
          const avail = agents.find((a) => a.name === name)?.available ?? true;
          return (
            <div key={name} className={cn("rounded-xl border p-2.5 text-xs transition-colors", working && "border-brand bg-brand/5 shadow-sm", !avail && "opacity-50")}>
              <div className="flex items-center gap-1.5 font-medium"><Icon className={cn("h-4 w-4", working ? "text-brand" : "text-muted-foreground")} />{LABEL[name]}</div>
              <p className={cn("mt-1 line-clamp-2 min-h-[2rem]", working ? "text-foreground" : "text-muted-foreground")}>
                {working ? <><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />{st.task && st.task !== "final" && !st.task.startsWith("C") ? `${st.task}: ` : ""}{st.title}</> : st?.done ? `idle · ${st.done} done` : "idle"}
              </p>
            </div>
          );
        })}
      </section>

      {run.status === "failed" && <div className="flex gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" /><span><b>The run failed.</b> {run.error}</span></div>}
      {run.plan_notes?.length > 0 && <p className="text-xs text-amber-700 dark:text-amber-400">Plan adjustments: {run.plan_notes.join(" ")}</p>}

      {run.tasks.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Tasks <span className="font-normal text-muted-foreground">— each column runs after the previous one; tasks in a column can run in parallel (file-creating tasks run alone)</span></h2>
          <div className="thin-scroll flex gap-3 overflow-x-auto pb-2">
            {waves(run.tasks).map((wave, i) => (
              <div key={i} className="w-72 shrink-0 space-y-2">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Step {i + 1}</p>
                {wave.map((id) => { const t = run.tasks.find((x) => x.id === id)!; return <TaskCard key={id} t={t} feed={activity[id] ?? []} />; })}
              </div>
            ))}
          </div>
        </section>
      )}

      {run.conflicts?.length > 0 && (
        <section className="space-y-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium"><Scale className="h-4 w-4" /> Conflicting results ({run.conflicts.length})</h2>
          {run.conflicts.map((c, i) => (
            <div key={i} className={cn("rounded-xl border p-3 text-sm", c.status === "resolved" ? "border-emerald-500/30" : "border-amber-500/40")}>
              <p className="font-medium">{c.topic} <span className={cn("ml-1 text-xs font-normal", c.status === "resolved" ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400")}>
                {c.status === "resolved" ? "resolved by the Verification Agent" : c.status === "open" ? "checking…" : "unresolved"}</span></p>
              <ul className="mt-1 text-xs">{c.positions.map((p, j) => <li key={j}>• <span className="font-mono">{p.task}</span> ({LABEL[run.tasks.find((t) => t.id === p.task)?.agent ?? "research"]}): {p.claim}</li>)}</ul>
              {c.resolution && <div className="answer mt-2 rounded-lg bg-muted/50 p-2 text-xs"><Markdown content={c.resolution} /></div>}
            </div>
          ))}
        </section>
      )}

      {run.verification && (
        <section className={cn("rounded-xl border p-3 text-sm", run.verification.ok ? "border-emerald-500/30 bg-emerald-500/5" : "border-amber-500/40 bg-amber-500/5")}>
          <p className="flex items-center gap-1.5 font-medium">{run.verification.ok ? <ShieldCheck className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
            {run.verification.ok ? "Verified by the Verification Agent" : "The Verification Agent found issues"}{run.verification.round > 1 ? ` (after ${run.verification.round - 1} revision)` : ""}</p>
          {run.verification.issues.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs">{run.verification.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          {run.verification.checked.length > 0 && <details className="mt-1 text-xs"><summary className="cursor-pointer text-muted-foreground">What was checked</summary><ul className="mt-1 list-disc pl-5">{run.verification.checked.map((x, i) => <li key={i}>{x}</li>)}</ul></details>}
        </section>
      )}

      {run.final && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Final answer</h2>
          <article className="answer rounded-xl border bg-card/50 p-4 text-[15px]"><Markdown content={run.final} /></article>
        </section>
      )}
    </div>
  );
}

function TaskCard({ t, feed }: { t: TeamTask; feed: { text: string; ok?: boolean; running: boolean }[] }) {
  const [open, setOpen] = useState(false);
  const Icon = ICON[t.agent];
  const color = t.status === "done" ? "border-emerald-500/30" : t.status === "failed" ? "border-destructive/40" : t.status === "running" ? "border-brand shadow-sm" : "";
  return (
    <div className={cn("rounded-xl border bg-card p-2.5 text-xs", color)}>
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-muted-foreground">{t.id}</span>
        <Icon className="h-3.5 w-3.5 text-brand" /><span className="font-medium">{LABEL[t.agent]}</span>
        <span className="ml-auto">{t.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" /> : t.status === "done" ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : t.status === "failed" ? <X className="h-3.5 w-3.5 text-destructive" /> : <span className="text-muted-foreground">waiting</span>}</span>
      </div>
      <p className="mt-1 text-sm">{t.title}</p>
      {t.depends_on.length > 0 && <p className="text-muted-foreground">after {t.depends_on.join(", ")}</p>}
      {feed.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {feed.slice(t.status === "running" ? -4 : -2).map((f, i) => (
            <li key={i} className="flex gap-1 truncate text-muted-foreground">
              {f.running ? <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin" /> : f.ok === false ? <X className="mt-0.5 h-3 w-3 shrink-0 text-destructive" /> : <FlaskConical className="mt-0.5 h-3 w-3 shrink-0" />}
              <span className="truncate">{f.text}</span>
            </li>
          ))}
        </ul>
      )}
      {t.status === "failed" && <p className="mt-1 text-destructive">{t.error}{t.attempts > 1 ? ` (after ${t.attempts} attempts)` : ""}</p>}
      {t.notes && <p className="mt-1 text-amber-700 dark:text-amber-400">{t.notes}</p>}
      {(t.result || t.sources.length > 0) && (
        <button onClick={() => setOpen((v) => !v)} className="mt-1.5 flex items-center gap-0.5 text-brand">{open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}Result{t.confidence ? ` · ${t.confidence} confidence` : ""}{t.tool_calls ? ` · ${t.tool_calls} tool call${t.tool_calls === 1 ? "" : "s"}` : ""}</button>
      )}
      {open && (
        <div className="mt-1 space-y-1">
          <div className="answer max-h-80 overflow-y-auto rounded bg-muted/40 p-2"><Markdown content={t.result} /></div>
          {t.sources.length > 0 && <ul>{t.sources.map((s) => <li key={s} className="truncate">{/^https?:/.test(s) ? <a href={s} target="_blank" rel="noreferrer" className="text-brand hover:underline">{s}</a> : s}</li>)}</ul>}
        </div>
      )}
    </div>
  );
}
