"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, CircleDashed, Loader2, Pencil, Plug, Plus, Trash2, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { ago, type BuiltinIntegration, type Integration } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { Card, ErrorNote, Field, inputCls, Shell } from "./Shell";

type Draft = { id?: string; name: string; description: string; base_url: string; auth_type: string; auth_name: string;
  secret: string; allow_writes: boolean; test_path: string; has_secret?: boolean };
const EMPTY: Draft = { name: "", description: "", base_url: "https://", auth_type: "bearer", auth_name: "", secret: "", allow_writes: false, test_path: "" };

export function IntegrationsApp() {
  const [data, setData] = useState<{ custom: Integration[]; builtin: BuiltinIntegration[] } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const load = () => api<{ custom: Integration[]; builtin: BuiltinIntegration[] }>("/api/integrations").then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function save() {
    if (!draft) return;
    setError(null);
    const body = { ...draft, secret: draft.secret || null };
    try { await api(draft.id ? `/api/integrations/${draft.id}` : "/api/integrations", { method: draft.id ? "PUT" : "POST", body }); setDraft(null); load(); }
    catch (e: any) { setError(e.message); }
  }
  async function test(id: string) {
    setTesting(id);
    try { await api(`/api/integrations/${id}/test`, { method: "POST" }); } catch (e: any) { setError(e.message); }
    setTesting(null); load();
  }

  return (
    <Shell title="Integrations" icon={Plug} actions={<Button size="sm" onClick={() => setDraft({ ...EMPTY })}><Plus /> Connect an API</Button>}>
      <ErrorNote error={error} />
      <h2 className="mb-2 font-medium">Built-in services</h2>
      <p className="mb-3 text-sm text-muted-foreground">These are set up by whoever runs this Aria server. Anything marked "not configured" stays off; nothing pretends to work without it.</p>
      <div className="mb-6 grid gap-2 sm:grid-cols-2">
        {data?.builtin.map((b) => (
          <Card key={b.name} className="p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              {b.configured ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <CircleDashed className="h-4 w-4 text-muted-foreground" />}
              {b.name} <span className="ml-auto text-xs font-normal text-muted-foreground">{b.configured ? "configured" : "not configured"}</span>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">Enables: {b.enables}</div>
            {!b.configured && <div className="mt-1 text-xs text-muted-foreground">Needs: {b.needs}</div>}
          </Card>
        ))}
      </div>

      <h2 className="mb-2 font-medium">Your API connections</h2>
      <p className="mb-3 text-sm text-muted-foreground">Connect any HTTP/JSON API once. It becomes a tool (<code>api_name</code>) that chats, skills, custom agents and automations can call. Credentials are encrypted and never shown again. Read requests (GET) are always allowed; write requests only run in automations and only if you allow writes.</p>
      {draft && (
        <Card className="mb-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name"><input className={inputCls} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus /></Field>
            <Field label="Base URL" hint="Public https address, e.g. https://api.github.com"><input className={inputCls} value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} /></Field>
          </div>
          <Field label="What it's for" hint="Helps the AI decide when to use it."><input className={inputCls} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Authentication">
              <select className={inputCls} value={draft.auth_type} onChange={(e) => setDraft({ ...draft, auth_type: e.target.value })}>
                <option value="none">None</option><option value="bearer">Bearer token</option><option value="header">Custom header</option>
                <option value="query">Query parameter</option><option value="basic">Basic (user:password)</option>
              </select>
            </Field>
            {(draft.auth_type === "header" || draft.auth_type === "query") && (
              <Field label={draft.auth_type === "header" ? "Header name" : "Parameter name"}><input className={inputCls} value={draft.auth_name} placeholder={draft.auth_type === "header" ? "X-API-Key" : "api_key"} onChange={(e) => setDraft({ ...draft, auth_name: e.target.value })} /></Field>
            )}
            {draft.auth_type !== "none" && (
              <Field label="Credential" hint={draft.has_secret ? "Leave empty to keep the saved one." : undefined}>
                <input className={inputCls} type="password" autoComplete="off" value={draft.secret} onChange={(e) => setDraft({ ...draft, secret: e.target.value })} />
              </Field>
            )}
          </div>
          <Field label="Test path" hint="A GET path used by 'Test', e.g. /user"><input className={inputCls} value={draft.test_path} onChange={(e) => setDraft({ ...draft, test_path: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.allow_writes} onChange={(e) => setDraft({ ...draft, allow_writes: e.target.checked })} /> Allow write requests (POST/PUT/PATCH/DELETE) in automations</label>
          <div className="flex gap-2"><Button disabled={!draft.name.trim()} onClick={save}>Save</Button><Button variant="ghost" onClick={() => setDraft(null)}>Cancel</Button></div>
        </Card>
      )}
      {!data ? <Loader2 className="animate-spin text-muted-foreground" /> : data.custom.length === 0 && !draft ? (
        <Card><p className="text-sm text-muted-foreground">No API connections yet.</p></Card>
      ) : (
        <div className="space-y-2">
          {data.custom.map((i) => (
            <Card key={i.id} className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{i.name} <span className="font-mono text-xs text-muted-foreground">{i.tool_name}</span></div>
                <div className="text-xs text-muted-foreground">{i.base_url} · {i.auth_type === "none" ? "no auth" : `${i.auth_type} auth`}{i.allow_writes ? " · writes allowed in automations" : " · read-only"}</div>
                {i.last_test && <div className={`text-xs ${i.last_test.ok ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}>Last test {ago(i.last_test.at)}: {i.last_test.ok ? `HTTP ${i.last_test.status}` : i.last_test.error ?? `HTTP ${i.last_test.status}`}</div>}
              </div>
              <Button size="sm" variant="outline" disabled={testing === i.id} onClick={() => test(i.id)}>{testing === i.id ? <Loader2 className="animate-spin" /> : <Zap />} Test</Button>
              <Button size="sm" variant="ghost" title="Edit" onClick={() => setDraft({ id: i.id, name: i.name, description: i.description, base_url: i.base_url, auth_type: i.auth_type, auth_name: i.auth_name, secret: "", allow_writes: i.allow_writes, test_path: i.test_path, has_secret: i.has_secret })}><Pencil /></Button>
              <Button size="sm" variant="ghost" title="Delete" onClick={() => confirm(`Remove "${i.name}"? Automations using it will fail.`) && api(`/api/integrations/${i.id}`, { method: "DELETE" }).then(load)}><Trash2 /></Button>
            </Card>
          ))}
        </div>
      )}
    </Shell>
  );
}
