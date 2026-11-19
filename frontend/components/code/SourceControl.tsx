"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, GitBranch, GitCommitHorizontal, Loader2, Plus, RotateCcw, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { confirmFetch, wsApi, type Commit, type GitStatus } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { STATUS_COLOR } from "./FileExplorer";
import type { ConfirmOptions } from "./ConfirmDialog";

type Ask = (o: ConfirmOptions) => Promise<false | { value: string }>;

export function SourceControl({ wsId, status, refresh, onOpenDiff, onShowCommit, ask, onError }: {
  wsId: string; status: GitStatus | null; refresh: () => Promise<void>; onOpenDiff: (path?: string) => void;
  onShowCommit: (c: Commit) => void; ask: Ask; onError: (m: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [log, setLog] = useState<Commit[]>([]);
  const [branches, setBranches] = useState<{ current: string; branches: string[] } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const loadMeta = useCallback(async () => {
    try {
      const [l, b] = await Promise.all([api<Commit[]>(`${wsApi(wsId)}/git/log?limit=30`), api<any>(`${wsApi(wsId)}/git/branches`)]);
      setLog(l); setBranches(b);
    } catch (e: any) { onError(e.message); }
  }, [wsId, onError]);
  useEffect(() => { loadMeta(); }, [loadMeta, status]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try { await fn(); await refresh(); await loadMeta(); } catch (e: any) { onError(e.message); } finally { setBusy(null); }
  }

  const commit = () => act("commit", async () => {
    await api(`${wsApi(wsId)}/git/commit`, { method: "POST", body: { message } });
    setMessage("");
  });

  const discard = async (paths: string[]) => {
    const ok = await ask({ title: `Discard changes in ${paths.length === 1 ? paths[0] : `${paths.length} files`}?`, destructive: true,
      body: "Uncommitted edits are reverted to the last commit and new files are deleted. A checkpoint is saved first, so you can still restore it from the AI panel's history.", confirmLabel: "Discard" });
    if (ok) act("discard", () => confirmFetch(`${wsApi(wsId)}/git/discard`, { paths }));
  };

  const newBranch = async () => {
    const r = await ask({ title: "Create branch", input: { label: "Branch name", placeholder: "feature/my-change", required: true }, confirmLabel: "Create" });
    if (r) act("branch", () => api(`${wsApi(wsId)}/git/checkout`, { method: "POST", body: { name: r.value.trim(), create: true } }));
  };

  const push = async () => {
    let remote = status?.remote;
    if (!remote) {
      const r = await ask({ title: "Set remote", input: { label: "HTTPS repository URL", placeholder: "https://github.com/you/repo.git", required: true }, confirmLabel: "Save remote" });
      if (!r) return;
      try { await api(`${wsApi(wsId)}/git/remote`, { method: "PUT", body: { url: r.value.trim() } }); remote = r.value.trim(); }
      catch (e: any) { return onError(e.message); }
    }
    const r = await ask({
      title: "Publish changes?", confirmLabel: "Push", destructive: true,
      body: <>This pushes branch <b>{status?.branch}</b>{status?.ahead ? ` (${status.ahead} commit${status.ahead === 1 ? "" : "s"} ahead)` : ""} to <span className="break-all font-mono">{remote}</span>. Uncommitted changes are not included.</>,
      input: { label: "Access token (used for this push only, never stored)", type: "password", placeholder: "Leave empty for public remotes that allow it" },
    });
    if (r) act("push", () => confirmFetch(`${wsApi(wsId)}/git/push`, { token: r.value || null }));
  };

  if (!status) return <Loader2 className="mx-auto my-6 h-4 w-4 animate-spin text-muted-foreground" />;
  return (
    <div className="flex h-full flex-col text-[13px]">
      <div className="flex h-8 shrink-0 items-center justify-between px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Source control
        <button title="Push" onClick={push} className="rounded p-1 hover:bg-accent"><Upload className="h-3.5 w-3.5" /></button>
      </div>
      <div className="thin-scroll min-h-0 flex-1 space-y-3 overflow-y-auto p-2">
        <div className="flex items-center gap-1">
          <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
          <select value={branches?.current ?? status.branch} disabled={!!busy}
            onChange={(e) => act("checkout", () => api(`${wsApi(wsId)}/git/checkout`, { method: "POST", body: { name: e.target.value } }))}
            className="h-7 min-w-0 flex-1 rounded-md border border-input bg-background px-1 text-xs">
            {(branches?.branches.length ? branches.branches : [status.branch]).map((b) => <option key={b}>{b}</option>)}
          </select>
          <button title="New branch" onClick={newBranch} className="rounded p-1 hover:bg-accent"><Plus className="h-3.5 w-3.5" /></button>
        </div>
        {status.remote && <p className="truncate text-xs text-muted-foreground" title={status.remote}>origin: {status.remote}</p>}
        <div className="space-y-1.5">
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={2} placeholder="Commit message"
            onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && message.trim()) commit(); }}
            className="w-full resize-none rounded-md border border-input bg-background px-2 py-1.5 text-sm" />
          <Button size="sm" className="w-full" disabled={!message.trim() || status.clean || !!busy} onClick={commit}>
            {busy === "commit" ? <Loader2 className="animate-spin" /> : <Check />} Commit {status.files.length ? `${status.files.length} file${status.files.length === 1 ? "" : "s"}` : ""}
          </Button>
        </div>
        <div>
          <div className="flex items-center justify-between py-1 text-xs font-medium text-muted-foreground">
            <span>Changes ({status.files.length})</span>
            {!status.clean && (
              <span className="flex gap-2">
                <button className="underline" onClick={() => onOpenDiff()}>View all</button>
                <button className="underline" onClick={() => discard(status.files.map((f) => f.path))}>Discard all</button>
              </span>
            )}
          </div>
          {status.clean && <p className="text-xs text-muted-foreground">No uncommitted changes.</p>}
          {status.files.map((f) => (
            <div key={f.path} className="group flex h-7 cursor-pointer items-center gap-1.5 rounded px-1 hover:bg-accent" onClick={() => onOpenDiff(f.path)}>
              <span className={cn("min-w-0 flex-1 truncate", STATUS_COLOR[f.status])} title={f.path}>{f.path}</span>
              <span className="shrink-0 font-mono text-[11px]"><span className="text-emerald-600">+{f.added}</span> <span className="text-red-600">−{f.removed}</span></span>
              <button title="Discard changes" onClick={(e) => { e.stopPropagation(); discard([f.path]); }} className="hidden rounded p-0.5 hover:bg-background group-hover:block">
                <RotateCcw className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
        <div>
          <p className="py-1 text-xs font-medium text-muted-foreground">History</p>
          {log.map((c) => (
            <button key={c.sha} onClick={() => onShowCommit(c)} className="flex w-full items-start gap-1.5 rounded px-1 py-1 text-left hover:bg-accent">
              <GitCommitHorizontal className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{c.message}</span>
                <span className="block text-[11px] text-muted-foreground">{c.short} · {c.author} · {new Date(c.date).toLocaleDateString()}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
