"use client";

import { Markdown } from "@/components/chat/Markdown";
import type { TaskResult } from "@/lib/hub";
import { StatusPill } from "./Shell";

export function TaskResultView({ r }: { r: TaskResult }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <StatusPill status={r.status} /><span>model {r.model}</span><span>{r.tool_calls} tool call(s)</span>
      </div>
      {r.error && <p className="text-sm text-destructive">{r.error}</p>}
      {r.result && <div className="answer text-[15px]"><Markdown content={r.result} /></div>}
      {r.sources.length > 0 && <div className="text-xs text-muted-foreground">Sources: {r.sources.join(", ")}</div>}
      {r.notes && <div className="text-xs text-muted-foreground">{r.notes}</div>}
      {r.tools_used.length > 0 && (
        <details className="text-xs text-muted-foreground"><summary>Tools used</summary>
          <ul className="mt-1 space-y-0.5">{r.tools_used.map((t, i) => <li key={i}>{t.ok ? "✓" : "✗"} <span className="font-mono">{t.tool}</span> {t.input}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
