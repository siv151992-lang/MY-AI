"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { AlertTriangle, ArrowUp, Check, FileCode2, GitCompare, Loader2, MessageSquarePlus, Square, Undo2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ChatMessage, ModelInfo, ServerMessage } from "@/lib/types";
import { confirmFetch, streamWorkspaceChat, wsApi, type Approval, type EditorContext } from "@/lib/workspace";
import { cn, uid } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { AgentActivity } from "@/components/chat/AgentActivity";
import { Markdown } from "@/components/chat/Markdown";
import type { ConfirmOptions } from "./ConfirmDialog";

type TurnFile = { path: string; status: "added" | "modified" | "deleted" };
type WsMessage = ChatMessage & { approvals?: Approval[]; turn?: { checkpoint: string; files: TurnFile[] | null }; undone?: boolean };
type Conv = { id: string; title: string; updated_at: string };
export type ChatPanelHandle = { send: (text: string) => void };

const QUICK = [
  "Explain how this project is structured and how the main pieces fit together",
  "Find bugs in the open file and fix them, then run the tests",
  "Write unit tests for the open file and make sure they pass",
  "Generate a README with setup, usage and architecture docs",
  "Refactor the open file for readability without changing behaviour",
];

export const ChatPanel = forwardRef<ChatPanelHandle, {
  wsId: string; conversations: Conv[]; models: ModelInfo[]; modelId: string | null; onModel: (id: string) => void;
  editor: EditorContext | null; onWorkspaceChange: (paths: string[]) => void; onOpenTurnDiff: (sha: string, path?: string) => void;
  onCommand: (ev: any) => void; ask: (o: ConfirmOptions) => Promise<false | { value: string }>; onConversations: () => void;
}>(function ChatPanel({ wsId, conversations, models, modelId, onModel, editor, onWorkspaceChange, onOpenTurnDiff, onCommand, ask, onConversations }, ref) {
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WsMessage[]>([]);
  const [input, setInput] = useState("");
  const [useEditor, setUseEditor] = useState(true);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [messages]);

  const load = useCallback(async (id: string | null) => {
    abort.current?.abort();
    setConvId(id);
    setMessages([]);
    if (!id) return;
    const c = await api<{ messages: ServerMessage[] }>(`${wsApi(wsId)}/conversations/${id}`);
    setMessages(c.messages.map((m) => ({
      key: m.id, id: m.id, role: m.role, content: m.content, tools: m.meta?.tools ?? [], plan: m.meta?.plan ?? null,
      verification: m.meta?.verification ?? null, interrupted: m.meta?.interrupted, model: m.meta?.model,
      turn: (m.meta as any)?.checkpoint ? { checkpoint: (m.meta as any).checkpoint, files: null } : undefined,
    })));
  }, [wsId]);

  const patchLast = (fn: (m: WsMessage) => WsMessage) =>
    setMessages((ms) => (ms.length && ms[ms.length - 1].role === "assistant" ? [...ms.slice(0, -1), fn(ms[ms.length - 1])] : ms));

  const handle = (ev: any) => {
    switch (ev.type) {
      case "conversation": setConvId(ev.id); break;
      case "checkpoint": patchLast((m) => ({ ...m, turn: { checkpoint: ev.sha, files: [] } })); break;
      case "status": patchLast((m) => ({ ...m, status: ev.text })); break;
      case "analysis": { const { type, ...a } = ev; patchLast((m) => ({ ...m, analysis: a })); break; }
      case "plan": patchLast((m) => ({ ...m, plan: ev.steps })); break;
      case "token": patchLast((m) => ({ ...m, content: m.content + ev.text, status: null })); break;
      case "reset": patchLast((m) => ({ ...m, content: "", revised: true })); break;
      case "tool_start": patchLast((m) => ({ ...m, status: null, tools: [...(m.tools ?? []), { id: ev.id, name: ev.name, input: ev.input, running: true }] })); break;
      case "tool_end": patchLast((m) => ({ ...m, tools: (m.tools ?? []).map((t) => (t.id === ev.id ? { ...t, output: ev.output, is_error: ev.is_error, running: false } : t)) })); break;
      case "verification": patchLast((m) => ({ ...m, verification: { ok: ev.ok, issues: ev.issues ?? [] } })); break;
      case "workspace_change": onWorkspaceChange(ev.paths ?? []); break;
      case "command_start": case "command_end": onCommand(ev); break;
      case "approval_required": patchLast((m) => ({ ...m, status: "Waiting for your approval", approvals: [...(m.approvals ?? []), ev.approval] })); break;
      case "approval_decided": patchLast((m) => ({ ...m, status: null, approvals: (m.approvals ?? []).map((a) => (a.id === ev.id ? { ...a, status: ev.status } : a)) })); break;
      case "turn_changes": patchLast((m) => ({ ...m, turn: { checkpoint: ev.checkpoint, files: ev.files } })); break;
      case "error": patchLast((m) => ({ ...m, error: ev.message, status: null })); break;
      case "done": patchLast((m) => ({ ...m, id: ev.message.id, model: ev.message.meta?.model, status: null })); break;
      case "title": onConversations(); break;
    }
  };

  async function send(text: string) {
    text = text.trim();
    if (!text || busy) return;
    const ctx = useEditor && editor ? editor : null;
    setMessages((ms) => [...ms,
      { key: uid(), role: "user", content: text + (ctx ? `\n\n— context: ${ctx.path}${ctx.start_line ? ` lines ${ctx.start_line}-${ctx.end_line}` : ""}` : "") },
      { key: uid(), role: "assistant", content: "", streaming: true, tools: [] }]);
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await streamWorkspaceChat(wsId, { content: text, conversation_id: convId, model_id: modelId, editor: ctx }, handle, ctrl.signal);
    } catch (e: any) {
      if (e.name === "AbortError") patchLast((m) => ({ ...m, interrupted: true }));
      else patchLast((m) => ({ ...m, error: e.message }));
    } finally {
      patchLast((m) => ({ ...m, streaming: false, status: null, tools: m.tools?.map((t) => ({ ...t, running: false })) }));
      setBusy(false);
      abort.current = null;
      onConversations();
      onWorkspaceChange([]);
    }
  }
  useImperativeHandle(ref, () => ({ send }));

  async function decide(a: Approval, approved: boolean) {
    try { await api(`${wsApi(wsId)}/approvals/${a.id}`, { method: "POST", body: { approved } }); }
    catch (e: any) { patchLast((m) => ({ ...m, error: e.message })); }
  }

  async function loadTurnFiles(key: string, sha: string) {
    const r = await api<{ files: TurnFile[] }>(`${wsApi(wsId)}/checkpoints/${sha}/changes`);
    setMessages((ms) => ms.map((m) => (m.key === key ? { ...m, turn: { checkpoint: sha, files: r.files } } : m)));
  }

  async function undo(key: string, sha: string, n: number) {
    const ok = await ask({ title: "Undo this turn's changes?", destructive: true, confirmLabel: "Undo changes",
      body: `Restores the project files to how they were before this AI turn${n ? ` (${n} file${n === 1 ? "" : "s"} changed since)` : ""}. Any edits you made after it are reverted too. Your current state is checkpointed first.` });
    if (!ok) return;
    try {
      await confirmFetch(`${wsApi(wsId)}/checkpoints/restore`, { sha });
      setMessages((ms) => ms.map((m) => (m.key === key ? { ...m, undone: true } : m)));
      onWorkspaceChange([]);
    } catch (e: any) { patchLast((m) => ({ ...m, error: e.message })); }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b px-2">
        <select value={convId ?? ""} onChange={(e) => load(e.target.value || null)} className="h-7 min-w-0 flex-1 rounded-md bg-transparent px-1 text-sm font-medium">
          <option value="">New conversation</option>
          {conversations.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
        </select>
        <Button variant="ghost" size="icon-sm" title="New conversation" onClick={() => load(null)}><MessageSquarePlus /></Button>
      </div>
      <div ref={scroller} className="thin-scroll min-h-0 flex-1 space-y-5 overflow-y-auto px-3 py-3">
        {!messages.length && (
          <div className="space-y-2 pt-4 text-sm">
            <p className="font-medium">What should we build or fix?</p>
            <p className="text-xs text-muted-foreground">Aria reads the project, edits files, runs commands and tests in a sandbox, and shows every change as a diff. Deleting files and committing ask you first, and every turn can be undone.</p>
            {QUICK.map((q) => (
              <button key={q} onClick={() => send(q)} className="block w-full rounded-lg border px-3 py-2 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground">{q}</button>
            ))}
          </div>
        )}
        {messages.map((m) => m.role === "user" ? (
          <div key={m.key} className="ml-6 whitespace-pre-wrap rounded-xl bg-secondary px-3 py-2 text-sm">{m.content}</div>
        ) : (
          <div key={m.key} className="space-y-2 text-sm">
            <AgentActivity m={m} />
            {m.approvals?.map((a) => <ApprovalCard key={a.id} a={a} onDecide={decide} />)}
            {!m.content && m.streaming && !m.status && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {m.content && <div className={cn("answer text-[14px]", m.streaming && "streaming")}><Markdown content={m.content} /></div>}
            {m.error && <p className="flex items-start gap-1.5 rounded-lg bg-destructive/10 px-2 py-1.5 text-destructive"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{m.error}</p>}
            {m.interrupted && !m.streaming && <p className="text-xs text-muted-foreground">Stopped.</p>}
            {m.turn && !m.streaming && (m.turn.files === null ? (
              <button className="text-xs text-muted-foreground underline" onClick={() => loadTurnFiles(m.key, m.turn!.checkpoint)}>Show files changed in this turn</button>
            ) : m.turn.files.length > 0 && (
              <div className="rounded-lg border bg-card/50 p-2 text-xs">
                <div className="mb-1 flex items-center gap-2 font-medium">
                  <FileCode2 className="h-3.5 w-3.5" /> {m.turn.files.length} file{m.turn.files.length === 1 ? "" : "s"} changed
                  {m.undone && <span className="rounded bg-muted px-1.5 font-normal">undone</span>}
                  <span className="ml-auto flex gap-1">
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => onOpenTurnDiff(m.turn!.checkpoint)}><GitCompare /> Review</Button>
                    {!m.undone && <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => undo(m.key, m.turn!.checkpoint, m.turn!.files!.length)}><Undo2 /> Undo</Button>}
                  </span>
                </div>
                {m.turn.files.map((f) => (
                  <button key={f.path} onClick={() => onOpenTurnDiff(m.turn!.checkpoint, f.path)} className="flex w-full gap-2 rounded px-1 py-0.5 text-left hover:bg-accent">
                    <span className={cn("w-3 font-semibold", f.status === "added" ? "text-emerald-600" : f.status === "deleted" ? "text-red-600" : "text-amber-600")}>
                      {f.status[0].toUpperCase()}
                    </span>
                    <span className="truncate font-mono">{f.path}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="shrink-0 border-t p-2">
        {editor && (
          <button onClick={() => setUseEditor((v) => !v)} title="Include the open file (and selection) as context"
            className={cn("mb-1.5 flex max-w-full items-center gap-1 rounded-md border px-2 py-0.5 text-xs", useEditor ? "border-brand/40 bg-brand/10" : "text-muted-foreground line-through")}>
            <FileCode2 className="h-3 w-3 shrink-0" />
            <span className="truncate">{editor.path}{editor.start_line ? ` · lines ${editor.start_line}-${editor.end_line}` : ""}</span>
          </button>
        )}
        <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="rounded-xl border bg-card focus-within:ring-1 focus-within:ring-ring">
          <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={3} placeholder="Ask Aria to build, fix, explain, test or refactor…"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            className="block w-full resize-none bg-transparent px-3 pt-2 text-sm outline-none" />
          <div className="flex items-center gap-2 px-2 pb-2">
            <select value={modelId ?? ""} onChange={(e) => onModel(e.target.value)} className="h-7 min-w-0 rounded-md bg-transparent text-xs text-muted-foreground">
              {models.length ? models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>) : <option value="">No models configured</option>}
            </select>
            {busy ? (
              <Button type="button" size="icon-sm" variant="secondary" className="ml-auto" title="Stop" onClick={() => abort.current?.abort()}><Square /></Button>
            ) : (
              <Button type="submit" size="icon-sm" className="ml-auto" title="Send" disabled={!input.trim()}><ArrowUp /></Button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
});

function ApprovalCard({ a, onDecide }: { a: Approval; onDecide: (a: Approval, ok: boolean) => void }) {
  const pending = a.status === "pending";
  const title = { delete: "Delete files?", commit: "Create a commit?", command_deletes: "Apply deletions from a command?" }[a.kind] ?? "Approve action?";
  return (
    <div className={cn("rounded-lg border p-2.5 text-xs", pending ? "border-amber-500/50 bg-amber-500/10" : "opacity-80")}>
      <p className="flex items-center gap-1.5 text-sm font-medium"><AlertTriangle className="h-4 w-4 text-amber-600" /> {title}</p>
      <p className="mt-1">{a.summary}</p>
      {a.details.reason && <p className="mt-1 text-muted-foreground">Reason: {a.details.reason}</p>}
      {a.details.command && <p className="mt-1 font-mono text-muted-foreground">$ {a.details.command}</p>}
      {a.details.message && <pre className="mt-1 whitespace-pre-wrap rounded bg-background/60 p-1.5 font-sans">{a.details.message}</pre>}
      {(a.details.paths?.length || a.details.files?.length) ? (
        <ul className="thin-scroll mt-1 max-h-32 overflow-y-auto font-mono">
          {a.details.paths?.map((p) => <li key={p} className="text-red-600 dark:text-red-400">− {p}</li>)}
          {a.details.files?.map((f) => <li key={f.path}>{f.path} <span className="text-emerald-600">+{f.added}</span> <span className="text-red-600">−{f.removed}</span></li>)}
        </ul>
      ) : null}
      {pending ? (
        <div className="mt-2 flex justify-end gap-2">
          <Button size="sm" variant="ghost" className="h-7" onClick={() => onDecide(a, false)}><X /> Deny</Button>
          <Button size="sm" variant={a.kind === "commit" ? "default" : "destructive"} className="h-7" onClick={() => onDecide(a, true)}><Check /> Approve</Button>
        </div>
      ) : <p className="mt-1.5 font-medium capitalize">{a.status}</p>}
    </div>
  );
}
