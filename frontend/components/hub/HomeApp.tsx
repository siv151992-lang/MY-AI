"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, BookOpenCheck, Code2, FolderKanban, Home, Loader2, MessageSquare, Route as RouteIcon, Users, Workflow } from "lucide-react";
import { api } from "@/lib/api";
import { queueChatMessage, type Project, type Route } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { Card, ErrorNote, Shell } from "./Shell";
import { TaskList } from "./TaskList";
import { useTasks } from "./useTasks";
import { VoiceButton } from "./VoiceButton";

const ICONS = { research: BookOpenCheck, team: Users, code: Code2, automation: Workflow };

/** One input for everything: Aria routes the request (model, tools, workspace) and you launch it. */
export function HomeApp() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [route, setRoute] = useState<Route | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [stats, setStats] = useState<any>(null);
  const [name, setName] = useState("");
  const { snap, live } = useTasks();

  useEffect(() => {
    api<Project[]>("/api/projects").then(setProjects).catch(() => {});
    api<any>("/api/analytics?days=7").then(setStats).catch(() => {});
    api<{ name: string }>("/api/auth/me").then((u) => setName(u.name)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!text.trim()) { setRoute(null); return; }
    const t = setTimeout(() => {
      api<Route>("/api/router/route", { method: "POST", body: { text } }).then(setRoute).catch((e) => setError(e.message));
    }, 350);
    return () => clearTimeout(t);
  }, [text]);

  async function launch(kind: "chat" | "research" | "team" | "code" | "automation") {
    setError(null);
    setBusy(kind);
    try {
      if (kind === "chat") {
        const c = await api<{ id: string }>("/api/conversations", { method: "POST", body: { model_id: "auto", project_id: projectId || null } });
        queueChatMessage(text);
        router.push(`/chat/${c.id}`);
      } else if (kind === "research") {
        const r = await api<{ id: string }>("/api/research", { method: "POST", body: { question: text.slice(0, 2000), depth: "standard" } });
        if (projectId) await api("/api/projects/assign", { method: "POST", body: { kind: "research", id: r.id, project_id: projectId } });
        router.push(`/research/${r.id}`);
      } else if (kind === "team") {
        const r = await api<{ id: string }>("/api/team/runs", { method: "POST", body: { goal: text.slice(0, 8000) } });
        if (projectId) await api("/api/projects/assign", { method: "POST", body: { kind: "team", id: r.id, project_id: projectId } });
        router.push(`/team/${r.id}`);
      } else {
        router.push(`${kind === "code" ? "/code" : "/automations/new"}?prompt=${encodeURIComponent(text.slice(0, 1500))}`);
      }
    } catch (e: any) {
      setError(e.message);
      setBusy(null);
    }
  }

  const sug = route?.suggestion;
  const SugIcon = sug ? ICONS[sug.kind] : null;
  const t = stats?.totals;
  return (
    <Shell title="Home" icon={Home} wide>
      <div className="mb-6 space-y-1">
        <h2 className="font-serif text-3xl">{name ? `What's next, ${name.split(" ")[0]}?` : "What's next?"}</h2>
        <p className="text-sm text-muted-foreground">Describe any task. Aria picks the model and tools for it and suggests the right workspace: chat, deep research, the agent team, a code workspace or an automation.</p>
      </div>
      <Card className="space-y-3">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={8000} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) launch(sug ? sug.kind : "chat"); }}
          placeholder="e.g. Compare the top three vector databases for a 10M-document search app and recommend one"
          className="w-full resize-none bg-transparent text-[15px] outline-none" />
        <div className="flex flex-wrap items-center gap-2">
          <VoiceButton onText={(v) => setText((x) => (x ? x + " " : "") + v)} />
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="h-9 rounded-lg border bg-background px-2 text-sm" title="Project">
            <option value="">No project</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <div className="ml-auto flex flex-wrap gap-2">
            <Button variant={sug ? "outline" : "default"} disabled={!text.trim() || !!busy} onClick={() => launch("chat")}>
              {busy === "chat" ? <Loader2 className="animate-spin" /> : <MessageSquare />} Ask in chat
            </Button>
            {sug && SugIcon && (
              <Button disabled={!!busy} onClick={() => launch(sug.kind)}>
                {busy === sug.kind ? <Loader2 className="animate-spin" /> : <SugIcon />} {sug.label}
              </Button>
            )}
          </div>
        </div>
        {route && (
          <div className="rounded-xl bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="inline-flex items-center gap-1 font-medium text-foreground"><RouteIcon className="h-3.5 w-3.5" /> Routing</span>
              <span>task: <b>{route.category}</b></span><span>complexity: <b>{route.complexity}</b></span>
              <span>model: <b>{route.model_label ?? "none available"}</b></span>
              {route.needs.length > 0 && <span>needs: {route.needs.join(", ")}</span>}
              {route.agent_mode && <span>agent mode on</span>}
            </div>
            <div className="mt-1">{route.reasons.join(" · ")}</div>
            {sug && <div className="mt-1 text-foreground">{sug.reason}</div>}
          </div>
        )}
        <ErrorNote error={error} />
      </Card>

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="mb-1 flex items-center justify-between">
            <h3 className="font-medium">Running now</h3>
            <span className="text-xs text-muted-foreground">{live ? "● live" : "connecting…"}</span>
          </div>
          <TaskList items={snap?.active ?? []} empty="Nothing running. Long tasks (research, agent team, automations) show up here." />
          {snap && snap.recent.length > 0 && (
            <>
              <h3 className="mt-4 font-medium">Recent</h3>
              <TaskList items={snap.recent.slice(0, 5)} empty="" cancellable={false} />
              <Link href="/tasks" className="mt-2 inline-flex items-center gap-1 text-sm text-brand">All tasks <ArrowRight className="h-3.5 w-3.5" /></Link>
            </>
          )}
        </Card>
        <div className="space-y-4">
          <Card>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="font-medium">Projects</h3>
              <Link href="/projects" className="text-sm text-brand">Manage</Link>
            </div>
            {projects.length === 0 ? <p className="text-sm text-muted-foreground">Group chats, documents, code and runs with shared instructions. <Link href="/projects" className="text-brand">Create one</Link>.</p>
              : <ul className="space-y-1">{projects.slice(0, 6).map((p) => (
                <li key={p.id}><Link href={`/projects/${p.id}`} className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm hover:bg-accent"><FolderKanban className="h-4 w-4 text-muted-foreground" />{p.name}<span className="ml-auto text-xs text-muted-foreground">{p.chats} chats</span></Link></li>))}</ul>}
          </Card>
          <Card>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="font-medium">Last 7 days</h3>
              <Link href="/analytics" className="text-sm text-brand">Analytics</Link>
            </div>
            {t ? (
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <div><dt className="text-xs text-muted-foreground">Model calls</dt><dd className="text-lg font-medium">{t.calls}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Tokens</dt><dd className="text-lg font-medium">{(t.input_tokens + t.output_tokens).toLocaleString()}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Median latency</dt><dd className="text-lg font-medium">{t.p50_ms != null ? `${(t.p50_ms / 1000).toFixed(1)}s` : "–"}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Error rate</dt><dd className="text-lg font-medium">{(t.error_rate * 100).toFixed(1)}%</dd></div>
              </dl>
            ) : <p className="text-sm text-muted-foreground">No usage yet.</p>}
          </Card>
        </div>
      </div>
    </Shell>
  );
}
