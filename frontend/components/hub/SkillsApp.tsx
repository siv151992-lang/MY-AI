"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Copy, Loader2, MessageSquare, Pencil, Play, Plus, Sparkles, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { queueChatMessage, type Skill, type TaskResult } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { Card, ErrorNote, Field, inputCls, ModelSelect, Shell, ToolPicker } from "./Shell";
import { TaskResultView } from "./TaskResultView";
import { useCatalog } from "./useCatalog";

type Draft = { id?: string; name: string; description: string; instructions: string; tools: string[]; model_id: string | null };
const EMPTY: Draft = { name: "", description: "", instructions: "", tools: [], model_id: null };

export function SkillsApp() {
  const router = useRouter();
  const { models, tools } = useCatalog();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [running, setRunning] = useState<Skill | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => api<Skill[]>("/api/skills").then(setSkills).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function save() {
    if (!draft) return;
    setError(null);
    try {
      await api(draft.id ? `/api/skills/${draft.id}` : "/api/skills", { method: draft.id ? "PUT" : "POST", body: draft });
      setDraft(null); load();
    } catch (e: any) { setError(e.message); }
  }

  async function useInChat(s: Skill) {
    const c = await api<{ id: string }>("/api/conversations", { method: "POST", body: { model_id: "auto" } });
    queueChatMessage("", s.id);
    router.push(`/chat/${c.id}`);
  }

  return (
    <Shell title="Skills" icon={Sparkles} actions={<Button size="sm" onClick={() => setDraft({ ...EMPTY })}><Plus /> New skill</Button>}>
      <p className="mb-4 text-sm text-muted-foreground">A skill is a reusable instruction with input fields, allowed tools and an optional model. Run it here, apply it to a chat message (the ✦ menu in the chat box), or use it as an automation step. Use <code>{"{{name}}"}</code> placeholders for inputs.</p>
      <ErrorNote error={error} />
      {draft && (
        <Card className="mb-4 space-y-3">
          <Field label="Name"><input className={inputCls} value={draft.name} maxLength={80} onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus /></Field>
          <Field label="Description"><input className={inputCls} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <Field label="Instructions" hint="Placeholders like {{topic}} become input fields. {{input}} is the main text (in chat: your message).">
            <textarea className={`${inputCls} font-mono`} rows={7} value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} />
          </Field>
          <Field label="Model"><ModelSelect models={models} value={draft.model_id} onChange={(v) => setDraft({ ...draft, model_id: v })} /></Field>
          <Field label="Tools the skill may use"><ToolPicker tools={tools} value={draft.tools} onChange={(v) => setDraft({ ...draft, tools: v })} /></Field>
          <div className="flex gap-2"><Button disabled={!draft.name.trim() || !draft.instructions.trim()} onClick={save}>Save skill</Button><Button variant="ghost" onClick={() => setDraft(null)}>Cancel</Button></div>
        </Card>
      )}
      {running && <RunSkill skill={running} onClose={() => { setRunning(null); load(); }} />}
      {!skills ? <Loader2 className="animate-spin text-muted-foreground" /> : (
        <div className="grid gap-3 sm:grid-cols-2">
          {skills.map((s) => (
            <Card key={s.id} className="flex flex-col">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{s.name} {s.builtin && <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">built-in</span>}</div>
                  <div className="text-sm text-muted-foreground">{s.description}</div>
                </div>
              </div>
              <div className="mt-2 text-xs text-muted-foreground">
                Inputs: {s.inputs.map((i) => i.label).join(", ")}{s.tools.length ? ` · tools: ${s.tools.join(", ")}` : ""}{s.uses != null ? ` · used ${s.uses}×` : ""}
              </div>
              <div className="mt-3 flex flex-wrap gap-1">
                <Button size="sm" onClick={() => setRunning(s)}><Play /> Run</Button>
                <Button size="sm" variant="ghost" onClick={() => useInChat(s)}><MessageSquare /> Use in chat</Button>
                {s.builtin
                  ? <Button size="sm" variant="ghost" onClick={() => setDraft({ name: `${s.name} (copy)`, description: s.description, instructions: s.instructions, tools: s.tools, model_id: s.model_id })}><Copy /> Duplicate</Button>
                  : <>
                    <Button size="sm" variant="ghost" title="Edit" onClick={() => setDraft({ id: s.id, name: s.name, description: s.description, instructions: s.instructions, tools: s.tools, model_id: s.model_id })}><Pencil /></Button>
                    <Button size="sm" variant="ghost" title="Delete" onClick={() => confirm(`Delete skill "${s.name}"?`) && api(`/api/skills/${s.id}`, { method: "DELETE" }).then(load)}><Trash2 /></Button>
                  </>}
              </div>
            </Card>
          ))}
        </div>
      )}
    </Shell>
  );
}

function RunSkill({ skill, onClose }: { skill: Skill; onClose: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(Object.fromEntries(skill.inputs.map((i) => [i.name, i.default ?? ""])));
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<TaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setBusy(true); setError(null); setRes(null);
    try { setRes(await api<TaskResult>(`/api/skills/${skill.id}/run`, { method: "POST", body: { values } })); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }
  return (
    <Card className="mb-4 space-y-3 border-brand/40">
      <div className="flex items-center justify-between"><h3 className="font-medium">Run: {skill.name}</h3><Button size="sm" variant="ghost" onClick={onClose}>Close</Button></div>
      {skill.inputs.map((i) => (
        <Field key={i.name} label={`${i.label}${i.required ? " *" : ""}`}>
          <textarea className={inputCls} rows={i.name === "input" ? 5 : 1} value={values[i.name] ?? ""} onChange={(e) => setValues({ ...values, [i.name]: e.target.value })} />
        </Field>
      ))}
      <ErrorNote error={error} />
      <Button onClick={run} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <Play />} {busy ? "Running…" : "Run skill"}</Button>
      {res && <TaskResultView r={res} />}
    </Card>
  );
}
