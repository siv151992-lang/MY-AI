"use client";

import { useState } from "react";
import { AlertTriangle, Check, CheckCircle2, ChevronRight, Circle, CircleSlash, Loader2, ShieldAlert, ShieldCheck,
  Wrench, X, XCircle } from "lucide-react";
import type { AgentApproval, AgentEvent, AgentRunDetail, AgentStep } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  planning: { label: "Planning", cls: "bg-sky-500/15 text-sky-700 dark:text-sky-300" },
  running: { label: "Running", cls: "bg-brand/15 text-brand" },
  awaiting_approval: { label: "Needs your approval", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  verifying: { label: "Verifying", cls: "bg-violet-500/15 text-violet-700 dark:text-violet-300" },
  completed: { label: "Completed", cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  completed_with_issues: { label: "Completed with issues", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  failed: { label: "Failed", cls: "bg-destructive/15 text-destructive" },
  cancelled: { label: "Cancelled", cls: "bg-muted text-muted-foreground" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS_LABEL[status] ?? { label: status, cls: "bg-muted" };
  const live = ["planning", "running", "verifying", "awaiting_approval"].includes(status);
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium", s.cls)}>
      {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {s.label}
    </span>
  );
}

function StepIcon({ status }: { status: AgentStep["status"] }) {
  switch (status) {
    case "done": return <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />;
    case "failed": return <XCircle className="h-4 w-4 text-destructive" />;
    case "running": return <Loader2 className="h-4 w-4 animate-spin text-brand" />;
    case "skipped": return <CircleSlash className="h-4 w-4 text-muted-foreground" />;
    default: return <Circle className="h-4 w-4 text-muted-foreground/60" />;
  }
}

function ApprovalCard({ a, onDecide }: { a: AgentApproval; onDecide: (id: string, ok: boolean) => Promise<void> }) {
  const [busy, setBusy] = useState<"y" | "n" | null>(null);
  const [showArgs, setShowArgs] = useState(false);
  const act = async (ok: boolean) => { setBusy(ok ? "y" : "n"); try { await onDecide(a.id, ok); } finally { setBusy(null); } };
  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-3 animate-fade-up">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium">
        <ShieldAlert className="h-4 w-4 text-amber-600" /> Permission needed · step {a.step_idx + 1} · <code className="text-xs">{a.tool}</code>
      </div>
      <pre className="thin-scroll mb-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-background p-2.5 text-xs">{a.summary}</pre>
      <button onClick={() => setShowArgs(!showArgs)} className="mb-2 text-xs text-muted-foreground hover:underline">
        {showArgs ? "Hide" : "Show"} exact parameters
      </button>
      {showArgs && <pre className="thin-scroll mb-2 max-h-48 overflow-auto rounded-lg bg-muted p-2 text-xs">{JSON.stringify(a.args, null, 2)}</pre>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => act(true)} disabled={!!busy}>
          {busy === "y" ? <Loader2 className="animate-spin" /> : <Check />} Approve
        </Button>
        <Button size="sm" variant="outline" onClick={() => act(false)} disabled={!!busy}>
          {busy === "n" ? <Loader2 className="animate-spin" /> : <X />} Deny
        </Button>
      </div>
    </div>
  );
}

function StepRow({ s, live, current }: { s: AgentStep; live: AgentEvent | null; current: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={cn("rounded-lg transition-colors", current && "bg-brand/5 ring-1 ring-brand/30")}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-2.5 px-2.5 py-2 text-left">
        <span className="mt-0.5"><StepIcon status={s.status} /></span>
        <span className="min-w-0 flex-1">
          <span className={cn("block text-sm", s.status === "skipped" && "text-muted-foreground line-through")}>
            {s.idx + 1}. {s.title}
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            {s.tool !== "none" && <span className="rounded bg-muted px-1.5 py-0.5 font-mono">{s.tool}</span>}
            {s.attempts > 1 && <span>attempt {s.attempts}</span>}
            {s.origin !== "plan" && <span className="rounded bg-violet-500/10 px-1.5 py-0.5 text-violet-700 dark:text-violet-300">{s.origin}</span>}
            {current && live && <span className="truncate text-brand">{live.data.summary ?? live.data.tool}…</span>}
          </span>
        </span>
        <ChevronRight className={cn("mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="space-y-1.5 px-9 pb-2.5 text-xs text-muted-foreground animate-fade-up">
          {s.description && <p>{s.description}</p>}
          {s.success_criteria && <p><span className="font-medium text-foreground">Done when:</span> {s.success_criteria}</p>}
          {s.result && <p className="whitespace-pre-wrap rounded-md bg-emerald-500/5 p-2 text-foreground">{s.result}</p>}
          {s.error && <p className="whitespace-pre-wrap rounded-md bg-destructive/5 p-2 text-destructive">{s.error}</p>}
        </div>
      )}
    </li>
  );
}

export function ActivityPanel({ run, events, onDecide }: {
  run: AgentRunDetail; events: AgentEvent[]; onDecide: (id: string, ok: boolean) => Promise<void>;
}) {
  const current = run.steps.find((s) => s.status === "running") ?? null;
  const lastTool = [...events].reverse().find((e) => e.type === "tool_start" || e.type === "tool_end") ?? null;
  const liveTool = lastTool?.type === "tool_start" ? lastTool : null;
  const pending = run.approvals.filter((a) => a.status === "pending" && run.active);
  const done = run.steps.filter((s) => s.status === "done").length;
  const total = run.steps.filter((s) => s.status !== "skipped").length;
  const errors = events.filter((e) => e.type === "step_error" || e.type === "warning" || (e.type === "tool_end" && !e.data.ok));

  return (
    <div className="space-y-4">
      {/* progress */}
      <div>
        <div className="mb-1.5 flex items-center justify-between text-xs text-muted-foreground">
          <span>{done} of {total || "?"} steps completed</span>
          <span>{run.actions.length} action{run.actions.length === 1 ? "" : "s"} performed</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-brand transition-all duration-500" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </div>
      </div>

      {pending.map((a) => <ApprovalCard key={a.id} a={a} onDecide={onDecide} />)}

      {/* current task */}
      <section className="rounded-xl border bg-card p-3">
        <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Current task</h3>
        {current ? (
          <div>
            <p className="text-sm font-medium">{current.idx + 1}. {current.title}</p>
            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              {liveTool ? <><Wrench className="h-3 w-3" /> {liveTool.data.summary}</> :
                run.status === "awaiting_approval" ? <><ShieldAlert className="h-3 w-3" /> Waiting for your approval</> :
                <><Loader2 className="h-3 w-3 animate-spin" /> Working{current.attempts > 1 ? ` (attempt ${current.attempts})` : ""}</>}
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {run.status === "planning" ? "Building the execution plan…" : run.status === "verifying" ? "Verifying the goal was achieved…"
              : run.active ? "Preparing…" : "No task running."}
          </p>
        )}
      </section>

      {/* steps */}
      <section>
        <h3 className="mb-1 px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Plan & steps</h3>
        {run.steps.length === 0 ? (
          <p className="px-1 text-sm text-muted-foreground">{run.active ? "Planning…" : "No steps."}</p>
        ) : (
          <ol className="space-y-0.5">
            {run.steps.map((s) => <StepRow key={s.id || s.idx} s={s} current={s.status === "running"} live={liveTool} />)}
          </ol>
        )}
      </section>

      {/* errors */}
      {errors.length > 0 && (
        <section>
          <h3 className="mb-1 px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Errors & recovery</h3>
          <ul className="space-y-1">
            {errors.map((e) => (
              <li key={e.seq} className="flex gap-2 rounded-lg bg-destructive/5 px-2.5 py-2 text-xs">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                <span>
                  <span className="font-medium">Step {(e.data.step ?? 0) + 1}</span>
                  {e.type === "step_error" && <> · attempt {e.data.attempt}: {e.data.error}{e.data.will_retry ? " — retrying" : ""}</>}
                  {e.type === "warning" && <>: {e.data.message}</>}
                  {e.type === "tool_end" && <> · <code>{e.data.tool}</code> failed: {String(e.data.output).slice(0, 300)}</>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.verification && (
        <section className={cn("flex gap-2 rounded-xl p-3 text-sm",
          run.verification.achieved === "yes" ? "bg-emerald-500/10" : "bg-amber-500/10")}>
          {run.verification.achieved === "yes" ? <ShieldCheck className="mt-0.5 h-4 w-4 text-emerald-600" /> : <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />}
          <div>
            <p className="font-medium">Verification: goal {run.verification.achieved === "yes" ? "achieved" : run.verification.achieved === "partial" ? "partially achieved" : "not achieved"}</p>
            {run.verification.issues.length > 0 && <ul className="mt-1 list-disc pl-4 text-xs">{run.verification.issues.map((i, n) => <li key={n}>{i}</li>)}</ul>}
          </div>
        </section>
      )}
    </div>
  );
}
