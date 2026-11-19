"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, ArchiveRestore, Check, Download, History, Loader2, Pencil, Pin, PinOff, Plus, Search, Trash2, Upload, X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type {
  AuditEntry, ConversationMemory, Memory, MemoryCategory, MemoryDetail, MemoryList, MemorySettings, MemoryStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

const CATEGORIES: MemoryCategory[] = ["instruction", "profile", "preference", "project", "relationship", "fact", "other"];
const CATEGORY_HINT: Record<MemoryCategory, string> = {
  instruction: "How Aria should respond", profile: "Who you are", preference: "Likes, tools, styles",
  project: "Ongoing work and goals", relationship: "People in your life", fact: "Other durable facts", other: "Anything else",
};
type Tab = "active" | "pending" | "archived" | "chat" | "privacy" | "activity";

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "never");
const selectCls = "h-9 rounded-lg border border-input bg-background px-2 text-sm";

export function MemoryDialog({ open, onOpenChange, conversationId, initialTab, onChanged }: {
  open: boolean; onOpenChange: (v: boolean) => void; conversationId?: string | null; initialTab?: Tab;
  onChanged?: () => void;
}) {
  const [tab, setTab] = useState<Tab>("active");
  const [counts, setCounts] = useState<Record<MemoryStatus, number>>({ active: 0, pending: 0, archived: 0 });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setTab(initialTab ?? "active"); setError(null); setNotice(null); } }, [open, initialTab]);

  const refreshCounts = useCallback(async () => {
    try { setCounts((await api<MemoryList>("/api/memories?status=pending")).counts); } catch {}
  }, []);
  useEffect(() => { if (open) refreshCounts(); }, [open, refreshCounts]);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const changed = () => { refreshCounts(); onChanged?.(); };

  async function importFile(f: File) {
    const fd = new FormData();
    fd.append("file", f);
    try {
      const res = await fetch("/api/memories/import", { method: "POST", body: fd, credentials: "same-origin", headers: { "X-Requested-With": "aria" } });
      const body = await res.json();
      if (!res.ok) throw new ApiError(body.detail ?? "Import failed", res.status);
      setNotice(`Imported ${body.imported} memories${body.duplicates ? `, ${body.duplicates} already known` : ""}${body.skipped.length ? `, ${body.skipped.length} skipped` : ""}.`);
      changed();
      setTab("active");
    } catch (e) { fail(e); }
  }

  const tabs: { id: Tab; label: string; badge?: number; hidden?: boolean }[] = [
    { id: "active", label: "Memories", badge: counts.active },
    { id: "pending", label: "Suggestions", badge: counts.pending },
    { id: "archived", label: "Archived", badge: counts.archived },
    { id: "chat", label: "This chat", hidden: !conversationId },
    { id: "privacy", label: "Privacy" },
    { id: "activity", label: "Activity" },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[88dvh] max-w-3xl flex-col gap-3 p-4 sm:p-6">
        <div className="pr-8">
          <DialogTitle>Memory</DialogTitle>
          <DialogDescription>
            What Aria remembers about you. Inspect, correct or delete anything; nothing it learns on its own is kept without your approval unless you change that in Privacy.
          </DialogDescription>
        </div>
        <div className="flex flex-wrap items-center gap-1 border-b pb-2">
          {tabs.filter((t) => !t.hidden).map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={cn("rounded-lg px-3 py-1.5 text-sm transition-colors", tab === t.id ? "bg-secondary font-medium" : "text-muted-foreground hover:bg-accent")}>
              {t.label}
              {!!t.badge && <span className={cn("ml-1.5 rounded-full px-1.5 text-xs", t.id === "pending" ? "bg-brand text-white" : "bg-muted")}>{t.badge}</span>}
            </button>
          ))}
          <div className="ml-auto flex gap-1">
            <Button variant="ghost" size="sm" asChild title="Download everything Aria remembers as JSON">
              <a href="/api/memories/export?format=json" download><Download /> JSON</a>
            </Button>
            <Button variant="ghost" size="sm" asChild title="Download a readable Markdown copy">
              <a href="/api/memories/export?format=markdown" download><Download /> Markdown</a>
            </Button>
            <Button variant="ghost" size="sm" title="Import memories from an Aria JSON export" onClick={() => importRef.current?.click()}><Upload /> Import</Button>
            <input ref={importRef} type="file" accept="application/json,.json" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) importFile(f); }} />
          </div>
        </div>
        {error && <Banner tone="error" onClose={() => setError(null)}>{error}</Banner>}
        {notice && <Banner onClose={() => setNotice(null)}>{notice}</Banner>}
        <div className="thin-scroll -mx-1 min-h-0 flex-1 overflow-y-auto px-1">
          {open && (tab === "active" || tab === "archived") && <MemoryListView key={tab} status={tab} onError={fail} onChanged={changed} />}
          {open && tab === "pending" && <SuggestionsView onError={fail} onChanged={changed} />}
          {open && tab === "chat" && conversationId && <ConversationView conversationId={conversationId} onError={fail} onChanged={changed} />}
          {open && tab === "privacy" && <PrivacyView onError={fail} onNotice={setNotice} onChanged={changed} />}
          {open && tab === "activity" && <ActivityView onError={fail} />}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Banner({ children, tone, onClose }: { children: React.ReactNode; tone?: "error"; onClose: () => void }) {
  return (
    <div className={cn("flex items-start justify-between gap-2 rounded-lg px-3 py-2 text-sm",
      tone === "error" ? "bg-destructive/10 text-destructive" : "bg-brand/10")}>
      <span>{children}</span>
      <button onClick={onClose} title="Dismiss"><X className="h-4 w-4" /></button>
    </div>
  );
}

