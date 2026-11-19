"use client";

import { useState } from "react";
import { Brain, Check, History, Pencil, X } from "lucide-react";
import { api } from "@/lib/api";
import type { MemorySuggestion, MessageMemory } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/** Shows which memories informed an answer, so their use is never invisible. */
export function MemoryUsed({ memory, onOpen }: { memory: MessageMemory; onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  const n = memory.used.length;
  if (!n && !memory.recalled && !memory.compacted) return null;
  const known = memory.used.filter((m) => m.content);
  const parts = [
    n ? `${n} ${n === 1 ? "memory" : "memories"}` : null,
    memory.recalled ? `${memory.recalled} earlier ${memory.recalled === 1 ? "section" : "sections"} recalled` : null,
    memory.compacted ? "older messages condensed" : null,
  ].filter(Boolean);
  return (
    <div className="mb-2 text-xs text-muted-foreground">
      <button onClick={() => (known.length ? setOpen((v) => !v) : onOpen())}
        className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 hover:bg-accent">
        {memory.used.length ? <Brain className="h-3.5 w-3.5" /> : <History className="h-3.5 w-3.5" />}
        Used {parts.join(" · ")}
      </button>
      {open && (
        <div className="mt-2 space-y-1 rounded-lg border bg-card/50 p-2">
          {known.map((m) => <p key={m.id}>• {m.content} <span className="opacity-70">({m.category})</span></p>)}
          <button className="underline" onClick={onOpen}>Manage memories</button>
        </div>
      )}
    </div>
  );
}

/** Inline approval card for facts Aria learned from this exchange. */
export function MemorySuggestions({ items, onOpen }: { items: MemorySuggestion[]; onOpen: () => void }) {
  const [state, setState] = useState(items);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  if (!state.length) return null;

  const mark = (id: string, decision: MemorySuggestion["decision"], content?: string) =>
    setState((xs) => xs.map((x) => (x.id === id ? { ...x, decision, content: content ?? x.content } : x)));

  async function save(s: MemorySuggestion, content?: string) {
    try {
      await api(`/api/memories/${s.id}/approve`, { method: "POST", body: content ? { content } : {} });
      mark(s.id, "saved", content);
    } catch { mark(s.id, "error"); }
    setEditing(null);
  }
  async function dismiss(s: MemorySuggestion) {
    try {
      // auto-saved memories (approval turned off) are deleted; suggestions are rejected
      await api(s.status === "active" ? `/api/memories/${s.id}` : `/api/memories/${s.id}/reject`,
        { method: s.status === "active" ? "DELETE" : "POST" });
      mark(s.id, "dismissed");
    } catch { mark(s.id, "error"); }
  }

  const pending = state.some((s) => s.status === "pending");
  return (
    <div className="mt-3 rounded-xl border border-brand/30 bg-brand/5 p-3 text-sm">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Brain className="h-3.5 w-3.5 text-brand" />
        {pending ? "Remember this for future chats?" : "Saved to memory (you turned off approvals)"}
      </p>
      <div className="space-y-1.5">
        {state.map((s) => (
          <div key={s.id} className={cn("flex items-start gap-2", s.decision === "dismissed" && "opacity-50 line-through")}>
            {editing === s.id ? (
              <form className="flex flex-1 gap-1" onSubmit={(e) => { e.preventDefault(); save(s, draft.trim()); }}>
                <input value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus maxLength={500}
                  className="h-8 flex-1 rounded-md border border-input bg-background px-2 text-sm" />
                <Button size="sm" type="submit" disabled={draft.trim().length < 2}>Save</Button>
              </form>
            ) : (
              <>
                <span className="flex-1">
                  {s.content}
                  <span className="ml-1.5 text-xs text-muted-foreground">({s.category}{s.supersedes_id ? ", updates an older memory" : ""})</span>
                </span>
                {s.decision === "saved" && <span className="text-xs text-brand">Saved</span>}
                {s.decision === "error" && <span className="text-xs text-destructive">Failed</span>}
                {!s.decision && (
                  <span className="flex shrink-0 gap-0.5">
                    {s.status === "pending" && (
                      <>
                        <Button size="icon-sm" variant="ghost" title="Save" onClick={() => save(s)}><Check /></Button>
                        <Button size="icon-sm" variant="ghost" title="Correct, then save" onClick={() => { setEditing(s.id); setDraft(s.content); }}><Pencil /></Button>
                      </>
                    )}
                    <Button size="icon-sm" variant="ghost" title={s.status === "pending" ? "Don't remember" : "Forget this"} onClick={() => dismiss(s)}><X /></Button>
                  </span>
                )}
              </>
            )}
          </div>
        ))}
      </div>
      <button className="mt-2 text-xs text-muted-foreground underline" onClick={onOpen}>Review all memory</button>
    </div>
  );
}
