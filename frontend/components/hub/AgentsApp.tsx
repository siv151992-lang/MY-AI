"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, MessageSquare, Pencil, Play, Plus, Trash2, UserCog } from "lucide-react";
import { api } from "@/lib/api";
import type { CustomAgent, TaskResult } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { Card, ErrorNote, Field, inputCls, ModelSelect, Shell, ToolPicker } from "./Shell";
import { TaskResultView } from "./TaskResultView";
import { useCatalog } from "./useCatalog";

type Draft = Omit<CustomAgent, "id" | "team_key" | "updated_at"> & { id?: string };
const EMPTY: Draft = { name: "", description: "", instructions: "", tools: [], model_id: null, use_memory: true, in_team: true };

export function AgentsApp() {
  const router = useRouter();
  const { models, tools } = useCatalog();
  const [agents, setAgents] = useState<CustomAgent[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tasking, setTasking] = useState<CustomAgent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => api<CustomAgent[]>("/api/agents").then(setAgents).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function save() {
    if (!draft) return;
    setError(null);
    try { await api(draft.id ? `/api/agents/${draft.id}` : "/api/agents", { method: draft.id ? "PUT" : "POST", body: draft }); setDraft(null); load(); }
    catch (e: any) { setError(e.message); }
  }
  async function chat(a: CustomAgent) {
    const c = await api<{ id: string }>("/api/conversations", { method: "POST", body: { agent_id: a.id, model_id: a.model_id ?? "auto" } });
    router.push(`/chat/${c.id}`);
  }

  return (
    <Shell title="Custom agents" icon={UserCog} actions={<Button size="sm" onClick={() => setDraft({ ...EMPTY })}><Plus /> New agent</Button>}>
      <p className="mb-4 text-sm text-muted-foreground">Create specialists with their own instructions, tools and model. Chat with them, give them one-off tasks, use them in automations, and (if enabled) the agent team's planner can assign them work alongside the built-in agents.</p>
      <ErrorNote error={error} />
      {draft && (
        <Card className="mb-4 space-y-3">
          <Field label="Name"><input className={inputCls} value={draft.name} maxLength={60} onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus /></Field>
          <Field label="What it's for" hint="Shown to the team planner when it chooses agents."><input className={inputCls} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <Field label="Instructions"><textarea className={inputCls} rows={7} value={draft.instructions} placeholder="You review contracts for a small software company. Flag unusual liability, IP and termination clauses…" onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} /></Field>
          <Field label="Model"><ModelSelect models={models} value={draft.model_id} onChange={(v) => setDraft({ ...draft, model_id: v })} /></Field>
          <Field label="Tools"><ToolPicker tools={tools} value={draft.tools} onChange={(v) => setDraft({ ...draft, tools: v })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.in_team} onChange={(e) => setDraft({ ...draft, in_team: e.target.checked })} /> Available to the agent team</label>
          <div className="flex gap-2"><Button disabled={!draft.name.trim() || !draft.instructions.trim()} onClick={save}>Save agent</Button><Button variant="ghost" onClick={() => setDraft(null)}>Cancel</Button></div>
        </Card>
      )}
      {tasking && <AgentTask agent={tasking} onClose={() => setTasking(null)} />}
      {!agents ? <Loader2 className="animate-spin text-muted-foreground" /> : agents.length === 0 && !draft ? (
        <Card><p className="text-sm text-muted-foreground">No custom agents yet.</p></Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {agents.map((a) => (
            <Card key={a.id}>
              <div className="font-medium">{a.name}</div>
              <div className="line-clamp-2 text-sm text-muted-foreground">{a.description || a.instructions}</div>
              <div className="mt-2 text-xs text-muted-foreground">
                {a.model_id ?? "auto model"} · {a.tools.length ? a.tools.join(", ") : "no tools"}{a.in_team ? " · in agent team" : ""}
              </div>
              <div className="mt-3 flex flex-wrap gap-1">
                <Button size="sm" onClick={() => chat(a)}><MessageSquare /> Chat</Button>
                <Button size="sm" variant="ghost" onClick={() => setTasking(a)}><Play /> Give a task</Button>
                <Button size="sm" variant="ghost" title="Edit" onClick={() => setDraft({ ...a })}><Pencil /></Button>
                <Button size="sm" variant="ghost" title="Delete" onClick={() => confirm(`Delete agent "${a.name}"?`) && api(`/api/agents/${a.id}`, { method: "DELETE" }).then(load)}><Trash2 /></Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </Shell>
  );
}

function AgentTask({ agent, onClose }: { agent: CustomAgent; onClose: () => void }) {
  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<TaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setBusy(true); setError(null); setRes(null);
    try { setRes(await api<TaskResult>(`/api/agents/${agent.id}/run`, { method: "POST", body: { task } })); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }
  return (
    <Card className="mb-4 space-y-3 border-brand/40">
      <div className="flex items-center justify-between"><h3 className="font-medium">Task for {agent.name}</h3><Button size="sm" variant="ghost" onClick={onClose}>Close</Button></div>
      <textarea className={inputCls} rows={4} value={task} onChange={(e) => setTask(e.target.value)} autoFocus />
      <ErrorNote error={error} />
      <Button onClick={run} disabled={busy || !task.trim()}>{busy ? <Loader2 className="animate-spin" /> : <Play />} {busy ? "Working…" : "Run"}</Button>
      {res && <TaskResultView r={res} />}
    </Card>
  );
}