function Spinner() {
  return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-8 text-center text-sm text-muted-foreground">{children}</p>;
}

// ---------------------------------------------------------------- memories
function MemoryListView({ status, onError, onChanged }: { status: "active" | "archived"; onError: (e: unknown) => void; onChanged: () => void }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<"" | MemoryCategory>("");
  const [sort, setSort] = useState<"recent" | "importance" | "used">("recent");
  const [draft, setDraft] = useState("");
  const [draftCat, setDraftCat] = useState<"" | MemoryCategory>("");

  const load = useCallback(async () => {
    const p = new URLSearchParams({ status, sort });
    if (q.trim()) p.set("q", q.trim());
    if (category) p.set("category", category);
    try { setItems((await api<MemoryList>(`/api/memories?${p}`)).items); } catch (e) { onError(e); }
  }, [status, q, category, sort, onError]);

  useEffect(() => { const t = setTimeout(load, q ? 300 : 0); return () => clearTimeout(t); }, [load, q]);

  async function add() {
    if (!draft.trim()) return;
    try {
      const m = await api<Memory & { duplicate?: boolean }>("/api/memories", { method: "POST", body: { content: draft.trim(), category: draftCat || null } });
      setDraft("");
      if (m.duplicate) onError(new Error("Aria already remembers that, so it was marked as more important instead."));
      await load();
      onChanged();
    } catch (e) { onError(e); }
  }

  const replace = (m: Memory) => setItems((xs) => xs?.map((x) => (x.id === m.id ? { ...x, ...m } : x)) ?? null);
  const drop = (id: string) => { setItems((xs) => xs?.filter((x) => x.id !== id) ?? null); onChanged(); };

  return (
    <div className="space-y-3">
      {status === "active" && (
        <form onSubmit={(e) => { e.preventDefault(); add(); }} className="flex flex-wrap gap-2">
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add something for Aria to remember…" maxLength={500} className="min-w-[12rem] flex-1" />
          <select value={draftCat} onChange={(e) => setDraftCat(e.target.value as MemoryCategory | "")} className={selectCls} title="Category (guessed if left on auto)">
            <option value="">Auto category</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <Button type="submit" disabled={!draft.trim()}><Plus /> Add</Button>
        </form>
      )}
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by meaning, e.g. “what tools do I use?”" className="pl-8" />
        </div>
        <select value={category} onChange={(e) => setCategory(e.target.value as MemoryCategory | "")} className={selectCls}>
          <option value="">All categories</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {!q && (
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} className={selectCls}>
            <option value="recent">Recent</option>
            <option value="importance">Most important</option>
            <option value="used">Most used</option>
          </select>
        )}
      </div>
      {items === null ? <Spinner /> : items.length === 0 ? (
        <Empty>{q ? "No memories match that search." : status === "active" ? "Nothing remembered yet." : "Nothing archived."}</Empty>
      ) : (
        <div className="space-y-1">
          {items.map((m) => <MemoryRow key={m.id} m={m} onReplace={replace} onRemove={drop} onError={onError} />)}
        </div>
      )}
    </div>
  );
}

