"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Cpu, Download, KeyRound, Loader2, RefreshCw, Search } from "lucide-react";
import { api } from "@/lib/api";
import { createSSEParser } from "@/lib/sse";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type SetupStatus = {
  configured: boolean;
  default_model: string | null;
  web_search: boolean;
  can_edit: boolean;
  providers: { name: string; connected: boolean; hint: string | null; source: "app" | "env" | null }[];
  local?: LocalStatus;
};

type LocalStatus = { reachable: boolean; url: string | null; models: string[]; download_url: string;
  recommended: { name: string; size: string; note: string }[] };

/** Free local AI: no API key. Uses Ollama running on this computer; downloads a model with one click. */
function LocalAI({ initial, onConnected }: { initial?: LocalStatus; onConnected: () => void }) {
  const [st, setSt] = useState<LocalStatus | undefined>(initial);
  const [checking, setChecking] = useState(false);
  const [pick, setPick] = useState(initial?.recommended[0]?.name ?? "llama3.2:3b");
  const [progress, setProgress] = useState<{ status: string; percent: number | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    setChecking(true); setError(null);
    try {
      const s = await api<LocalStatus>("/api/setup/local");
      setSt(s);
      if (s.models.length) onConnected();
    } catch (e: any) { setError(e.message); } finally { setChecking(false); }
  }

  async function download() {
    setError(null); setProgress({ status: "starting", percent: null });
    try {
      const res = await fetch("/api/setup/local/pull", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "aria" }, body: JSON.stringify({ model: pick }) });
      if (!res.ok || !res.body) throw new Error((await res.json().catch(() => ({}))).detail || "Download failed");
      let failed: string | null = null;
      const parser = createSSEParser(({ data }) => {
        const ev = JSON.parse(data);
        if (ev.type === "progress") setProgress({ status: ev.status, percent: ev.percent });
        if (ev.type === "error") failed = ev.message;
      });
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) { const { value, done } = await reader.read(); if (done) break; parser.feed(value); }
      if (failed) throw new Error(failed);
      setProgress(null);
      await check();
    } catch (e: any) { setError(e.message); setProgress(null); }
  }

  if (st?.models.length) return null;
  return (
    <div className="space-y-2 rounded-xl border border-brand/30 bg-brand/5 p-4">
      <div className="flex items-center gap-2 font-medium"><Cpu className="h-4 w-4 text-brand" /> Free AI on this computer — no API key</div>
      {!st?.reachable ? (
        <>
          <p className="text-sm text-muted-foreground">Install <b>Ollama</b> (free) and Aria connects to it by itself. Chats stay on your PC.
            You need about 8 GB of RAM; answers are slower than paid models.</p>
          <div className="flex flex-wrap gap-2">
            <Button asChild type="button"><a href={st?.download_url ?? "https://ollama.com/download"} target="_blank" rel="noreferrer"><Download /> Download Ollama</a></Button>
            <Button type="button" variant="outline" onClick={check} disabled={checking}>{checking ? <Loader2 className="animate-spin" /> : <RefreshCw />} I installed it — check again</Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Ollama is running. Pick a model to download once (it then works offline):</p>
          <div className="space-y-1">
            {st.recommended.map((m) => (
              <label key={m.name} className="flex items-start gap-2 text-sm">
                <input type="radio" className="mt-1" checked={pick === m.name} onChange={() => setPick(m.name)} disabled={!!progress} />
                <span><span className="font-mono">{m.name}</span> · {m.size} — <span className="text-muted-foreground">{m.note}</span></span>
              </label>
            ))}
          </div>
          {progress ? (
            <div className="space-y-1">
              <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-brand transition-all" style={{ width: `${progress.percent ?? 2}%` }} /></div>
              <p className="text-xs text-muted-foreground">{progress.status}{progress.percent != null ? ` — ${progress.percent}%` : ""}</p>
            </div>
          ) : <Button type="button" onClick={download}><Download /> Download and use</Button>}
        </>
      )}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}

/** Shown on the chat screen until an AI model is connected: paste a key, Aria checks it and uses it right away. */
export function ConnectModel({ onConnected, onClose }: { onConnected: () => void; onClose: () => void }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => { api<SetupStatus>("/api/setup").then(setStatus).catch(() => {}); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      const r = await api<SetupStatus & { message: string; provider: string }>("/api/setup/key", { method: "POST", body: { key } });
      setKey(""); setStatus(r); setDone(r.message);
      if (r.configured) onConnected();
    } catch (err: any) {
      setError(err.message || "Couldn't save the key.");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  if (!status.can_edit) {
    return (
      <div className="rounded-2xl border bg-card/60 p-5 text-sm text-muted-foreground">
        No AI model is connected yet. Ask the person who set up Aria to open it and paste an API key on the chat screen.
      </div>
    );
  }
  const modelReady = status.configured;
  return (
    <form onSubmit={submit} className="space-y-3 rounded-2xl border bg-card/60 p-5 text-left shadow-sm animate-fade-up">
      <div className="flex items-center gap-2 font-medium">
        {modelReady ? <Search className="h-4 w-4" /> : <KeyRound className="h-4 w-4" />}
        {modelReady ? "Optional: turn on web search" : "Connect an AI model for full answers"}
      </div>
      {!modelReady && <LocalAI initial={status.local} onConnected={() => api<SetupStatus>("/api/setup").then((r) => { setStatus(r); if (r.configured) onConnected(); })} />}
      {!modelReady && <div className="pt-1 text-sm font-medium">Or use a paid model with an API key (faster, smarter)</div>}
      <p className="text-sm text-muted-foreground">
        {modelReady ? (
          <>Paste a Tavily key (starts with <code>tvly-</code>, free at tavily.com) to let Aria search the web and run Deep research.</>
        ) : (
          <>Paste your API key below. Anthropic keys start with <code>sk-ant-</code> (console.anthropic.com), OpenAI keys
            with <code>sk-</code> (platform.openai.com).</>
        )}
      </p>
      <div className="flex gap-2">
        <Input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)}
          placeholder={modelReady ? "tvly-..." : "sk-ant-... or sk-..."} aria-label="API key" />
        <Button type="submit" disabled={busy || !key.trim()}>
          {busy ? <Loader2 className="animate-spin" /> : null}{busy ? "Checking" : "Connect"}
        </Button>
      </div>
      {done && <p className="flex items-center gap-1.5 text-sm text-emerald-700 dark:text-emerald-400"><CheckCircle2 className="h-4 w-4" />{done}</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {modelReady && (
        <button type="button" onClick={onClose} className="text-xs text-muted-foreground underline">
          {status.web_search ? "Done" : "Skip for now"}
        </button>
      )}
      <p className="text-xs text-muted-foreground">The key is checked with the provider, then stored only in Aria's database on this computer.
        It is never shown again in full.</p>
    </form>
  );
}
