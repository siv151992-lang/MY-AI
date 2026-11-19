"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp, Copy, Loader2, Play, Plus, RefreshCw, Trash2, Workflow, X } from "lucide-react";
import { api } from "@/lib/api";
import { ago, type Automation, type AutomationRun, type AutomationStep, type CustomAgent, type Integration, type Skill } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/chat/Markdown";
import { Card, ErrorNote, Field, inputCls, ModelSelect, Shell, StatusPill, ToolPicker } from "./Shell";
import { useCatalog } from "./useCatalog";

const STEP_TYPES: { type: string; label: string; help: string }[] = [
  { type: "prompt", label: "AI task", help: "Instructions for the AI, optionally with tools." },
  { type: "skill", label: "Run a skill", help: "A saved or built-in skill; {{prev}} is passed as its input by default." },
  { type: "agent", label: "Custom agent", help: "Give one of your custom agents a task." },
  { type: "research", label: "Deep research", help: "Sourced research report (needs a web search key)." },
  { type: "team", label: "Agent team", help: "Multi-agent run; waits for the verified result." },
  { type: "http", label: "Call an API", help: "One of your integrations. Writes only if allowed there." },
  { type: "notify", label: "Send notification", help: "Slack or email (needs SLACK_WEBHOOK_URL / SMTP on the server)." },
];

export function AutomationsApp({ id }: { id: string | null }) {
  if (id) return <Editor id={id === "new" ? null : id} />;
  return <List />;
}

function List() {
  const [list, setList] = useState<Automation[] | null>(null);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Automation[]>("/api/automations").then(setList).catch((e) => setError(e.message));
    api<AutomationRun[]>("/api/automation-runs").then(setRuns).catch(() => {});
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);
  return (
    <Shell title="Automations" icon={Workflow} actions={<Button size="sm" asChild><Link href="/automations/new"><Plus /> New automation</Link></Button>}>
      <p className="mb-4 text-sm text-muted-foreground">Multi-step workflows that run on demand, on a schedule, or when another service calls a secret webhook URL. Each step can use the previous step's output.</p>
      <ErrorNote error={error} />
      {!list ? <Loader2 className="animate-spin text-muted-foreground" /> : list.length === 0 ? <Card><p className="text-sm text-muted-foreground">No automations yet.</p></Card> : (
        <div className="space-y-2">
          {list.map((a) => (
            <Link key={a.id} href={`/automations/${a.id}`} className="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4 hover:bg-accent/50">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{a.name} {!a.enabled && <span className="text-xs text-muted-foreground">(paused)</span>}</div>
                <div className="text-xs text-muted-foreground">
                  {a.trigger_type === "schedule" ? `Scheduled ${a.schedule_text ?? a.schedule} · next ${a.next_run_at ? new Date(a.next_run_at).toLocaleString() : "–"}` : a.trigger_type === "webhook" ? "Webhook trigger" : "Manual"}
                  {" · "}{a.steps.length} step(s) · last run {ago(a.last_run_at)}
                </div>
              </div>
              {a.last_status && <StatusPill status={a.last_status} />}
            </Link>
          ))}
        </div>
      )}
      {runs.length > 0 && <><h2 className="mb-2 mt-6 font-medium">Recent runs</h2><RunList runs={runs.slice(0, 10)} /></>}
    </Shell>
  );
}