function ImportanceMeter({ value, onSet }: { value: number; onSet?: (v: number) => void }) {
  const filled = Math.max(1, Math.round(value * 5));
  return (
    <span className="inline-flex items-center gap-0.5" title={`Importance ${(value * 100).toFixed(0)}%${onSet ? " (click to change)" : ""}`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <button key={i} type="button" disabled={!onSet} onClick={() => onSet?.(i / 5)}
          className={cn("h-2 w-2 rounded-full", i <= filled ? "bg-brand" : "bg-muted", onSet && "hover:scale-125")} />
      ))}
    </span>
  );
}

function Badge({ children, tone }: { children: React.ReactNode; tone?: "warn" }) {
  return <span className={cn("rounded px-1.5 py-0.5 text-[11px]", tone === "warn" ? "bg-amber-500/15 text-amber-700 dark:text-amber-400" : "bg-muted text-muted-foreground")}>{children}</span>;
}

function MemoryRow({ m, onReplace, onRemove, onError }: {
  m: Memory; onReplace: (m: Memory) => void; onRemove: (id: string) => void; onError: (e: unknown) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.content);
  const [cat, setCat] = useState<MemoryCategory>(m.category);
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function patch(body: Partial<Pick<Memory, "content" | "category" | "importance" | "pinned">> & { status?: "active" | "archived" }) {
    try {
      const out = await api<Memory>(`/api/memories/${m.id}`, { method: "PATCH", body });
      if (body.status && body.status !== m.status) onRemove(m.id); else onReplace(out);
      if (showHistory) setDetail(await api<MemoryDetail>(`/api/memories/${m.id}`));
      return true;
    } catch (e) { onError(e); return false; }
  }

  async function toggleHistory() {
    if (!showHistory && !detail) {
      try { setDetail(await api<MemoryDetail>(`/api/memories/${m.id}`)); } catch (e) { onError(e); return; }
    }
    setShowHistory((v) => !v);
  }

  async function remove() {
    if (!confirmDelete) { setConfirmDelete(true); setTimeout(() => setConfirmDelete(false), 3000); return; }
    try { await api(`/api/memories/${m.id}`, { method: "DELETE" }); onRemove(m.id); } catch (e) { onError(e); }
  }

  return (
    <div className="group rounded-lg px-3 py-2 text-sm hover:bg-accent/50">
      {editing ? (
        <form className="space-y-2" onSubmit={async (e) => { e.preventDefault(); if (await patch({ content: text, category: cat })) setEditing(false); }}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={500} rows={2} autoFocus
            className="w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm" />
          <div className="flex gap-2">
            <select value={cat} onChange={(e) => setCat(e.target.value as MemoryCategory)} className={selectCls}>
              {CATEGORIES.map((c) => <option key={c} value={c} title={CATEGORY_HINT[c]}>{c}</option>)}
            </select>
            <Button type="submit" size="sm" disabled={text.trim().length < 2}><Check /> Save correction</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setEditing(false); setText(m.content); setCat(m.category); }}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="break-words">{m.pinned && <Pin className="mr-1 inline h-3.5 w-3.5 text-brand" />}{m.content}</p>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <Badge>{m.category}</Badge>
              {m.sensitivity !== "normal" && <Badge tone="warn">{m.sensitivity}</Badge>}
              <ImportanceMeter value={m.importance} onSet={m.status === "active" ? (v) => patch({ importance: v }) : undefined} />
              <span>· {m.source === "extracted" ? "learned" : m.source === "explicit" ? "you asked" : m.source === "import" ? "imported" : "added by you"}</span>
              <span>· used {m.access_count}×</span>
              {typeof m.score === "number" && <span>· match {(m.relevance ?? m.score).toFixed(2)}</span>}
            </div>
          </div>
          <div className="flex shrink-0 gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
            {m.status === "active" && (
              <Button variant="ghost" size="icon-sm" title={m.pinned ? "Unpin" : "Pin: always include this memory"} onClick={() => patch({ pinned: !m.pinned })}>
                {m.pinned ? <PinOff /> : <Pin />}
              </Button>
            )}
            <Button variant="ghost" size="icon-sm" title="Correct" onClick={() => setEditing(true)}><Pencil /></Button>
            <Button variant="ghost" size="icon-sm" title="Details and history" onClick={toggleHistory}><History /></Button>
            {m.status === "active" ? (
              <Button variant="ghost" size="icon-sm" title="Archive (stop using, keep a copy)" onClick={() => patch({ status: "archived" })}><Archive /></Button>
            ) : (
              <Button variant="ghost" size="icon-sm" title="Restore" onClick={() => patch({ status: "active" })}><ArchiveRestore /></Button>
            )}
            <Button variant="ghost" size="icon-sm" title={confirmDelete ? "Click again to delete permanently" : "Delete permanently"}
              className={cn(confirmDelete && "bg-destructive/15 text-destructive")} onClick={remove}><Trash2 /></Button>
          </div>
        </div>
      )}
      {showHistory && detail && (
        <div className="mt-2 space-y-2 rounded-lg border bg-card/50 p-3 text-xs">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-muted-foreground sm:grid-cols-3">
            <span>Created {fmtDate(detail.created_at)}</span>
            <span>Last used {fmtDate(detail.last_accessed_at)}</span>
            <span>Confidence {(detail.confidence * 100).toFixed(0)}%</span>
            {detail.source_conversation && (
              <a className="col-span-full underline" href={`/chat/${detail.source_conversation.id}`}>From chat: {detail.source_conversation.title}</a>
            )}
          </div>
          {detail.source_excerpt && <blockquote className="border-l-2 pl-2 italic text-muted-foreground">“{detail.source_excerpt}”</blockquote>}
          <div>
            <p className="mb-1 font-medium">History</p>
            <ol className="space-y-1">
              {detail.revisions.map((r) => (
                <li key={r.version} className="flex gap-2">
                  <span className="shrink-0 text-muted-foreground">v{r.version} · {fmtDate(r.created_at)} · {r.changed_by}</span>
                  <span className="min-w-0 flex-1">{r.content}{r.reason && <span className="text-muted-foreground"> ({r.reason})</span>}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- suggestions
function SuggestionsView({ onError, onChanged }: { onError: (e: unknown) => void; onChanged: () => void }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [replaced, setReplaced] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const [pending, active] = await Promise.all([
        api<MemoryList>("/api/memories?status=pending"), api<MemoryList>("/api/memories?status=active"),
      ]);
      setItems(pending.items);
      setReplaced(Object.fromEntries(active.items.map((m) => [m.id, m.content])));
    } catch (e) { onError(e); }
  }, [onError]);
  useEffect(() => { load(); }, [load]);

  const done = (id: string) => { setItems((xs) => xs?.filter((x) => x.id !== id) ?? null); onChanged(); };

  async function approve(m: Memory) {
    const content = edits[m.id]?.trim();
    try {
      await api(`/api/memories/${m.id}/approve`, { method: "POST", body: content && content !== m.content ? { content } : {} });
      done(m.id);
    } catch (e) { onError(e); }
  }
  async function reject(m: Memory) {
    try { await api(`/api/memories/${m.id}/reject`, { method: "POST" }); done(m.id); } catch (e) { onError(e); }
  }
  async function all(action: "approve" | "reject") {
    if (!items?.length) return;
    try {
      await api("/api/memories/review", { method: "POST", body: { [action]: items.map((m) => m.id) } });
      setItems([]);
      onChanged();
    } catch (e) { onError(e); }
  }

  if (items === null) return <Spinner />;
  if (!items.length) return <Empty>No suggestions waiting. When Aria notices something worth remembering, it asks you here first.</Empty>;
  return (
    <div className="space-y-2">
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="outline" onClick={() => all("reject")}>Dismiss all</Button>
        <Button size="sm" onClick={() => all("approve")}>Save all</Button>
      </div>
      {items.map((m) => (
        <div key={m.id} className="space-y-2 rounded-lg border p-3 text-sm">
          <textarea value={edits[m.id] ?? m.content} onChange={(e) => setEdits((x) => ({ ...x, [m.id]: e.target.value }))}
            rows={2} maxLength={500} className="w-full resize-none rounded-md bg-transparent focus:outline-none focus:ring-1 focus:ring-ring" />
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <Badge>{m.category}</Badge>
            {m.sensitivity !== "normal" && <Badge tone="warn">{m.sensitivity}</Badge>}
            <ImportanceMeter value={m.importance} />
            {m.confidence < 0.6 && <Badge tone="warn">unsure</Badge>}
            {m.supersedes_id && <span>· replaces “{replaced[m.supersedes_id] ?? "an older memory"}”</span>}
          </div>
          {m.source_excerpt && <blockquote className="border-l-2 pl-2 text-xs italic text-muted-foreground">You said: “{m.source_excerpt}”</blockquote>}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => reject(m)}><X /> Dismiss</Button>
            <Button size="sm" onClick={() => approve(m)}><Check /> Save</Button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- this conversation
function ConversationView({ conversationId, onError, onChanged }: { conversationId: string; onError: (e: unknown) => void; onChanged: () => void }) {
  const [data, setData] = useState<ConversationMemory | null>(null);
  const [summary, setSummary] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api<ConversationMemory>(`/api/conversations/${conversationId}/memory`);
      setData(d);
      setSummary(d.summary ?? "");
    } catch (e) { onError(e); }
  }, [conversationId, onError]);
  useEffect(() => { load(); }, [load]);

  async function patch(body: { memory_enabled?: boolean; summary?: string }) {
    try {
      const d = await api<ConversationMemory>(`/api/conversations/${conversationId}/memory`, { method: "PATCH", body });
      setData(d);
      setSummary(d.summary ?? "");
      onChanged();
    } catch (e) { onError(e); }
  }
  async function reset() {
    if (!confirmReset) { setConfirmReset(true); setTimeout(() => setConfirmReset(false), 3000); return; }
    try { await api(`/api/conversations/${conversationId}/memory`, { method: "DELETE" }); await load(); } catch (e) { onError(e); }
  }

  if (!data) return <Spinner />;
  const stateEntries = Object.entries(data.state).filter(([, v]) => v?.length);
  return (
    <div className="space-y-5 text-sm">
      <Toggle label="Use long-term memory in this chat"
        hint="Off = incognito for memory: Aria neither reads your memories nor learns new ones here."
        checked={data.memory_enabled} onChange={(v) => patch({ memory_enabled: v })} />
      <section className="space-y-2">
        <h3 className="font-medium">Context window</h3>
        <p className="text-muted-foreground">
          {data.summarized_upto > 0
            ? `${data.summarized_upto} of ${data.total_messages} messages have been condensed into the summary below; the latest ${data.total_messages - data.summarized_upto} are sent word for word.`
            : `All ${data.total_messages} messages fit in the context window, so nothing has been condensed yet.`}
        </p>
      </section>
      {(data.summary !== null || data.summarized_upto > 0) && (
        <section className="space-y-2">
          <h3 className="font-medium">Running summary</h3>
          <textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={6} maxLength={8000}
            className="w-full rounded-lg border border-input bg-background px-3 py-2" />
          <div className="flex justify-end">
            <Button size="sm" disabled={summary === (data.summary ?? "")} onClick={() => patch({ summary })}><Check /> Save correction</Button>
          </div>
        </section>
      )}
      {data.segments.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-medium">Recallable sections ({data.segments.length})</h3>
          <p className="text-xs text-muted-foreground">Aria searches these when an older detail becomes relevant again.</p>
          {data.segments.map((s) => (
            <div key={s.id} className="rounded-lg border p-2">
              <p className="mb-1 text-xs text-muted-foreground">Messages {s.from_seq}–{s.to_seq}</p>
              <p className="whitespace-pre-wrap">{s.summary}</p>
            </div>
          ))}
        </section>
      )}
      {stateEntries.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-medium">Working state</h3>
          {stateEntries.map(([k, v]) => (
            <div key={k}>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{k.replace("_", " ")}</p>
              <ul className="list-disc pl-5">{v.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
          ))}
        </section>
      )}
      {data.memories.length > 0 && (
        <section className="space-y-1">
          <h3 className="font-medium">Long-term memories learned in this chat</h3>
          {data.memories.map((m) => <p key={m.id}>• {m.content} <span className="text-xs text-muted-foreground">({m.status})</span></p>)}
        </section>
      )}
      <div className="border-t pt-3">
        <Button size="sm" variant="outline" className={cn(confirmReset && "border-destructive text-destructive")} onClick={reset}>
          <Trash2 /> {confirmReset ? "Click again to clear summary, sections and state" : "Clear this chat's summary"}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- privacy
function Toggle({ label, hint, checked, onChange, disabled }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className={cn("flex cursor-pointer items-start justify-between gap-4", disabled && "cursor-not-allowed opacity-50")}>
      <span>
        <span className="block text-sm font-medium">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
      <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
        className={cn("relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors", checked ? "bg-brand" : "bg-muted")}>
        <span className={cn("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all", checked ? "left-[1.125rem]" : "left-0.5")} />
      </button>
    </label>
  );
}

function PrivacyView({ onError, onNotice, onChanged }: { onError: (e: unknown) => void; onNotice: (s: string) => void; onChanged: () => void }) {
  const [s, setS] = useState<MemorySettings | null>(null);
  const [wipeConvs, setWipeConvs] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  useEffect(() => { api<MemorySettings>("/api/memories/settings").then(setS).catch(onError); }, [onError]);

  async function save(patch: Partial<MemorySettings>) {
    if (!s) return;
    setS({ ...s, ...patch });
    try { setS(await api<MemorySettings>("/api/memories/settings", { method: "PUT", body: patch })); } catch (e) { onError(e); }
  }
  async function wipe() {
    try {
      const r = await api<{ deleted: number }>("/api/memories/delete", { method: "POST", body: { scope: "all", confirm: true, include_conversation_memory: wipeConvs } });
      onNotice(`Deleted ${r.deleted} memories${wipeConvs ? " and all conversation summaries" : ""}.`);
      setConfirmText("");
      onChanged();
    } catch (e) { onError(e); }
  }

  const off = useMemo(() => !s?.memory_enabled, [s]);
  if (!s) return <Spinner />;
  return (
    <div className="space-y-5">
      <section className="space-y-4">
        <Toggle label="Memory" hint="Master switch. When off, Aria doesn't read or add long-term memories (you can still view and manage them)."
          checked={s.memory_enabled} onChange={(v) => save({ memory_enabled: v })} />
        <Toggle label="Use memories in chats" hint="Include relevant memories in Aria's context when answering."
          checked={s.use_in_chat} disabled={off} onChange={(v) => save({ use_in_chat: v })} />
        <Toggle label="Learn from conversations" hint="Let Aria notice durable facts about you and suggest saving them."
          checked={s.auto_extract} disabled={off} onChange={(v) => save({ auto_extract: v })} />
        <Toggle label="Ask before saving" hint="Learned facts wait in Suggestions until you approve them. Sensitive or uncertain ones always ask."
          checked={s.require_approval} disabled={off || !s.auto_extract} onChange={(v) => save({ require_approval: v })} />
        <Toggle label="Allow sensitive topics" hint="Health, beliefs, sexuality, politics, finances and legal matters can be suggested (always with approval). Passwords, keys and card or ID numbers are never stored."
          checked={s.allow_sensitive} disabled={off || !s.auto_extract} onChange={(v) => save({ allow_sensitive: v })} />
        <Toggle label="Keep source quotes" hint="Store the (redacted) words a memory was learned from, so you can check it."
          checked={s.store_source_excerpts} onChange={(v) => save({ store_source_excerpts: v })} />
      </section>
      <section className="space-y-4 border-t pt-4">
        <Toggle label="Summarise long chats" hint="Condense older messages into a summary and searchable sections. When off, older messages are simply dropped from context and nothing is stored."
          checked={s.summarize_conversations} onChange={(v) => save({ summarize_conversations: v })} />
        <Toggle label="Recall across chats" hint="Let Aria look up summaries of your other chats (except ones with memory turned off)."
          checked={s.cross_conversation_recall} disabled={off} onChange={(v) => save({ cross_conversation_recall: v })} />
      </section>
      <section className="grid gap-4 border-t pt-4 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="font-medium">Forget unused memories after</span>
          <select value={s.retention_days ?? 0} onChange={(e) => save({ retention_days: Number(e.target.value) || null })} className={cn(selectCls, "w-full")}>
            <option value={0}>Never</option>
            {[30, 90, 180, 365, 730].map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
          <span className="block text-xs text-muted-foreground">Pinned memories are kept.</span>
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Discard unreviewed suggestions after</span>
          <select value={s.pending_expiry_days} onChange={(e) => save({ pending_expiry_days: Number(e.target.value) })} className={cn(selectCls, "w-full")}>
            {[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
        </label>
      </section>
      <section className="space-y-2 rounded-lg border border-destructive/30 p-3">
        <h3 className="text-sm font-medium text-destructive">Delete everything</h3>
        <p className="text-xs text-muted-foreground">Permanently deletes all memories, suggestions and their history. Download an export first if you want a copy.</p>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={wipeConvs} onChange={(e) => setWipeConvs(e.target.checked)} />
          Also delete summaries and working state of all chats
        </label>
        <div className="flex gap-2">
          <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder='Type "delete" to confirm' className="max-w-[14rem]" />
          <Button variant="destructive" disabled={confirmText.trim().toLowerCase() !== "delete"} onClick={wipe}><Trash2 /> Delete all</Button>
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- activity
const ACTION_TEXT: Record<string, string> = {
  created: "Memory saved", approved: "Suggestion approved", rejected: "Suggestion dismissed", edited: "Memory edited",
  deleted: "Memory deleted", deleted_bulk: "Memories deleted in bulk", archived: "Memory archived (superseded)",
  expired: "Expired automatically", exported: "Memories exported", imported: "Memories imported",
  settings_changed: "Privacy settings changed", summary_edited: "Chat summary corrected",
  conversation_memory_cleared: "Chat summary cleared",
};

function ActivityView({ onError }: { onError: (e: unknown) => void }) {
  const [items, setItems] = useState<AuditEntry[] | null>(null);
  useEffect(() => { api<AuditEntry[]>("/api/memories/audit?limit=200").then(setItems).catch(onError); }, [onError]);
  if (items === null) return <Spinner />;
  if (!items.length) return <Empty>No memory activity yet.</Empty>;
  return (
    <ol className="space-y-1 text-sm">
      {items.map((e) => {
        const d = e.detail as Record<string, any>;
        const extra = e.action === "created" ? `${d.source} · ${d.status}` : e.action === "settings_changed"
          ? Object.entries(d.changed ?? {}).map(([k, v]) => `${k} → ${v}`).join(", ")
          : e.action === "deleted_bulk" ? `${d.count} (${d.scope})` : e.action === "edited" ? (d.fields ?? []).join(", ")
          : e.action === "imported" ? `${d.imported} imported` : e.action === "exported" ? `${d.memories} memories` : "";
        return (
          <li key={e.id} className="flex gap-3 rounded px-2 py-1 hover:bg-accent/50">
            <span className="w-28 shrink-0 text-xs text-muted-foreground">{new Date(e.created_at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}</span>
            <span>{ACTION_TEXT[e.action] ?? e.action}{extra && <span className="text-muted-foreground"> · {extra}</span>}</span>
          </li>
        );
      })}
    </ol>
  );
}
