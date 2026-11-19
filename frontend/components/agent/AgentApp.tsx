"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bot, FileText, ListTree, Loader2, MessageSquare, Play, Plus, ShieldAlert, Square } from "lucide-react";
import { api } from "@/lib/api";
import type { AgentEvent, AgentRun, AgentRunDetail, ModelInfo, ToolInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/chat/Markdown";
import { ActivityPanel, StatusBadge } from "./ActivityPanel";

const LIVE = new Set(["planning", "running", "awaiting_approval", "verifying"]);

function describe(e: AgentEvent): string | null {
  const d = e.data;
  switch (e.type) {
    case "created": return "Run created";
    case "plan": return d.replanned ? `Re-planned: ${d.steps.length} steps${d.superseded?.length ? ` (replaced ${d.superseded.length})` : ""}`
      : d.remediation ? "Added remediation steps" : `Plan ready: ${d.steps.length} steps`;
    case "status": return `Status → ${d.status.replaceAll("_", " ")}`;
    case "step_started": return `Started step ${d.step.idx + 1}: ${d.step.title}`;
    case "step_done": return `Completed step ${d.step.idx + 1}`;
    case "step_failed": return `Step ${d.step.idx + 1} failed`;
    case "step_skipped": return `Skipped step ${d.step.idx + 1}`;
    case "step_error": return `Step ${d.step + 1} attempt ${d.attempt} failed${d.will_retry ? ", retrying" : ""}`;
    case "tool_start": return `→ ${d.summary}`;
    case "tool_end": return `${d.ok ? "✓" : "✗"} ${d.tool}: ${String(d.output).split("\n")[0].slice(0, 140)}`;
    case "approval_required": return `Permission requested: ${d.summary.split("\n")[0]}`;
    case "approval_decided": return `Permission ${d.status}`;
    case "replanning": return "Recovering: re-planning remaining steps";
    case "verification": return `Verification: ${d.achieved}`;
    case "warning": return `Guard: ${d.message}`;
    case "note": return d.text.slice(0, 160);
    case "report": return "Final report written";
    default: return null;
  }
}

export function AgentApp({ initialRunId }: { initialRunId: string | null }) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [runId, setRunId] = useState<string | null>(initialRunId);
  const [run, setRun] = useState<AgentRunDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [tab, setTab] = useState<"activity" | "report">("activity");
  const [goal, setGoal] = useState("");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    Promise.all([api<AgentRun[]>("/api/agent/runs"), api<{ default: string | null; models: ModelInfo[] }>("/api/models"),
      api<ToolInfo[]>("/api/agent/tools")])
      .then(([r, m, t]) => { setRuns(r); setModels(m.models); setModelId(m.default); setTools(t); })
      .catch((e) => setError(e.message));
  }, []);

  const loadRun = useCallback(async (id: string) => {
    const r = await api<AgentRunDetail>(`/api/agent/runs/${id}`);
    setRun(r);
    setRuns((rs) => rs.map((x) => (x.id === r.id ? { ...x, status: r.status } : x)));
    return r;
  }, []);

  const scheduleRefetch = useCallback((id: string) => {
    if (refetchTimer.current) clearTimeout(refetchTimer.current);
    refetchTimer.current = setTimeout(() => loadRun(id).catch(() => {}), 250);
  }, [loadRun]);

  // subscribe to the live event stream (replays history first)
  useEffect(() => {
    esRef.current?.close();
    setEvents([]);
    setRun(null);
    if (!runId) return;
    let closed = false;
    let lastSeq = 0;
    loadRun(runId).then((r) => { if (r.status === "completed" || r.report) setTab(r.active ? "activity" : "report"); })
      .catch((e) => setError(e.message));

    const connect = () => {
      const es = new EventSource(`/api/agent/runs/${runId}/events?after=${lastSeq}`);
      esRef.current = es;
      const onEvent = (msg: MessageEvent) => {
        const ev: AgentEvent = JSON.parse(msg.data);
        if (ev.seq <= lastSeq) return;
        lastSeq = ev.seq;
        setEvents((evs) => [...evs, ev]);
        if (ev.type !== "note" && ev.type !== "tool_start") scheduleRefetch(runId);
        if (ev.type === "report") setTab("report");
      };
      ["created", "plan", "status", "step_started", "step_done", "step_failed", "step_skipped", "step_error", "tool_start",
        "tool_end", "approval_required", "approval_decided", "replanning", "verification", "warning", "note", "report"]
        .forEach((t) => es.addEventListener(t, onEvent as EventListener));
      es.addEventListener("end", () => { es.close(); scheduleRefetch(runId); });
      es.onerror = () => {
        es.close();
        if (!closed) setTimeout(() => { if (!closed) loadRun(runId).then((r) => { if (r.active) connect(); }).catch(() => {}); }, 2000);
      };
    };
    connect();
    return () => { closed = true; esRef.current?.close(); };
  }, [runId, loadRun, scheduleRefetch]);

  const select = (id: string | null) => {
    window.history.pushState(null, "", id ? `/agent/${id}` : "/agent");
    setRunId(id);
    setTab("activity");
  };

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const r = await api<AgentRun>("/api/agent/runs", { method: "POST", body: { goal, model_id: modelId } });
      setRuns((rs) => [r, ...rs]);
      setGoal("");
      select(r.id);
    } catch (e: any) { setError(e.message); } finally { setStarting(false); }
  }

  async function decide(id: string, approved: boolean) {
    if (!runId) return;
    try {
      await api(`/api/agent/runs/${runId}/approvals/${id}`, { method: "POST", body: { approved } });
      scheduleRefetch(runId);
    } catch (e: any) { setError(e.message); }
  }

  async function cancel() {
    if (!runId) return;
    await api(`/api/agent/runs/${runId}/cancel`, { method: "POST" }).catch((e) => setError(e.message));
  }

  const timeline = events.map((e) => ({ e, text: describe(e) })).filter((x) => x.text);

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* runs list */}
      <aside className="hidden w-72 shrink-0 flex-col border-r bg-sidebar md:flex">
        <div className="flex items-center gap-2 px-3 pb-2 pt-3">
          <Bot className="h-5 w-5 text-brand" /><span className="font-serif text-lg">Agent</span>
          <Link href="/chat" className="ml-auto"><Button variant="ghost" size="sm"><MessageSquare /> Chat</Button></Link>
        </div>
        <div className="px-2"><Button variant="ghost" className="w-full justify-start text-brand hover:text-brand" onClick={() => select(null)}><Plus /> New goal</Button></div>
        <nav className="thin-scroll mt-2 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {runs.map((r) => (
            <button key={r.id} onClick={() => select(r.id)}
              className={cn("w-full rounded-lg px-3 py-2 text-left text-sm transition-colors", r.id === runId ? "bg-accent" : "hover:bg-accent/60")}>
              <span className="line-clamp-2">{r.goal}</span>
              <span className="mt-1 block"><StatusBadge status={r.status} /></span>
            </button>
          ))}
          {runs.length === 0 && <p className="px-3 py-4 text-sm text-muted-foreground">No runs yet.</p>}
        </nav>
      </aside>

      <main className="thin-scroll flex-1 overflow-y-auto">
        <div className="flex items-center gap-2 border-b px-3 py-2 md:hidden">
          <Link href="/chat"><Button variant="ghost" size="icon"><ArrowLeft /></Button></Link>
          {runId && <Button variant="ghost" size="sm" onClick={() => select(null)}><Plus /> New goal</Button>}
        </div>
        {error && (
          <div className="mx-auto mt-3 flex max-w-4xl justify-between rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive">
            {error}<button className="text-xs underline" onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}

        {!runId ? (
          <div className="mx-auto max-w-2xl px-4 py-10 animate-fade-up">
            <h1 className="font-serif text-3xl">What should I get done?</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Describe a goal. I’ll plan it, carry it out with real tools, recover from errors, ask before anything that
              affects the outside world, verify the result, and give you a report.
            </p>
            <textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={5}
              placeholder="e.g. Research the three most-used open-source vector databases, compare them on licensing, performance and hosting options, save the comparison as a markdown file, and email me the summary."
              className="mt-5 w-full resize-y rounded-2xl border bg-card p-4 text-[15px] leading-6 shadow-sm outline-none focus:ring-2 focus:ring-ring" />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select value={modelId ?? ""} onChange={(e) => setModelId(e.target.value)}
                className="h-9 rounded-lg border bg-background px-2 text-sm">
                {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              <Button className="ml-auto" disabled={goal.trim().length < 5 || starting || !models.length} onClick={start}>
                {starting ? <Loader2 className="animate-spin" /> : <Play />} Start agent
              </Button>
            </div>
            <div className="mt-8">
              <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tools available on this server</h2>
              <div className="grid gap-2 sm:grid-cols-2">
                {tools.map((t) => (
                  <div key={t.name} className="rounded-xl border bg-card/60 px-3 py-2 text-sm">
                    <div className="flex items-center gap-2 font-mono text-xs">
                      {t.name}
                      {t.needs_approval && <span className="flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 font-sans text-amber-700 dark:text-amber-300"><ShieldAlert className="h-3 w-3" /> asks first</span>}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t.description}</p>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                The agent can only do what these tools allow. Email and Slack appear here once they’re configured in the server’s <code>.env</code>.
              </p>
            </div>
          </div>
        ) : !run ? (
          <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
        ) : (
          <div className="mx-auto max-w-5xl px-4 py-6">
            <div className="mb-5 flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="mb-1.5"><StatusBadge status={run.status} /></div>
                <h1 className="font-serif text-xl leading-snug sm:text-2xl">{run.goal}</h1>
              </div>
              {run.active && LIVE.has(run.status) && (
                <Button variant="outline" size="sm" onClick={cancel}><Square className="fill-current !size-3" /> Stop</Button>
              )}
            </div>

            <div className="mb-4 flex gap-1 border-b">
              {(["activity", "report"] as const).map((t) => (
                <button key={t} onClick={() => setTab(t)} disabled={t === "report" && !run.report}
                  className={cn("flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors disabled:opacity-40",
                    tab === t ? "border-brand text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
                  {t === "activity" ? <ListTree className="h-4 w-4" /> : <FileText className="h-4 w-4" />}
                  {t === "activity" ? "Activity" : "Report"}
                </button>
              ))}
            </div>

            {tab === "activity" ? (
              <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
                <ActivityPanel run={run} events={events} onDecide={decide} />
                <section>
                  <h3 className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Timeline</h3>
                  <ol className="thin-scroll max-h-[70vh] space-y-1 overflow-y-auto border-l pl-4">
                    {timeline.map(({ e, text }) => (
                      <li key={e.seq} className="relative text-xs animate-fade-up">
                        <span className={cn("absolute -left-[21px] top-1 h-2 w-2 rounded-full",
                          e.type.includes("error") || e.type === "step_failed" || (e.type === "tool_end" && !e.data.ok) ? "bg-destructive"
                            : e.type === "approval_required" ? "bg-amber-500" : e.type === "step_done" ? "bg-emerald-500" : "bg-border")} />
                        <span className="text-muted-foreground">{new Date(e.at).toLocaleTimeString()}</span>{" "}
                        <span className={cn(e.type === "note" && "italic text-muted-foreground")}>{text}</span>
                      </li>
                    ))}
                    {run.active && <li className="text-xs text-muted-foreground"><Loader2 className="inline h-3 w-3 animate-spin" /> live</li>}
                  </ol>
                </section>
              </div>
            ) : (
              <article className="answer rounded-2xl border bg-card p-6 text-[15px]"><Markdown content={run.report} /></article>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
