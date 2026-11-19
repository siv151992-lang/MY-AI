"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { FlaskConical, Loader2, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { wsApi, type ExecResult } from "@/lib/workspace";
import { cn } from "@/lib/utils";

export type TermEntry = {
  id: number; by: "you" | "ai"; cwd: string; command: string; running: boolean;
  exit_code?: number; stdout?: string; stderr?: string; timed_out?: boolean; note?: string;
};
export type TerminalHandle = { aiStart: (command: string, cwd: string) => void; aiEnd: (ev: any) => void; run: (cmd: string) => void };

let seq = 0;

export const Terminal = forwardRef<TerminalHandle, { wsId: string; dirs: Set<string>; onFilesChanged: () => void }>(
  function Terminal({ wsId, dirs, onFilesChanged }, ref) {
    const [entries, setEntries] = useState<TermEntry[]>([]);
    const [input, setInput] = useState("");
    const [cwd, setCwd] = useState("");
    const [history, setHistory] = useState<string[]>([]);
    const [hIdx, setHIdx] = useState(-1);
    const [busy, setBusy] = useState(false);
    const scroller = useRef<HTMLDivElement>(null);

    useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [entries]);

    const patch = (id: number, p: Partial<TermEntry>) => setEntries((es) => es.map((e) => (e.id === id ? { ...e, ...p } : e)));

    async function run(command: string) {
      command = command.trim();
      if (!command) return;
      setHistory((h) => [command, ...h.filter((x) => x !== command)].slice(0, 100));
      setHIdx(-1);
      // `cd` is handled here: each command runs in a fresh sandbox process, so only the directory carries over.
      const cd = command.match(/^cd(?:\s+(.*))?$/);
      if (cd) {
        const target = resolve(cwd, (cd[1] ?? "").trim());
        if (target !== null && (target === "" || dirs.has(target))) setCwd(target);
        else setEntries((es) => [...es, { id: ++seq, by: "you", cwd, command, running: false, exit_code: 1, stderr: `cd: no such directory: ${cd[1]}` }]);
        return;
      }
      if (command === "clear") { setEntries([]); return; }
      const id = ++seq;
      setEntries((es) => [...es, { id, by: "you", cwd, command, running: true }]);
      setBusy(true);
      try {
        const r = await api<ExecResult>(`${wsApi(wsId)}/exec`, { method: "POST", body: { command, cwd, timeout: 120 } });
        const changed = [...r.written.map((p) => `+ ${p}`), ...r.deleted.map((p) => `− ${p}`)];
        patch(id, { running: false, exit_code: r.exit_code, stdout: r.stdout, stderr: r.stderr, timed_out: r.timed_out,
          note: changed.length ? `files changed: ${changed.join(", ")}` : undefined });
        if (changed.length) onFilesChanged();
      } catch (e: any) {
        patch(id, { running: false, exit_code: -1, stderr: e.message });
      } finally { setBusy(false); }
    }

    useImperativeHandle(ref, () => ({
      run,
      aiStart: (command, cwd) => setEntries((es) => [...es, { id: ++seq, by: "ai", cwd, command, running: true }]),
      aiEnd: (ev) => setEntries((es) => {
        const idx = es.map((e) => e.by === "ai" && e.running && e.command === ev.command).lastIndexOf(true);
        if (idx < 0) return es;
        const copy = [...es];
        copy[idx] = { ...copy[idx], running: false, exit_code: ev.exit_code, stdout: ev.stdout, stderr: ev.stderr, timed_out: ev.timed_out };
        return copy;
      }),
    }));

    async function runTests() {
      try {
        const { command } = await api<{ command: string | null }>(`${wsApi(wsId)}/test-command`);
        if (command) run(command);
        else setEntries((es) => [...es, { id: ++seq, by: "you", cwd, command: "(run tests)", running: false, exit_code: 1,
          stderr: "No test setup detected. Type the test command, e.g. python -m pytest -q or node --test." }]);
      } catch (e: any) { setEntries((es) => [...es, { id: ++seq, by: "you", cwd, command: "(run tests)", running: false, exit_code: -1, stderr: e.message }]); }
    }

    return (
      <div className="flex h-full flex-col bg-zinc-950 font-mono text-[12.5px] text-zinc-200">
        <div className="flex h-8 shrink-0 items-center gap-2 border-b border-zinc-800 px-3 text-xs text-zinc-400">
          <span className="font-sans font-medium uppercase tracking-wide">Terminal</span>
          <span className="truncate font-sans">sandboxed · no network · each command runs on a fresh copy of the project</span>
          <span className="ml-auto flex gap-1">
            <button onClick={runTests} disabled={busy} className="flex items-center gap-1 rounded px-2 py-0.5 font-sans hover:bg-zinc-800"><FlaskConical className="h-3.5 w-3.5" /> Run tests</button>
            <button onClick={() => setEntries([])} title="Clear" className="rounded p-1 hover:bg-zinc-800"><Trash2 className="h-3.5 w-3.5" /></button>
          </span>
        </div>
        <div ref={scroller} className="thin-scroll min-h-0 flex-1 overflow-y-auto px-3 py-2">
          {entries.map((e) => (
            <div key={e.id} className="mb-2">
              <div className="flex gap-2">
                <span className={cn(e.by === "ai" ? "text-violet-400" : "text-emerald-400")}>{e.by === "ai" ? "AI" : "you"}</span>
                <span className="text-sky-400">~/{e.cwd}</span>
                <span className="text-zinc-500">$</span>
                <span className="whitespace-pre-wrap break-all">{e.command}</span>
              </div>
              {e.running && <Loader2 className="my-1 h-3.5 w-3.5 animate-spin text-zinc-500" />}
              {e.stdout && <pre className="whitespace-pre-wrap break-words text-zinc-300">{e.stdout}</pre>}
              {e.stderr && <pre className="whitespace-pre-wrap break-words text-red-400">{e.stderr}</pre>}
              {e.note && <p className="text-amber-300">{e.note}</p>}
              {!e.running && e.exit_code !== undefined && e.exit_code !== 0 && <p className="text-zinc-500">exit {e.exit_code}{e.timed_out ? " (timed out)" : ""}</p>}
            </div>
          ))}
          <form className="flex gap-2" onSubmit={(ev) => { ev.preventDefault(); const c = input; setInput(""); run(c); }}>
            <span className="text-emerald-400">you</span><span className="text-sky-400">~/{cwd}</span><span className="text-zinc-500">$</span>
            <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy} spellCheck={false} autoComplete="off"
              onKeyDown={(e) => {
                if (e.key === "ArrowUp" && history.length) { e.preventDefault(); const i = Math.min(hIdx + 1, history.length - 1); setHIdx(i); setInput(history[i]); }
                if (e.key === "ArrowDown") { e.preventDefault(); const i = hIdx - 1; setHIdx(Math.max(i, -1)); setInput(i >= 0 ? history[i] : ""); }
              }}
              className="min-w-0 flex-1 bg-transparent outline-none" placeholder={busy ? "running…" : "type a command, e.g. ls or python3 app.py"} />
          </form>
        </div>
      </div>
    );
  });

function resolve(cwd: string, target: string): string | null {
  if (!target || target === "~" || target === "/") return "";
  const parts = target.startsWith("/") ? [] : cwd ? cwd.split("/") : [];
  for (const p of target.split("/")) {
    if (!p || p === ".") continue;
    if (p === "..") { if (!parts.length) return null; parts.pop(); } else parts.push(p);
  }
  return parts.join("/");
}
