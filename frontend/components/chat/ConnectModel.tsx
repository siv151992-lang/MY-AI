"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, KeyRound, Loader2, Search } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type SetupStatus = {
  configured: boolean;
  default_model: string | null;
  web_search: boolean;
  can_edit: boolean;
  providers: { name: string; connected: boolean; hint: string | null; source: "app" | "env" | null }[];
};

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
        {modelReady ? "Optional: turn on web search" : "Connect an AI model to start chatting"}
      </div>
      <p className="text-sm text-muted-foreground">
        {modelReady ? (
          <>Paste a Tavily key (starts with <code>tvly-</code>, free at tavily.com) to let Aria search the web and run Deep research.</>
        ) : (
          <>Paste your API key below. Anthropic keys start with <code>sk-ant-</code> (console.anthropic.com), OpenAI keys
            with <code>sk-</code> (platform.openai.com). Or type <code>ollama</code> to use free local models.</>
        )}
      </p>
      <div className="flex gap-2">
        <Input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)}
          placeholder={modelReady ? "tvly-..." : "sk-ant-... or sk-... or ollama"} aria-label="API key" autoFocus />
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
