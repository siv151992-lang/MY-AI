"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, FolderKanban, Loader2, MessageSquarePlus, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { ago, type Project } from "@/lib/hub";
import type { Conversation } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, ErrorNote, Field, inputCls, Shell, StatusPill } from "./Shell";

const KIND_LABEL: Record<string, string> = { conversation: "Chats", document: "Documents", workspace: "Code workspaces",
  research: "Deep research", team: "Agent team runs", agent_run: "Autonomous agent runs", automation: "Automations" };

export function ProjectsApp({ id }: { id: string | null }) {
  return id ? <ProjectView id={id} /> : <ProjectList />;
}

function ProjectList() {
  const router = useRouter();
  const [list, setList] = useState<Project[] | null>(null);
  const [form, setForm] = useState({ name: "", description: "", instructions: "" });
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => { api<Project[]>("/api/projects").then(setList).catch((e) => setError(e.message)); }, []);

  async function create() {
    setError(null);
    try {
      const p = await api<Project>("/api/projects", { method: "POST", body: form });
      router.push(`/projects/${p.id}`);
    } catch (e: any) { setError(e.message); }
  }

  return (
    <Shell title="Projects" icon={FolderKanban} actions={<Button size="sm" onClick={() => setCreating(true)}><Plus /> New project</Button>}>
      <p className="mb-4 text-sm text-muted-foreground">A project groups chats, documents, code workspaces, research, team runs and automations. Its instructions apply to every chat in it, and memories learned in a project stay in that project.</p>
      {creating && (
        <Card className="mb-4 space-y-3">
          <Field label="Name"><input className={inputCls} value={form.name} maxLength={80} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus /></Field>
          <Field label="Description"><input className={inputCls} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Instructions for every chat in this project" hint="e.g. 'We are building a Django app on Postgres. Answer concisely and use British English.'">
            <textarea className={inputCls} rows={4} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} />
          </Field>
          <ErrorNote error={error} />
          <div className="flex gap-2"><Button disabled={!form.name.trim()} onClick={create}>Create project</Button><Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button></div>
        </Card>
      )}
      {!list ? <Loader2 className="animate-spin text-muted-foreground" /> : list.length === 0 && !creating ? (
        <Card><p className="text-sm text-muted-foreground">No projects yet.</p></Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {list.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`} className="rounded-2xl border bg-card p-4 transition-colors hover:bg-accent/50">
              <div className="font-medium">{p.name}</div>
              {p.description && <div className="line-clamp-2 text-sm text-muted-foreground">{p.description}</div>}
              <div className="mt-2 text-xs text-muted-foreground">{p.chats} chats · {p.documents} documents · updated {ago(p.updated_at)}</div>
            </Link>
          ))}
        </div>
      )}
    </Shell>
  );
}

function ProjectView({ id }: { id: string }) {
  const router = useRouter();
  const [p, setP] = useState<Project | null>(null);
  const [draft, setDraft] = useState({ name: "", description: "", instructions: "" });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [pick, setPick] = useState("");
  const load = useCallback(() => api<Project>(`/api/projects/${id}`).then((x) => {
    setP(x); setDraft({ name: x.name, description: x.description, instructions: x.instructions });
  }).catch((e) => setError(e.message)), [id]);
  useEffect(() => { load(); api<Conversation[]>("/api/conversations").then(setConvs).catch(() => {}); }, [load]);

  async function save() {
    setError(null);
    try { await api(`/api/projects/${id}`, { method: "PATCH", body: draft }); setSaved(true); setTimeout(() => setSaved(false), 1500); load(); }
    catch (e: any) { setError(e.message); }
  }
  async function newChat() {
    const c = await api<{ id: string }>("/api/conversations", { method: "POST", body: { model_id: "auto", project_id: id } });
    router.push(`/chat/${c.id}`);
  }
  async function assign(kind: string, itemId: string, projectId: string | null) {
    try { await api("/api/projects/assign", { method: "POST", body: { kind, id: itemId, project_id: projectId } }); load(); }
    catch (e: any) { setError(e.message); }
  }
  async function remove() {
    if (!confirm("Delete this project? Its chats, documents and runs are kept but no longer grouped.")) return;
    await api(`/api/projects/${id}`, { method: "DELETE" });
    router.push("/projects");
  }

  if (!p) return <Shell title="Project" icon={FolderKanban}>{error ? <ErrorNote error={error} /> : <Loader2 className="animate-spin" />}</Shell>;
  const inProject = new Set((p.items?.conversation ?? []).map((c) => c.id));
  return (
    <Shell title={p.name} icon={FolderKanban} wide actions={<>
      <Button size="sm" onClick={newChat}><MessageSquarePlus /> New chat in project</Button>
      <Button size="sm" variant="ghost" title="Archive" onClick={() => api(`/api/projects/${id}`, { method: "PATCH", body: { archived: !p.archived } }).then(load)}><Archive /> {p.archived ? "Unarchive" : "Archive"}</Button>
      <Button size="sm" variant="ghost" title="Delete project" onClick={remove}><Trash2 /></Button>
    </>}>
      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="space-y-3 lg:col-span-2">
          <Field label="Name"><input className={inputCls} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
          <Field label="Description"><input className={inputCls} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <Field label="Instructions" hint="Added to every chat in this project."><textarea className={inputCls} rows={8} value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} /></Field>
          <ErrorNote error={error} />
          <Button onClick={save}>{saved ? "Saved" : "Save"}</Button>
        </Card>
        <div className="space-y-4 lg:col-span-3">
          <Card>
            <h3 className="mb-2 font-medium">Add an existing chat</h3>
            <div className="flex gap-2">
              <select className={inputCls} value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">Choose a chat…</option>
                {convs.filter((c) => !inProject.has(c.id)).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
              </select>
              <Button disabled={!pick} onClick={() => { assign("conversation", pick, id); setPick(""); }}>Add</Button>
            </div>
          </Card>
          {Object.entries(p.items ?? {}).map(([kind, items]) => items.length > 0 && (
            <Card key={kind}>
              <h3 className="mb-1 font-medium">{KIND_LABEL[kind] ?? kind} <span className="text-sm font-normal text-muted-foreground">({items.length})</span></h3>
              <ul className="divide-y">
                {items.map((it) => (
                  <li key={it.id} className="flex items-center gap-2 py-1.5 text-sm">
                    <Link href={it.url} className="line-clamp-1 flex-1 hover:underline">{it.title || "(untitled)"}</Link>
                    {it.status && <StatusPill status={it.status} />}
                    <span className="text-xs text-muted-foreground">{ago(it.created_at)}</span>
                    <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => assign(kind, it.id, null)}>Remove</button>
                  </li>
                ))}
              </ul>
            </Card>
          ))}
          {Object.values(p.items ?? {}).every((x) => x.length === 0) && <Card><p className="text-sm text-muted-foreground">Nothing in this project yet. Start a chat here, or pick a project when starting research or team runs from Home.</p></Card>}
        </div>
      </div>
    </Shell>
  );
}
