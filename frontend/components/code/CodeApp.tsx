"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bot, Download, Files, GitBranch, Loader2, PanelBottom, Save, Search, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { ModelInfo } from "@/lib/types";
import { confirmFetch, wsApi, type Commit, type EditorContext, type FileStatus, type GitStatus, type TreeEntry, type Workspace } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ChatPanel, type ChatPanelHandle } from "./ChatPanel";
import { CodeEditor, type Selection } from "./CodeEditor";
import { useConfirm } from "./ConfirmDialog";
import { DiffView } from "./DiffView";
import { FileExplorer } from "./FileExplorer";
import { SearchPanel } from "./SearchPanel";
import { SourceControl } from "./SourceControl";
import { Terminal, type TerminalHandle } from "./Terminal";

type FileTab = { kind: "file"; id: string; path: string; content: string; saved: string; binary: boolean; stale?: boolean };
type DiffTab = { kind: "diff"; id: string; title: string; diff: string; header?: string };
type Tab = FileTab | DiffTab;
type Conv = { id: string; title: string; updated_at: string };

export function CodeApp({ wsId }: { wsId: string }) {
  const [ws, setWs] = useState<(Workspace & { conversations: Conv[] }) | null>(null);
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [changes, setChanges] = useState<Record<string, FileStatus>>({});
  const [git, setGit] = useState<GitStatus | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [left, setLeft] = useState<"files" | "search" | "git">("files");
  const [showTerm, setShowTerm] = useState(true);
  const [showChat, setShowChat] = useState(true);
  const [selection, setSelection] = useState<Selection>(null);
  const [jump, setJump] = useState<{ line: number; n: number } | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [chatWidth, setChatWidth] = useState(420);
  const [termHeight, setTermHeight] = useState(230);
  const [confirmEl, ask] = useConfirm();
  const term = useRef<TerminalHandle>(null);
  const chat = useRef<ChatPanelHandle>(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;

  const fail = useCallback((m: string) => setBanner(m), []);

  const refresh = useCallback(async () => {
    try {
      const [t, g] = await Promise.all([
        api<{ entries: TreeEntry[]; changes: Record<string, FileStatus> }>(`${wsApi(wsId)}/tree`),
        api<GitStatus>(`${wsApi(wsId)}/git/status`),
      ]);
      setEntries(t.entries); setChanges(t.changes); setGit(g);
    } catch (e: any) { fail(e.message); }
  }, [wsId, fail]);

  const loadWs = useCallback(async () => {
    try { setWs(await api(`${wsApi(wsId)}`)); } catch (e: any) { fail(e.status === 404 ? "Workspace not found." : e.message); }
  }, [wsId, fail]);

  useEffect(() => {
    loadWs(); refresh();
    api<{ default: string | null; models: ModelInfo[] }>("/api/models").then((m) => {
      setModels(m.models);
      const saved = localStorage.getItem("aria.model");
      setModelId(saved && m.models.some((x) => x.id === saved) ? saved : m.default);
    }).catch(() => {});
  }, [loadWs, refresh]);

  const current = tabs.find((t) => t.id === active) ?? null;
  const dirs = useMemo(() => new Set(entries.filter((e) => e.type === "dir").map((e) => e.path)), [entries]);

  // ---------- tabs ----------
  const upsert = (tab: Tab) => {
    setTabs((ts) => (ts.some((t) => t.id === tab.id) ? ts.map((t) => (t.id === tab.id ? tab : t)) : [...ts, tab]));
    setActive(tab.id);
  };

  const openFile = useCallback(async (path: string, line?: number) => {
    const existing = tabsRef.current.find((t) => t.kind === "file" && t.path === path);
    if (existing) { setActive(existing.id); if (line) setJump({ line, n: Date.now() }); return; }
    try {
      const f = await api<{ path: string; binary: boolean; content: string | null }>(`${wsApi(wsId)}/file?path=${encodeURIComponent(path)}`);
      upsert({ kind: "file", id: `f:${path}`, path, content: f.content ?? "", saved: f.content ?? "", binary: f.binary });
      if (line) setTimeout(() => setJump({ line, n: Date.now() }), 50);
    } catch (e: any) { fail(e.message); }
  }, [wsId, fail]);

  const closeTab = async (id: string) => {
    const t = tabsRef.current.find((x) => x.id === id);
    if (t?.kind === "file" && t.content !== t.saved && !(await ask({ title: `Close ${t.path} without saving?`, destructive: true, confirmLabel: "Discard edits" }))) return;
    setTabs((ts) => {
      const rest = ts.filter((x) => x.id !== id);
      if (active === id) setActive(rest.length ? rest[rest.length - 1].id : null);
      return rest;
    });
  };

  async function save(tab: FileTab) {
    try {
      await api(`${wsApi(wsId)}/file`, { method: "PUT", body: { path: tab.path, content: tab.content, expected: tab.saved } });
      setTabs((ts) => ts.map((t) => (t.id === tab.id ? { ...tab, saved: tab.content, stale: false } : t)));
      refresh();
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        const r = await ask({ title: "File changed on disk", confirmLabel: "Overwrite with my version", destructive: true,
          body: `${tab.path} was changed (probably by the AI) after you opened it. Overwrite it with your version, or cancel and reload the tab to see the new content.` });
        if (r) {
          await api(`${wsApi(wsId)}/file`, { method: "PUT", body: { path: tab.path, content: tab.content } }).catch((x) => fail(x.message));
          setTabs((ts) => ts.map((t) => (t.id === tab.id ? { ...tab, saved: tab.content, stale: false } : t)));
          refresh();
        }
      } else fail(e.message);
    }
  }

  const reloadTab = async (tab: FileTab) => {
    try {
      const f = await api<{ content: string | null; binary: boolean }>(`${wsApi(wsId)}/file?path=${encodeURIComponent(tab.path)}`);
      setTabs((ts) => ts.map((t) => (t.id === tab.id ? { ...tab, content: f.content ?? "", saved: f.content ?? "", binary: f.binary, stale: false } : t)));
    } catch {
      setTabs((ts) => ts.filter((t) => t.id !== tab.id));  // deleted
    }
  };

  // The AI (or a command) changed files: refresh the tree and update open tabs.
  const onWorkspaceChange = useCallback(async (paths: string[]) => {
    await refresh();
    for (const t of tabsRef.current) {
      if (t.kind !== "file" || (paths.length && !paths.includes(t.path))) continue;
      if (t.content === t.saved) await reloadTab(t);
      else setTabs((ts) => ts.map((x) => (x.id === t.id ? { ...t, stale: true } : x)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  const openDiff = async (path?: string) => {
    try {
      const { diff } = await api<{ diff: string }>(`${wsApi(wsId)}/git/diff${path ? `?path=${encodeURIComponent(path)}` : ""}`);
      upsert({ kind: "diff", id: `d:${path ?? "*"}`, title: path ? `Δ ${path.split("/").pop()}` : "Δ All changes", diff });
    } catch (e: any) { fail(e.message); }
  };

  const openTurnDiff = async (sha: string, path?: string) => {
    try {
      const r = await api<{ diff: string }>(`${wsApi(wsId)}/checkpoints/${sha}/changes`);
      let diff = r.diff;
      if (path) diff = diff.split(/(?=^diff --git )/m).filter((c) => c.includes(` b/${path}\n`) || c.includes(` a/${path} `)).join("");
      upsert({ kind: "diff", id: `t:${sha}:${path ?? "*"}`, title: `AI turn${path ? ` · ${path.split("/").pop()}` : ""}`, diff,
        header: "Changes since the start of this AI turn (includes any edits made afterwards)." });
    } catch (e: any) { fail(e.message); }
  };

  const showCommit = async (c: Commit) => {
    try {
      const { text } = await api<{ text: string }>(`${wsApi(wsId)}/git/show/${c.sha}`);
      upsert({ kind: "diff", id: `c:${c.sha}`, title: c.short, diff: text, header: `${c.message} — ${c.author}, ${new Date(c.date).toLocaleString()}` });
    } catch (e: any) { fail(e.message); }
  };

  // ---------- explorer actions ----------
  const create = async (dir: string, type: "file" | "dir") => {
    const r = await ask({ title: type === "file" ? "New file" : "New folder", confirmLabel: "Create",
      input: { label: "Path", initial: dir ? dir + "/" : "", placeholder: type === "file" ? "src/main.py" : "src/utils", required: true } });
    if (!r) return;
    const path = r.value.trim().replace(/^\/+/, "");
    try {
      if (type === "file") { await api(`${wsApi(wsId)}/file`, { method: "PUT", body: { path, content: "" } }); await refresh(); openFile(path); }
      else { await api(`${wsApi(wsId)}/mkdir`, { method: "POST", body: { path } }); refresh(); }
    } catch (e: any) { fail(e.message); }
  };

  const rename = async (path: string) => {
    const r = await ask({ title: `Rename or move ${path}`, confirmLabel: "Move", input: { label: "New path", initial: path, required: true } });
    if (!r || r.value.trim() === path) return;
    try {
      await api(`${wsApi(wsId)}/move`, { method: "POST", body: { from: path, to: r.value.trim() } });
      setTabs((ts) => ts.filter((t) => !(t.kind === "file" && (t.path === path || t.path.startsWith(path + "/")))));
      refresh();
    } catch (e: any) { fail(e.message); }
  };

  const remove = async (path: string, type: "file" | "dir") => {
    const n = type === "dir" ? entries.filter((e) => e.type === "file" && e.path.startsWith(path + "/")).length : 1;
    const ok = await ask({ title: `Delete ${path}?`, destructive: true, confirmLabel: "Delete",
      body: type === "dir" ? `This deletes the folder and ${n} file${n === 1 ? "" : "s"} in it. Committed files can be restored from git; uncommitted ones are lost.` : "Committed versions can be restored from git; uncommitted changes are lost." });
    if (!ok) return;
    try {
      await confirmFetch(`${wsApi(wsId)}/delete`, { path });
      setTabs((ts) => ts.filter((t) => !(t.kind === "file" && (t.path === path || t.path.startsWith(path + "/")))));
      refresh();
    } catch (e: any) { fail(e.message); }
  };

  // ---------- panel resizing ----------
  const drag = (axis: "x" | "y") => (e: React.MouseEvent) => {
    e.preventDefault();
    const start = axis === "x" ? e.clientX : e.clientY;
    const init = axis === "x" ? chatWidth : termHeight;
    const move = (ev: MouseEvent) => {
      if (axis === "x") setChatWidth(Math.min(800, Math.max(300, init + start - ev.clientX)));
      else setTermHeight(Math.min(600, Math.max(120, init + start - ev.clientY)));
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const editorCtx: EditorContext | null = current?.kind === "file" && !current.binary
    ? { path: current.path, ...(selection ? { selection: selection.text, start_line: selection.startLine, end_line: selection.endLine } : {}) }
    : null;

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="flex h-10 shrink-0 items-center gap-2 border-b px-2 text-sm">
        <Button variant="ghost" size="icon-sm" asChild title="All workspaces"><Link href="/code"><ArrowLeft /></Link></Button>
        <span className="truncate font-medium">{ws?.name ?? "…"}</span>
        {git && <span className="flex items-center gap-1 text-xs text-muted-foreground"><GitBranch className="h-3.5 w-3.5" />{git.branch}{git.files.length ? ` · ${git.files.length} changed` : ""}</span>}
        <span className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" asChild title="Download the project as .zip"><a href={`${wsApi(wsId)}/export`} download><Download /> Export</a></Button>
          <Button variant={showTerm ? "secondary" : "ghost"} size="icon-sm" title="Toggle terminal" onClick={() => setShowTerm((v) => !v)}><PanelBottom /></Button>
          <Button variant={showChat ? "secondary" : "ghost"} size="icon-sm" title="Toggle AI panel" onClick={() => setShowChat((v) => !v)}><Bot /></Button>
        </span>
      </header>
      {banner && (
        <div className="flex items-center gap-2 border-b bg-destructive/10 px-3 py-1.5 text-sm text-destructive">
          <span className="flex-1">{banner}</span><button onClick={() => setBanner(null)}><X className="h-4 w-4" /></button>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        {/* activity bar + side panel */}
        <nav className="flex w-11 shrink-0 flex-col items-center gap-1 border-r py-2">
          {([["files", Files, "Explorer"], ["search", Search, "Search"], ["git", GitBranch, "Source control"]] as const).map(([id, Icon, label]) => (
            <button key={id} title={label} onClick={() => setLeft(id)}
              className={cn("relative rounded-md p-2 text-muted-foreground hover:text-foreground", left === id && "bg-accent text-foreground")}>
              <Icon className="h-5 w-5" />
              {id === "git" && !!git?.files.length && <span className="absolute -right-0.5 -top-0.5 rounded-full bg-brand px-1 text-[10px] text-white">{git.files.length}</span>}
            </button>
          ))}
        </nav>
        <aside className="w-64 shrink-0 border-r">
          {left === "files" && <FileExplorer entries={entries} changes={changes} activePath={current?.kind === "file" ? current.path : null}
            onOpen={(p) => openFile(p)} onCreate={create} onRename={rename} onDelete={remove} />}
          {left === "search" && <SearchPanel wsId={wsId} onOpen={openFile} />}
          {left === "git" && <SourceControl wsId={wsId} status={git} refresh={refresh} onOpenDiff={openDiff} onShowCommit={showCommit} ask={ask} onError={fail} />}
        </aside>
        {/* editor + terminal */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="thin-scroll flex h-9 shrink-0 items-end overflow-x-auto border-b bg-muted/30">
            {tabs.map((t) => {
              const dirty = t.kind === "file" && t.content !== t.saved;
              return (
                <div key={t.id} onClick={() => setActive(t.id)}
                  className={cn("group flex h-full max-w-[14rem] cursor-pointer items-center gap-1.5 border-r px-3 text-xs", active === t.id ? "bg-background" : "text-muted-foreground hover:bg-background/60")}>
                  <span className="truncate" title={t.kind === "file" ? t.path : t.title}>{t.kind === "file" ? t.path.split("/").pop() : t.title}</span>
                  {t.kind === "file" && t.stale && <span title="Changed on disk" className="text-amber-500">!</span>}
                  <button onClick={(e) => { e.stopPropagation(); closeTab(t.id); }} className="rounded p-0.5 hover:bg-accent" title="Close">
                    {dirty ? <span className="block h-2 w-2 rounded-full bg-foreground group-hover:hidden" /> : null}
                    <X className={cn("h-3 w-3", dirty && "hidden group-hover:block")} />
                  </button>
                </div>
              );
            })}
          </div>
          <div className="relative min-h-0 flex-1 overflow-hidden">
            {!current && (
              <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
                <p>Open a file from the explorer, or ask the AI to build something.</p>
                <p className="text-xs">Ctrl/⌘+S saves · select code to ask about it</p>
              </div>
            )}
            {current?.kind === "file" && (
              <div className="flex h-full flex-col">
                {current.stale && (
                  <div className="flex items-center gap-2 border-b bg-amber-500/10 px-3 py-1 text-xs">
                    This file was changed on disk while you had unsaved edits.
                    <button className="underline" onClick={() => reloadTab(current)}>Reload (discard my edits)</button>
                    <button className="underline" onClick={() => openDiff(current.path)}>View diff</button>
                  </div>
                )}
                {current.content !== current.saved && (
                  <div className="absolute right-3 top-2 z-10">
                    <Button size="sm" onClick={() => save(current)}><Save /> Save</Button>
                  </div>
                )}
                {current.binary ? (
                  <p className="p-6 text-sm text-muted-foreground">Binary file — <a className="underline" href={`${wsApi(wsId)}/raw?path=${encodeURIComponent(current.path)}`}>download</a></p>
                ) : (
                  <div className="min-h-0 flex-1">
                    <CodeEditor key={current.id} path={current.path} value={current.content} jumpTo={jump}
                      onChange={(v) => setTabs((ts) => ts.map((t) => (t.id === current.id ? { ...current, content: v } : t)))}
                      onSave={() => save(tabsRef.current.find((t) => t.id === current.id) as FileTab)}
                      onSelection={setSelection} />
                  </div>
                )}
              </div>
            )}
            {current?.kind === "diff" && (
              <div className="thin-scroll h-full overflow-y-auto">
                {current.header && <p className="border-b px-3 py-2 text-sm">{current.header}</p>}
                <DiffView diff={current.diff} onOpenFile={(p) => openFile(p)} />
              </div>
            )}
          </div>
          {showTerm && (
            <>
              <div onMouseDown={drag("y")} className="h-1 shrink-0 cursor-row-resize bg-border hover:bg-brand/50" />
              <div style={{ height: termHeight }} className="shrink-0">
                <Terminal ref={term} wsId={wsId} dirs={dirs} onFilesChanged={() => onWorkspaceChange([])} />
              </div>
            </>
          )}
        </main>
        {showChat && (
          <>
            <div onMouseDown={drag("x")} className="w-1 shrink-0 cursor-col-resize bg-border hover:bg-brand/50" />
            <aside style={{ width: chatWidth }} className="shrink-0">
              {ws ? (
                <ChatPanel ref={chat} wsId={wsId} conversations={ws.conversations} models={models} modelId={modelId}
                  onModel={(id) => { setModelId(id); localStorage.setItem("aria.model", id); }}
                  editor={editorCtx} onWorkspaceChange={onWorkspaceChange} onOpenTurnDiff={openTurnDiff} ask={ask} onConversations={loadWs}
                  onCommand={(ev) => { setShowTerm(true); if (ev.type === "command_start") term.current?.aiStart(ev.command, ev.cwd ?? ""); else term.current?.aiEnd(ev); }} />
              ) : <Loader2 className="mx-auto mt-10 h-5 w-5 animate-spin text-muted-foreground" />}
            </aside>
          </>
        )}
      </div>
      {confirmEl}
    </div>
  );
}
