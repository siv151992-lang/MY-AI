"use client";

import { useState } from "react";
import { AlertTriangle, ChevronRight, Code2, FileSearch, Globe, ListChecks, Loader2, Search, ShieldCheck,
  Brain, Wrench, Lightbulb } from "lucide-react";
import type { ChatMessage, ToolRun } from "@/lib/types";
import { cn } from "@/lib/utils";

const TOOL_META: Record<string, { icon: any; label: (i: any) => string }> = {
  web_search: { icon: Search, label: (i) => `Searched the web for “${i.query ?? ""}”` },
  fetch_url: { icon: Globe, label: (i) => `Read ${safeHost(i.url)}` },
  run_python: { icon: Code2, label: () => "Ran Python code" },
  search_documents: { icon: FileSearch, label: (i) => `Searched your files for “${i.query ?? ""}”` },
  save_memory: { icon: Brain, label: () => "Saved to memory" },
};

function safeHost(u: unknown) {
  try { return new URL(String(u)).host; } catch { return String(u ?? "a page"); }
}

function ToolRow({ t }: { t: ToolRun }) {
  const [open, setOpen] = useState(false);
  const meta = TOOL_META[t.name] ?? { icon: Wrench, label: () => `Used ${t.name}` };
  const Icon = meta.icon;
  return (
    <div className="rounded-lg">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground">
        {t.running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className={cn("h-3.5 w-3.5", t.is_error && "text-destructive")} />}
        <span className="truncate">{meta.label(t.input)}</span>
        {t.is_error && <span className="text-xs text-destructive">failed</span>}
        <ChevronRight className={cn("ml-auto h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="ml-6 mt-1 space-y-2 animate-fade-up">
          {t.name === "run_python" && typeof t.input.code === "string" ? (
            <pre className="thin-scroll max-h-64 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs">{t.input.code}</pre>
          ) : (
            <pre className="thin-scroll overflow-auto rounded-lg bg-muted p-2 font-mono text-xs">{JSON.stringify(t.input, null, 2)}</pre>
          )}
          {t.output && (
            <pre className="thin-scroll max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border p-3 font-mono text-xs text-muted-foreground">{t.output}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function understandingWorthShowing(a: ChatMessage["analysis"]) {
  return !!a && (a.parts.length > 1 || a.assumptions.length > 0 || !!a.interpretation || a.missing_info.length > 0);
}

function Understanding({ a, defaultOpen }: { a: NonNullable<ChatMessage["analysis"]>; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const notes = [...(a.interpretation ? [`Interpreted as: ${a.interpretation}`] : []),
    ...a.assumptions.map((x) => `Assuming ${x.charAt(0).toLowerCase()}${x.slice(1)}`)];
  return (
    <div>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-medium hover:bg-accent/60">
        <Lightbulb className="h-3.5 w-3.5 text-brand" /> Understanding
        <span className="text-xs font-normal text-muted-foreground">
          {a.task_types.join(" · ")}{a.parts.length > 1 ? ` · ${a.parts.length} parts` : ""}
        </span>
        <ChevronRight className={cn("ml-auto h-3.5 w-3.5 transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mb-1 ml-8 space-y-1.5 text-sm text-muted-foreground animate-fade-up">
          {a.parts.length > 1 && (
            <ul className="list-disc space-y-0.5 pl-4">{a.parts.map((p, i) => <li key={i}>{p}</li>)}</ul>
          )}
          {notes.map((n, i) => <p key={i} className="italic">{n}</p>)}
          {a.missing_info.length > 0 && (
            <p className="text-amber-600 dark:text-amber-400">Missing: {a.missing_info.join("; ")}</p>
          )}
        </div>
      )}
    </div>
  );
}

export function AgentActivity({ m }: { m: ChatMessage }) {
  const [planOpen, setPlanOpen] = useState(true);
  const showUnderstanding = understandingWorthShowing(m.analysis);
  const hasActivity = showUnderstanding || (m.plan && m.plan.length) || (m.tools && m.tools.length) || m.status || m.verification;
  if (!hasActivity) return null;
  return (
    <div className="mb-3 space-y-1 rounded-xl border bg-card/50 p-2">
      {showUnderstanding && <Understanding a={m.analysis!} defaultOpen={!!m.streaming} />}
      {m.plan && m.plan.length > 0 && (
        <div>
          <button onClick={() => setPlanOpen(!planOpen)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-medium hover:bg-accent/60">
            <ListChecks className="h-3.5 w-3.5 text-brand" /> Plan
            <ChevronRight className={cn("ml-auto h-3.5 w-3.5 transition-transform", planOpen && "rotate-90")} />
          </button>
          {planOpen && (
            <ol className="mb-1 ml-8 list-decimal space-y-0.5 text-sm text-muted-foreground">
              {m.plan.map((s, i) => <li key={i}>{s}</li>)}
            </ol>
          )}
        </div>
      )}
      {m.tools?.map((t) => <ToolRow key={t.id} t={t} />)}
      {m.status && m.streaming && (
        <div className="flex items-center gap-2 px-2 py-1.5 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> {m.status}…
        </div>
      )}
      {m.verification && (
        <div className={cn("flex items-start gap-2 px-2 py-1.5 text-sm", m.verification.ok ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400")}>
          {m.verification.ok ? <ShieldCheck className="mt-0.5 h-3.5 w-3.5" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5" />}
          <span>
            {m.verification.ok
              ? "Answer self-checked"
              : m.streaming
                ? `Self-check found issues — revising: ${m.verification.issues.join("; ")}`
                : m.revised
                  ? `Revised after self-check flagged: ${m.verification.issues.join("; ")}`
                  : `Self-check flagged: ${m.verification.issues.join("; ")}`}
          </span>
        </div>
      )}
    </div>
  );
}