function RunList({ runs }: { runs: AutomationRun[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="space-y-2">
      {runs.map((r) => (
        <Card key={r.id} className="p-3">
          <button className="flex w-full flex-wrap items-center gap-2 text-left text-sm" onClick={() => setOpen(open === r.id ? null : r.id)}>
            <StatusPill status={r.status} />
            <span className="font-medium">{r.automation_name}</span>
            <span className="text-xs text-muted-foreground">{r.trigger} · {new Date(r.created_at).toLocaleString()}</span>
            <span className="ml-auto flex gap-1">{r.steps.map((s) => <span key={s.index} title={`${s.index}. ${s.title}: ${s.status}`} className={`h-2 w-4 rounded-full ${s.status === "succeeded" ? "bg-emerald-500" : s.status === "failed" ? "bg-red-500" : s.status === "running" ? "animate-pulse bg-amber-500" : "bg-muted"}`} />)}</span>
          </button>
          {r.error && <p className="mt-1 text-xs text-destructive">{r.error}</p>}
          {open === r.id && (
            <div className="mt-2 space-y-2 border-t pt-2">
              {r.steps.map((s) => (
                <div key={s.index} className="text-sm">
                  <div className="flex items-center gap-2"><span className="text-muted-foreground">{s.index}.</span> {s.title} <StatusPill status={s.status} /></div>
                  {s.error && <p className="text-xs text-destructive">{s.error}</p>}
                  {s.output && <details className="mt-1"><summary className="cursor-pointer text-xs text-muted-foreground">Output</summary><div className="answer mt-1 max-h-80 overflow-auto rounded-lg bg-muted/40 p-2 text-sm"><Markdown content={s.output} /></div></details>}
                </div>
              ))}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

function Editor({ id }: { id: string | null }) {
  const router = useRouter();
  const { models, tools } = useCatalog();
  const [a, setA] = useState<Partial<Automation>>({ name: "", enabled: true, trigger_type: "manual", schedule: "@daily",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", steps: [{ type: "prompt", instructions: "" }] });
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [hookUrl, setHookUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [agents, setAgents] = useState<CustomAgent[]>([]);
  const [ints, setInts] = useState<Integration[]>([]);

  const load = useCallback(() => {
    if (!id) return;
    api<Automation>(`/api/automations/${id}`).then((x) => { setA(x); setRuns(x.runs ?? []); }).catch((e) => setError(e.message));
  }, [id]);
  useEffect(() => {
    load();
    api<Skill[]>("/api/skills").then(setSkills).catch(() => {});
    api<CustomAgent[]>("/api/agents").then(setAgents).catch(() => {});
    api<{ custom: Integration[] }>("/api/integrations").then((d) => setInts(d.custom)).catch(() => {});
    if (!id) {
      const p = new URLSearchParams(window.location.search).get("prompt");
      if (p) setA((x) => ({ ...x, name: p.split(/\s+/).slice(0, 6).join(" ").slice(0, 80), steps: [{ type: "prompt", instructions: p }] }));
    }
  }, [id, load]);
  useEffect(() => {
    if (!id || !runs.some((r) => r.status === "running")) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [id, runs, load]);

  const steps = a.steps ?? [];
  const setStep = (i: number, patch: Partial<AutomationStep>) => setA({ ...a, steps: steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const move = (i: number, d: number) => { const s = [...steps]; const [x] = s.splice(i, 1); s.splice(i + d, 0, x); setA({ ...a, steps: s }); };

  async function save() {
    setSaving(true); setError(null);
    try {
      const body = { name: a.name, enabled: a.enabled, trigger_type: a.trigger_type, schedule: a.schedule, timezone: a.timezone, steps: a.steps, project_id: a.project_id ?? null };
      const res = await api<Automation>(id ? `/api/automations/${id}` : "/api/automations", { method: id ? "PUT" : "POST", body });
      if (res.webhook_url) setHookUrl(res.webhook_url);
      if (!id) router.replace(`/automations/${res.id}`); else load();
    } catch (e: any) { setError(e.message); } finally { setSaving(false); }
  }
  async function runNow() {
    setError(null);
    try { await api(`/api/automations/${id}/run`, { method: "POST" }); load(); } catch (e: any) { setError(e.message); }
  }

  return (
    <Shell title={id ? a.name || "Automation" : "New automation"} icon={Workflow} actions={id && <>
      <Button size="sm" onClick={runNow}><Play /> Run now</Button>
      <Button size="sm" variant="ghost" title="Delete" onClick={() => confirm("Delete this automation and its run history?") && api(`/api/automations/${id}`, { method: "DELETE" }).then(() => router.push("/automations"))}><Trash2 /></Button>
    </>}>
      <div className="space-y-4">
        <Card className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name"><input className={inputCls} value={a.name ?? ""} onChange={(e) => setA({ ...a, name: e.target.value })} /></Field>
            <Field label="Trigger">
              <select className={inputCls} value={a.trigger_type} onChange={(e) => setA({ ...a, trigger_type: e.target.value as any })}>
                <option value="manual">Manual (Run now)</option><option value="schedule">Schedule</option><option value="webhook">Webhook (secret URL)</option>
              </select>
            </Field>
          </div>
          {a.trigger_type === "schedule" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Schedule" hint="@hourly, @daily (09:00), @weekdays, @weekly, '@every 30m', or cron like '30 8 * * 1-5'. Minimum 5 minutes.">
                <input className={inputCls} value={a.schedule ?? ""} onChange={(e) => setA({ ...a, schedule: e.target.value })} />
              </Field>
              <Field label="Time zone"><input className={inputCls} value={a.timezone ?? "UTC"} onChange={(e) => setA({ ...a, timezone: e.target.value })} /></Field>
            </div>
          )}
          {a.trigger_type === "webhook" && (
            <p className="text-xs text-muted-foreground">POST JSON to the secret URL (shown once after saving) to start a run. The body is available as {"{{payload}}"} and fields as {"{{payload.field}}"}.
              {id && a.has_webhook && <> <button className="text-brand underline" onClick={() => api<{ webhook_url: string }>(`/api/automations/${id}/webhook`, { method: "POST" }).then((r) => setHookUrl(r.webhook_url))}>Create a new URL</button> (the old one stops working).</>}</p>
          )}
          {hookUrl && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              <div className="mb-1 font-medium">Webhook URL — copy it now, it won't be shown again:</div>
              <div className="flex items-center gap-2"><code className="break-all">{hookUrl}</code><Button size="icon-sm" variant="ghost" title="Copy" onClick={() => navigator.clipboard?.writeText(hookUrl)}><Copy /></Button></div>
            </div>
          )}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!a.enabled} onChange={(e) => setA({ ...a, enabled: e.target.checked })} /> Enabled</label>
        </Card>

        <h2 className="font-medium">Steps <span className="text-sm font-normal text-muted-foreground">— use {"{{prev}}"}, {"{{steps.1}}"}, {"{{date}}"}, {"{{payload}}"}</span></h2>
        {steps.map((s, i) => (
          <Card key={i} className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{i + 1}.</span>
              <select className={cn(inputCls, "w-44 shrink-0")} value={s.type} onChange={(e) => setStep(i, { type: e.target.value })}>
                {STEP_TYPES.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
              </select>
              <input className={cn(inputCls, "min-w-0 flex-1")} placeholder="Step title (optional)" value={s.title ?? ""} onChange={(e) => setStep(i, { title: e.target.value })} />
              <Button size="icon-sm" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} title="Move up"><ArrowUp /></Button>
              <Button size="icon-sm" variant="ghost" disabled={i === steps.length - 1} onClick={() => move(i, 1)} title="Move down"><ArrowDown /></Button>
              <Button size="icon-sm" variant="ghost" disabled={steps.length === 1} onClick={() => setA({ ...a, steps: steps.filter((_, j) => j !== i) })} title="Remove"><X /></Button>
            </div>
            <p className="text-xs text-muted-foreground">{STEP_TYPES.find((t) => t.type === s.type)?.help}</p>
            <StepFields s={s} set={(p) => setStep(i, p)} models={models} tools={tools} skills={skills} agents={agents} ints={ints} />
            <label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={!!s.continue_on_error} onChange={(e) => setStep(i, { continue_on_error: e.target.checked })} /> Continue if this step fails</label>
          </Card>
        ))}
        {steps.length < 10 && <Button variant="outline" onClick={() => setA({ ...a, steps: [...steps, { type: "prompt", instructions: "" }] })}><Plus /> Add step</Button>}
        <ErrorNote error={error} />
        <Button onClick={save} disabled={saving}>{saving && <Loader2 className="animate-spin" />} {id ? "Save changes" : "Create automation"}</Button>
        {id && <><div className="flex items-center gap-2 pt-4"><h2 className="font-medium">Runs</h2><Button size="icon-sm" variant="ghost" title="Refresh" onClick={load}><RefreshCw /></Button></div>
          {runs.length ? <RunList runs={runs} /> : <p className="text-sm text-muted-foreground">No runs yet.</p>}</>}
      </div>
    </Shell>
  );
}

function StepFields({ s, set, models, tools, skills, agents, ints }: {
  s: AutomationStep; set: (p: Partial<AutomationStep>) => void; models: any[]; tools: any[]; skills: Skill[]; agents: CustomAgent[]; ints: Integration[];
}) {
  const ta = (k: string, rows = 3, ph = "") => <textarea className={inputCls} rows={rows} placeholder={ph} value={s[k] ?? ""} onChange={(e) => set({ [k]: e.target.value })} />;
  switch (s.type) {
    case "prompt":
      return <>
        <Field label="Instructions">{ta("instructions", 4, "Summarize {{prev}} into 5 bullet points for the team")}</Field>
        <Field label="Model"><ModelSelect models={models} value={s.model_id ?? null} onChange={(v) => set({ model_id: v })} /></Field>
        <details><summary className="cursor-pointer text-sm">Tools ({(s.tools ?? []).length})</summary><div className="mt-2"><ToolPicker tools={tools} value={s.tools ?? []} onChange={(v) => set({ tools: v })} /></div></details>
      </>;
    case "skill": {
      const sk = skills.find((x) => x.id === s.skill_id);
      return <>
        <Field label="Skill"><select className={inputCls} value={s.skill_id ?? ""} onChange={(e) => set({ skill_id: e.target.value, inputs: {} })}>
          <option value="">Choose…</option>{skills.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        {sk?.inputs.map((inp) => (
          <Field key={inp.name} label={inp.label} hint={inp.name === "input" ? "Empty = the previous step's output" : undefined}>
            <input className={inputCls} value={(s.inputs ?? {})[inp.name] ?? ""} onChange={(e) => set({ inputs: { ...(s.inputs ?? {}), [inp.name]: e.target.value } })} />
          </Field>))}
      </>;
    }
    case "agent":
      return <>
        <Field label="Agent"><select className={inputCls} value={s.agent_id ?? ""} onChange={(e) => set({ agent_id: e.target.value })}>
          <option value="">Choose…</option>{agents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        {agents.length === 0 && <p className="text-xs text-muted-foreground">You have no custom agents yet. <Link className="text-brand" href="/agents">Create one</Link>.</p>}
        <Field label="Task">{ta("task")}</Field>
      </>;
    case "research":
      return <>
        <Field label="Question">{ta("question", 2)}</Field>
        <Field label="Depth"><select className={inputCls} value={s.depth ?? "standard"} onChange={(e) => set({ depth: e.target.value })}><option value="quick">Quick</option><option value="standard">Standard</option><option value="deep">Deep</option></select></Field>
      </>;
    case "team":
      return <Field label="Goal">{ta("goal")}</Field>;
    case "http":
      return <>
        <Field label="Integration"><select className={inputCls} value={s.integration_id ?? ""} onChange={(e) => set({ integration_id: e.target.value })}>
          <option value="">Choose…</option>{ints.map((x) => <option key={x.id} value={x.id}>{x.name}{x.allow_writes ? "" : " (read-only)"}</option>)}</select></Field>
        {ints.length === 0 && <p className="text-xs text-muted-foreground">No API connections yet. <Link className="text-brand" href="/integrations">Connect one</Link>.</p>}
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Method"><select className={inputCls} value={s.method ?? "GET"} onChange={(e) => set({ method: e.target.value })}>{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <div className="sm:col-span-3"><Field label="Path"><input className={inputCls} value={s.path ?? ""} placeholder="/issues" onChange={(e) => set({ path: e.target.value })} /></Field></div>
        </div>
        {(s.method ?? "GET") !== "GET" && <Field label="JSON body" hint='Variables are filled in first, e.g. {"text": "{{prev}}"}'>{ta("body", 3)}</Field>}
      </>;
    case "notify":
      return <>
        <Field label="Channel"><select className={inputCls} value={s.channel ?? ""} onChange={(e) => set({ channel: e.target.value })}><option value="">Choose…</option><option value="slack">Slack</option><option value="email">Email</option></select></Field>
        {s.channel === "email" && <><Field label="To" hint="Comma-separated, max 10"><input className={inputCls} value={s.to ?? ""} onChange={(e) => set({ to: e.target.value })} /></Field>
          <Field label="Subject"><input className={inputCls} value={s.subject ?? ""} onChange={(e) => set({ subject: e.target.value })} /></Field></>}
        <Field label="Message">{ta("message", 3, "{{prev}}")}</Field>
      </>;
  }
  return null;
}
