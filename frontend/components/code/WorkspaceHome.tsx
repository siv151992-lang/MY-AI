"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Code2, FolderGit2, Loader2, Plus, Trash2, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { uploadZip, type Workspace } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirm } from "./ConfirmDialog";

export function WorkspaceHome() {
  const router = useRouter();
  const [items, setItems] = useState<Workspace[] | null>(null);
  const [mode, setMode] = useState<"blank" | "clone" | "zip">("blank");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  useEffect(() => { const p = new URLSearchParams(window.location.search).get("prompt"); if (p) { setDescription(p.slice(0, 500)); setName(p.split(/\s+/).slice(0, 4).join(" ").slice(0, 60)); } }, []);
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api<Workspace[]>("/api/workspaces").then(setItems).catch((e) => setError(e.message)); }, []);

  async function create() {
    setBusy(true); setError(null);
    try {
      const n = name.trim() || (mode === "clone" ? url.split("/").pop()?.replace(/\.git$/, "") : file?.name.replace(/\.zip$/, "")) || "Project";
      const ws = mode === "zip" && file ? await uploadZip(file, n)
        : await api<Workspace>("/api/workspaces", { method: "POST", body: { name: n, description, clone_url: mode === "clone" ? url.trim() : null } });
      router.push(`/code/${ws.id}`);
    } catch (e: any) { setError(e.message); setBusy(false); }
  }

  async function remove(w: Workspace) {
    if (!(await ask({ title: `Delete “${w.name}”?`, destructive: true, confirmLabel: "Delete workspace",
      body: "All files, git history and the workspace's AI conversations are deleted permanently. Export it first if you want a copy." }))) return;
    try {
      await api(`/api/workspaces/${w.id}?confirm=true`, { method: "DELETE" });
      setItems((xs) => xs?.filter((x) => x.id !== w.id) ?? null);
    } catch (e: any) { setError(e.message); }
  }

  const ready = mode === "blank" ? !!name.trim() : mode === "clone" ? url.startsWith("https://") : !!file;
  return (
    <div className="mx-auto w-full max-w-4xl space-y-8 px-4 py-8">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon-sm" asChild title="Back to chat"><Link href="/chat"><ArrowLeft /></Link></Button>
        <Code2 className="h-6 w-6 text-brand" />
        <h1 className="font-serif text-2xl">Code workspaces</h1>
      </div>
      <section className="space-y-3 rounded-2xl border bg-card/50 p-4">
        <div className="flex gap-1">
          {([["blank", "New project", Plus], ["clone", "Clone from Git", FolderGit2], ["zip", "Upload .zip", Upload]] as const).map(([id, label, Icon]) => (
            <button key={id} onClick={() => setMode(id)} className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm", mode === id ? "bg-secondary font-medium" : "text-muted-foreground hover:bg-accent")}>
              <Icon className="h-4 w-4" /> {label}
            </button>
          ))}
        </div>
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (ready) create(); }}>
          {mode === "clone" && <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo.git (public HTTPS repositories)" />}
          {mode === "zip" && (
            <div className="flex items-center gap-2">
              <Button type="button" variant="outline" onClick={() => fileRef.current?.click()}><Upload /> Choose .zip</Button>
              <span className="text-sm text-muted-foreground">{file?.name ?? "No file selected (max 50 MB)"}</span>
              <input ref={fileRef} type="file" accept=".zip,application/zip" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </div>
          )}
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={mode === "blank" ? "Project name" : "Name (optional)"} maxLength={100} />
          {mode === "blank" && <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What are you building? (optional)" maxLength={1000} />}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={!ready || busy}>{busy ? <Loader2 className="animate-spin" /> : <Plus />} Create workspace</Button>
        </form>
      </section>
      <section className="space-y-2">
        {items === null ? <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" /> : items.length === 0 ? (
          <p className="text-center text-sm text-muted-foreground">No workspaces yet.</p>
        ) : items.map((w) => (
          <div key={w.id} className="group flex items-center gap-3 rounded-xl border px-4 py-3 hover:bg-accent/40">
            <FolderGit2 className="h-5 w-5 shrink-0 text-brand" />
            <Link href={`/code/${w.id}`} className="min-w-0 flex-1">
              <p className="truncate font-medium">{w.name}</p>
              <p className="truncate text-xs text-muted-foreground">{w.description || w.source} · updated {new Date(w.updated_at).toLocaleString()}</p>
            </Link>
            <Button variant="ghost" size="icon-sm" title="Delete workspace" className="opacity-0 group-hover:opacity-100" onClick={() => remove(w)}><Trash2 /></Button>
          </div>
        ))}
      </section>
      {confirmEl}
    </div>
  );
}
