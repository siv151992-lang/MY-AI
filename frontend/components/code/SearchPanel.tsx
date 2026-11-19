"use client";

import { useState } from "react";
import { Loader2, Search } from "lucide-react";
import { api } from "@/lib/api";
import { wsApi } from "@/lib/workspace";

type Hit = { path: string; line: number; text: string };
type Rel = { path: string; score: number; matches: string[] };

export function SearchPanel({ wsId, onOpen }: { wsId: string; onOpen: (path: string, line?: number) => void }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"text" | "regex" | "relevant">("text");
  const [res, setRes] = useState<{ hits?: Hit[]; files?: Rel[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!q.trim()) return;
    setBusy(true); setError(null);
    try { setRes(await api(`${wsApi(wsId)}/search?${new URLSearchParams({ q, mode })}`)); }
    catch (e: any) { setError(e.message); setRes(null); }
    finally { setBusy(false); }
  }

  const grouped = (res?.hits ?? []).reduce<Record<string, Hit[]>>((a, h) => { (a[h.path] ??= []).push(h); return a; }, {});
  return (
    <div className="flex h-full flex-col">
      <div className="h-8 shrink-0 px-2 pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Search</div>
      <form className="space-y-1.5 p-2" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div className="relative">
          <Search className="absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={mode === "relevant" ? "Where is login handled?" : "Search code"}
            className="h-8 w-full rounded-md border border-input bg-background pl-7 pr-2 text-sm" />
        </div>
        <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} className="h-7 w-full rounded-md border border-input bg-background px-1 text-xs">
          <option value="text">Text</option><option value="regex">Regex</option><option value="relevant">Natural language (ranked files)</option>
        </select>
      </form>
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto px-1 pb-4 text-xs">
        {busy && <Loader2 className="mx-auto my-4 h-4 w-4 animate-spin text-muted-foreground" />}
        {error && <p className="p-2 text-destructive">{error}</p>}
        {res?.files && (res.files.length ? res.files.map((f) => (
          <button key={f.path} onClick={() => onOpen(f.path)} className="block w-full rounded px-2 py-1 text-left hover:bg-accent">
            <span className="font-medium">{f.path}</span>
            {f.matches.map((m, i) => <span key={i} className="block truncate font-mono text-muted-foreground">{m}</span>)}
          </button>
        )) : <p className="p-2 text-muted-foreground">No relevant files.</p>)}
        {res?.hits && (Object.keys(grouped).length ? Object.entries(grouped).map(([path, hits]) => (
          <div key={path} className="mb-1">
            <p className="truncate px-2 py-0.5 font-medium">{path} <span className="text-muted-foreground">({hits.length})</span></p>
            {hits.map((h) => (
              <button key={h.line} onClick={() => onOpen(h.path, h.line)} className="flex w-full gap-2 rounded px-2 py-0.5 text-left font-mono hover:bg-accent">
                <span className="w-8 shrink-0 text-right text-muted-foreground">{h.line}</span><span className="truncate">{h.text}</span>
              </button>
            ))}
          </div>
        )) : <p className="p-2 text-muted-foreground">No matches.</p>)}
      </div>
    </div>
  );
}
