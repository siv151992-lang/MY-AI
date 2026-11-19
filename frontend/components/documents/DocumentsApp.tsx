"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle, ArrowLeft, Check, Download, FileSpreadsheet, FileText, FileType2, GitCompare, Image as ImageIcon, Loader2,
  MessageSquareText, Presentation, RefreshCw, ScanText, Share2, Sparkles, Table2, Trash2, Upload, Users, Wand2, X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fmtSize, linkPassages, type Citation, type Doc, type DocDetail, type DocTable, type ExtractedField } from "@/lib/documents";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Markdown } from "@/components/chat/Markdown";

const H = { "X-Requested-With": "aria" };
const TYPE_ICON: Record<string, any> = { pdf: FileText, docx: FileType2, xlsx: FileSpreadsheet, csv: FileSpreadsheet, pptx: Presentation, image: ImageIcon, txt: FileText };
type Mode = { kind: "doc"; id: string } | { kind: "ask" } | { kind: "compare" } | { kind: "generate" };

export function DocumentsApp({ initialId }: { initialId: string | null }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>(initialId ? { kind: "doc", id: initialId } : { kind: "ask" });
  const [uploads, setUploads] = useState<{ name: string; state: "uploading" | "done" | "error"; msg?: string }[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => api<Doc[]>("/api/documents").then(setDocs).catch(() => setDocs([])), []);
  useEffect(() => { load(); }, [load]);

  const open = (m: Mode) => {
    setMode(m);
    window.history.replaceState(null, "", m.kind === "doc" ? `/documents/${m.id}` : "/documents");
  };

  async function uploadFiles(files: FileList | File[]) {
    for (const f of Array.from(files)) {
      setUploads((u) => [...u, { name: f.name, state: "uploading" }]);
      const fd = new FormData();
      fd.append("file", f);
      try {
        const res = await fetch("/api/documents", { method: "POST", body: fd, credentials: "same-origin", headers: H });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new ApiError(body.detail ?? "Upload failed", res.status);
        setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "done", msg: body.warnings?.join(" ") } : x)));
      } catch (e: any) {
        setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "error", msg: e.message } : x)));
      }
      load();
    }
  }

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const selDocs = (docs ?? []).filter((d) => selected.includes(d.id));

  return (
    <div className="flex h-dvh overflow-hidden"
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); }}>
      <aside className="flex w-80 shrink-0 flex-col border-r bg-muted/30">
        <div className="flex h-12 items-center gap-2 px-3">
          <Button variant="ghost" size="icon-sm" asChild title="Back to chat"><Link href="/chat"><ArrowLeft /></Link></Button>
          <FileText className="h-5 w-5 text-brand" /><span className="font-serif text-lg">Documents</span>
        </div>
        <div className="space-y-2 px-3">
          <Button className="w-full" onClick={() => input.current?.click()}><Upload /> Upload files</Button>
          <input ref={input} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) uploadFiles(e.target.files); e.target.value = ""; }}
            accept=".pdf,.docx,.xlsx,.pptx,.csv,.tsv,.txt,.md,.json,image/*" />
          <p className="text-xs text-muted-foreground">PDF, DOCX, XLSX, CSV, PPTX, text and images (OCR). Private to you unless you share.</p>
          {uploads.slice(-4).map((u, i) => (
            <div key={i} className={cn("flex items-start gap-1.5 rounded-md px-2 py-1 text-xs", u.state === "error" ? "bg-destructive/10 text-destructive" : "bg-background")}>
              {u.state === "uploading" ? <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin" /> : u.state === "done" ? <Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" /> : <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />}
              <span className="min-w-0"><span className="block truncate">{u.name}</span>{u.msg && <span className="block">{u.msg}</span>}</span>
            </div>
          ))}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-1 px-3">
          <ModeBtn active={mode.kind === "ask"} onClick={() => open({ kind: "ask" })} icon={MessageSquareText} label="Ask" />
          <ModeBtn active={mode.kind === "compare"} onClick={() => open({ kind: "compare" })} icon={GitCompare} label="Compare" />
          <ModeBtn active={mode.kind === "generate"} onClick={() => open({ kind: "generate" })} icon={Wand2} label="Generate" />
        </div>
        <p className="px-3 pt-2 text-xs text-muted-foreground">{selected.length ? `${selected.length} selected` : "Tick documents to scope Ask, Compare and Generate"}
          {selected.length > 0 && <button className="ml-2 underline" onClick={() => setSelected([])}>clear</button>}</p>
        <nav className="thin-scroll mt-1 flex-1 overflow-y-auto px-2 pb-4">
          {docs === null ? <Loader2 className="mx-auto mt-6 h-5 w-5 animate-spin text-muted-foreground" /> : docs.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">No documents yet. Upload or drop files here.</p>
          ) : docs.map((d) => {
            const Icon = TYPE_ICON[d.doc_type ?? "txt"] ?? FileText;
            const active = mode.kind === "doc" && mode.id === d.id;
            return (
              <div key={d.id} className={cn("group flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-accent", active && "bg-accent")}>
                <input type="checkbox" checked={selected.includes(d.id)} onChange={() => toggle(d.id)} className="mt-1" title="Select" />
                <button className="flex min-w-0 flex-1 items-start gap-2 text-left" onClick={() => open({ kind: "doc", id: d.id })}>
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="block truncate">{d.filename}</span>
                    <span className="flex flex-wrap gap-1 text-[11px] text-muted-foreground">
                      {fmtSize(d.size_bytes)}{pageLabel(d)}{d.tables ? ` · ${d.tables} table${d.tables === 1 ? "" : "s"}` : ""}
                      {d.ocr && <Badge>OCR</Badge>}{d.meta?.generated && <Badge>generated</Badge>}
                      {!d.owned && <Badge>shared by {d.owner_name}</Badge>}{d.owned && d.shared_with > 0 && <Badge>shared</Badge>}
                    </span>
                  </span>
                </button>
              </div>
            );
          })}
        </nav>
      </aside>
      <main className={cn("thin-scroll min-w-0 flex-1 overflow-y-auto", drag && "bg-brand/5 outline-dashed outline-2 outline-brand/40")}>
        {mode.kind === "doc" && <DocView key={mode.id} id={mode.id} onChanged={load} onGone={() => { load(); open({ kind: "ask" }); }} />}
        {mode.kind === "ask" && <AskView selected={selDocs} />}
        {mode.kind === "compare" && <CompareView selected={selDocs} />}
        {mode.kind === "generate" && <GenerateView selected={selDocs} onCreated={(id) => { load(); open({ kind: "doc", id }); }} />}
      </main>
    </div>
  );
}

function pageLabel(d: Doc): string {
  if (!d.pages || d.doc_type === "image") return "";
  const unit = d.doc_type === "pptx" ? "slide" : "page";
  return ` · ${d.pages} ${unit}${d.pages === 1 ? "" : "s"}`;
}

function ModeBtn({ active, onClick, icon: Icon, label }: { active: boolean; onClick: () => void; icon: any; label: string }) {
  return <button onClick={onClick} className={cn("flex flex-col items-center gap-0.5 rounded-lg border py-1.5 text-xs", active ? "border-brand bg-brand/5" : "hover:bg-accent")}><Icon className="h-4 w-4" />{label}</button>;
}
function Badge({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-muted px-1">{children}</span>;
}
function Scope({ selected, min = 0 }: { selected: Doc[]; min?: number }) {
  return <p className="text-xs text-muted-foreground">{selected.length ? `Using: ${selected.map((d) => d.filename).join(", ")}` : min ? `Select at least ${min} documents in the list.` : "Searching all documents you have access to (tick some to narrow it)."}</p>;
}
function Citations({ cites }: { cites: Citation[] }) {
  if (!cites.length) return null;
  return (
    <div className="space-y-1.5">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Sources</h3>
      {cites.map((c) => (
        <div key={c.n} id={`cite-${c.n}`} className="rounded-lg border p-2 text-xs">
          <p className="font-medium"><span className="mr-1 font-mono text-brand">P{c.n}</span><Link href={`/documents/${c.file_id}`} className="hover:underline">{c.filename}</Link>{c.location && <span className="text-muted-foreground"> · {c.location}</span>}</p>
          <p className="mt-0.5 line-clamp-4 whitespace-pre-wrap text-muted-foreground">{c.snippet}</p>
        </div>
      ))}
    </div>
  );
}
function useRun<T>() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<T | null>(null);
  const run = async (fn: () => Promise<T>) => { setBusy(true); setError(null); try { setData(await fn()); } catch (e: any) { setError(e.message); } finally { setBusy(false); } };
  return { busy, error, data, run, setData };
}

function AskView({ selected }: { selected: Doc[] }) {
  const [q, setQ] = useState("");
  const r = useRun<{ answer: string; citations: Citation[]; passages: number; removed_citations: string[] }>();
  return (
    <div className="mx-auto max-w-3xl space-y-4 px-4 py-8">
      <h1 className="font-serif text-2xl">Ask your documents</h1>
      <Scope selected={selected} />
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim()) r.run(() => api("/api/documents/ask", { method: "POST", body: { question: q, document_ids: selected.map((d) => d.id) } })); }}>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. What are the payment terms and penalties?" />
        <Button disabled={r.busy || !q.trim()}>{r.busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Ask</Button>
      </form>
      {r.error && <p className="text-sm text-destructive">{r.error}</p>}
      {r.data && (
        <div className="space-y-4">
          <div className="answer text-[15px]"><Markdown content={linkPassages(r.data.answer, r.data.citations)} /></div>
          {r.data.removed_citations.length > 0 && <p className="text-xs text-amber-700 dark:text-amber-400">Statements marked [unsupported] cited passages that weren't retrieved; treat them with caution.</p>}
          <p className="text-xs text-muted-foreground">Answered from {r.data.passages} retrieved passages.</p>
          <Citations cites={r.data.citations} />
        </div>
      )}
    </div>
  );
}

function CompareView({ selected }: { selected: Doc[] }) {
  const [focus, setFocus] = useState("");
  const r = useRun<{ comparison: string; citations: Citation[] }>();
  return (
    <div className="mx-auto max-w-4xl space-y-4 px-4 py-8">
      <h1 className="font-serif text-2xl">Compare documents</h1>
      <Scope selected={selected} min={2} />
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); r.run(() => api("/api/documents/compare", { method: "POST", body: { document_ids: selected.map((d) => d.id), focus } })); }}>
        <Input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="Focus (optional), e.g. pricing, termination and liability" />
        <Button disabled={r.busy || selected.length < 2 || selected.length > 6}>{r.busy ? <Loader2 className="animate-spin" /> : <GitCompare />} Compare</Button>
      </form>
      {r.error && <p className="text-sm text-destructive">{r.error}</p>}
      {r.data && <><div className="answer text-[15px]"><Markdown content={linkPassages(r.data.comparison, r.data.citations)} /></div><Citations cites={r.data.citations} /></>}
    </div>
  );
}

function GenerateView({ selected, onCreated }: { selected: Doc[]; onCreated: (id: string) => void }) {
  const [prompt, setPrompt] = useState("");
  const [format, setFormat] = useState("docx");
  const r = useRun<Doc>();
  return (
    <div className="mx-auto max-w-3xl space-y-4 px-4 py-8">
      <h1 className="font-serif text-2xl">Generate a document</h1>
      <p className="text-xs text-muted-foreground">{selected.length ? `Grounded in: ${selected.map((d) => d.filename).join(", ")} (sources are cited and recorded).` : "Tick documents to ground the new file in them; otherwise it is written from your instructions alone."}</p>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} placeholder="e.g. A one-page executive brief of the attached contracts with a table of key dates and amounts"
        className="w-full resize-none rounded-xl border bg-card px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring" />
      <div className="flex items-center gap-2">
        {(["docx", "pdf", "xlsx", "pptx", "md"] as const).map((f) => (
          <button key={f} onClick={() => setFormat(f)} className={cn("rounded-lg border px-3 py-1.5 text-sm uppercase", format === f ? "border-brand bg-brand/5" : "hover:bg-accent")}>{f}</button>
        ))}
        <Button className="ml-auto" disabled={r.busy || prompt.trim().length < 5}
          onClick={() => r.run(async () => { const d = await api<Doc>("/api/documents/generate", { method: "POST", body: { prompt, format, source_ids: selected.map((x) => x.id) } }); onCreated(d.id); return d; })}>
          {r.busy ? <Loader2 className="animate-spin" /> : <Wand2 />} Generate</Button>
      </div>
      {r.error && <p className="text-sm text-destructive">{r.error}</p>}
    </div>
  );
}

function DocView({ id, onChanged, onGone }: { id: string; onChanged: () => void; onGone: () => void }) {
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [tab, setTab] = useState<"overview" | "content" | "tables" | "extract" | "sharing">("overview");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api<DocDetail>(`/api/documents/${id}`).then(setDoc).catch((e) => setError(e.status === 404 ? "This document doesn't exist or isn't shared with you." : e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  if (!doc) return <div className="p-10 text-center text-sm text-muted-foreground">{error ?? <Loader2 className="mx-auto h-5 w-5 animate-spin" />}</div>;

  const remove = async () => {
    if (!confirm(doc.owned ? `Delete "${doc.filename}" permanently${doc.shared_with ? ` (also for the ${doc.shared_with} people it's shared with)` : ""}?` : `Remove "${doc.filename}" from your library? The owner keeps it.`)) return;
    await api(`/api/documents/${id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    onGone();
  };
  const tabs = [["overview", "Overview"], ["content", "Content"], ["tables", `Tables (${doc.tables_list.length})`], ["extract", "Extract data"], ["sharing", doc.owned ? "Sharing" : "Access"]] as const;
  return (
    <div className="mx-auto max-w-5xl space-y-4 px-4 py-6">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="truncate font-serif text-2xl">{doc.filename}</h1>
          <p className="text-sm text-muted-foreground">{(doc.doc_type ?? "").toUpperCase()} · {fmtSize(doc.size_bytes)}{pageLabel(doc)} · {doc.chunks} indexed passages
            {!doc.owned && <> · shared with you by {doc.owner_name} (read-only)</>}</p>
        </div>
        <Button variant="outline" size="sm" asChild><a href={`/api/documents/${id}/download`} download><Download /> Download</a></Button>
        {doc.owned && <Button variant="ghost" size="icon-sm" title="Re-run extraction and indexing" onClick={() => api(`/api/documents/${id}/reindex`, { method: "POST" }).then(load).catch((e) => setError(e.message))}><RefreshCw /></Button>}
        <Button variant="ghost" size="icon-sm" title={doc.owned ? "Delete" : "Remove from my library"} onClick={remove}><Trash2 /></Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {(doc.warnings.length > 0 || doc.ocr) && (
        <div className="space-y-1 rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs">
          {doc.ocr && <p className="flex items-center gap-1.5"><ScanText className="h-3.5 w-3.5" /> Text was read with OCR{doc.meta?.ocr_pages ? ` (${doc.meta.ocr_pages} page${doc.meta.ocr_pages === 1 ? "" : "s"})` : ""}; check important figures against the original.</p>}
          {doc.warnings.map((w, i) => <p key={i} className="flex items-start gap-1.5"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{w}</p>)}
        </div>
      )}
      <div className="flex gap-1 border-b">
        {tabs.map(([t, label]) => <button key={t} onClick={() => setTab(t)} className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-brand font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>{label}</button>)}
      </div>
      {tab === "overview" && <Overview doc={doc} onSummary={load} />}
      {tab === "content" && <Content id={id} type={doc.doc_type} />}
      {tab === "tables" && <Tables id={id} tables={doc.tables_list} />}
      {tab === "extract" && <Extract id={id} />}
      {tab === "sharing" && <Sharing doc={doc} onChanged={() => { load(); onChanged(); }} />}
    </div>
  );
}

function Overview({ doc, onSummary }: { doc: DocDetail; onSummary: () => void }) {
  const [style, setStyle] = useState<"brief" | "bullets" | "detailed">("brief");
  const r = useRun<{ summary: string }>();
  const meta = Object.entries(doc.meta ?? {}).filter(([k]) => !["sheets", "sources", "generated", "prompt", "ocr_pages"].includes(k));
  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium">Summary</h2>
          <select value={style} onChange={(e) => setStyle(e.target.value as any)} className="ml-auto h-8 rounded-md border bg-background px-2 text-xs">
            <option value="brief">Brief</option><option value="bullets">Bullet points</option><option value="detailed">Detailed</option>
          </select>
          <Button size="sm" variant="outline" disabled={r.busy} onClick={() => r.run(async () => { const s = await api<{ summary: string }>(`/api/documents/${doc.id}/summarize`, { method: "POST", body: { style, force: true } }); if (style === "brief") onSummary(); return s; })}>
            {r.busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Summarise</Button>
        </div>
        {r.error && <p className="text-sm text-destructive">{r.error}</p>}
        <div className="answer rounded-xl border bg-card/50 p-3 text-sm">
          {r.data?.summary || doc.summary ? <Markdown content={r.data?.summary || doc.summary || ""} /> : <p className="text-muted-foreground">{doc.chunks ? "Summary is being prepared… (or click Summarise)" : "No readable text."}</p>}
        </div>
      </section>
      {doc.meta?.sheets && (
        <section><h2 className="mb-1 text-sm font-medium">Sheets</h2>
          <ul className="text-sm">{doc.meta.sheets.map((s: any) => <li key={s.name}>{s.name}: {s.rows} rows × {s.columns} columns{s.formulas ? `, ${s.formulas} formulas` : ""}</li>)}</ul></section>
      )}
      {doc.meta?.generated && (
        <section className="text-sm"><h2 className="mb-1 font-medium">Generated by Aria</h2><p className="text-muted-foreground">Prompt: {doc.meta.prompt}</p>
          {doc.meta.sources?.length > 0 && <ul className="mt-1 text-xs">{doc.meta.sources.map((s: any, i: number) => <li key={i}>Source: <Link className="underline" href={`/documents/${s.id}`}>{s.filename}</Link>{s.location && ` (${s.location})`}</li>)}</ul>}</section>
      )}
      {meta.length > 0 && <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">{meta.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt><dd>{String(v)}</dd></div>)}</dl>}
    </div>
  );
}

function Content({ id, type }: { id: string; type: string | null }) {
  const [chunks, setChunks] = useState<{ idx: number; content: string; page: number | null; section: string }[] | null>(null);
  useEffect(() => { api<{ chunks: any[] }>(`/api/documents/${id}/content?limit=400`).then((r) => setChunks(r.chunks)).catch(() => setChunks([])); }, [id]);
  if (!chunks) return <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />;
  if (!chunks.length) return <p className="text-sm text-muted-foreground">No text was extracted from this document.</p>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">The indexed text, in the passages used for search and question answering.</p>
      {chunks.map((c) => (
        <div key={c.idx} className="rounded-lg border p-2.5 text-sm">
          {(c.page || c.section) && <p className="mb-1 text-xs text-muted-foreground">{c.page ? (type === "pptx" ? `Slide ${c.page}` : `Page ${c.page}`) : ""}{c.page && c.section ? " · " : ""}{c.section}</p>}
          <p className="whitespace-pre-wrap">{c.content}</p>
        </div>
      ))}
    </div>
  );
}

function Tables({ id, tables }: { id: string; tables: DocTable[] }) {
  const [active, setActive] = useState(tables[0]?.id ?? null);
  const [page, setPage] = useState<{ rows: any[][]; offset: number } | null>(null);
  const [q, setQ] = useState("");
  const r = useRun<{ ok: boolean; answer: string; code: string; output: string; error: string; note: string }>();
  const t = tables.find((x) => x.id === active);
  useEffect(() => { if (active) api<{ rows: any[][]; offset: number }>(`/api/documents/${id}/tables/${active}?limit=50`).then(setPage).catch(() => {}); }, [id, active]);
  const go = (offset: number) => api<{ rows: any[][]; offset: number }>(`/api/documents/${id}/tables/${active}?offset=${offset}&limit=50`).then(setPage);
  if (!tables.length) return <p className="text-sm text-muted-foreground">No tables were found in this document.</p>;
  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded-xl border p-3">
        <h2 className="flex items-center gap-1.5 text-sm font-medium"><Table2 className="h-4 w-4" /> Analyse the data</h2>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim()) r.run(() => api(`/api/documents/${id}/analyze`, { method: "POST", body: { question: q } })); }}>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Which region grew most from Q1 to Q2? Show totals per region." />
          <Button disabled={r.busy || !q.trim()}>{r.busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Analyse</Button>
        </form>
        <p className="text-xs text-muted-foreground">Aria writes pandas code and runs it over all rows in the sandbox; the code and raw output are shown so you can check the numbers.</p>
        {r.error && <p className="text-sm text-destructive">{r.error}</p>}
        {r.data && (
          <div className="space-y-2 text-sm">
            <div className={cn("rounded-lg p-2.5", r.data.ok ? "bg-emerald-500/10" : "bg-destructive/10")}><Markdown content={r.data.answer} /></div>
            {r.data.note && <p className="text-xs text-amber-700">{r.data.note}</p>}
            <details><summary className="cursor-pointer text-xs text-muted-foreground">Output</summary><pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">{r.data.output || r.data.error}</pre></details>
            <details><summary className="cursor-pointer text-xs text-muted-foreground">Code</summary><pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">{r.data.code}</pre></details>
          </div>
        )}
      </section>
      <div className="flex flex-wrap gap-1">
        {tables.map((x) => <button key={x.id} onClick={() => setActive(x.id)} className={cn("rounded-lg border px-2.5 py-1 text-xs", active === x.id ? "border-brand bg-brand/5" : "hover:bg-accent")}>{x.name} <span className="text-muted-foreground">({x.total_rows}×{x.columns.length})</span></button>)}
      </div>
      {t && (
        <>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">{t.location}{t.formulas ? ` · ${t.formulas} formula cells` : ""}
            <a className="ml-auto underline" href={`/api/documents/${id}/tables/${t.id}/csv`}>Download CSV</a></div>
          <div className="thin-scroll overflow-x-auto rounded-lg border">
            <table className="w-full text-xs">
              <thead className="bg-muted/50"><tr>{t.profile.columns.map((c) => (
                <th key={c.name} className="whitespace-nowrap p-2 text-left align-top font-medium">{c.name}
                  <span className="block font-normal text-muted-foreground">{c.type}{c.type === "number" && c.mean !== undefined ? ` · avg ${c.mean} · Σ ${c.sum}` : ""}{c.empty ? ` · ${c.empty} empty` : ""}</span></th>
              ))}</tr></thead>
              <tbody>{page?.rows.map((row, i) => <tr key={i} className="border-t">{row.map((v, j) => <td key={j} className="whitespace-nowrap p-2">{v === null ? "" : String(v)}</td>)}</tr>)}</tbody>
            </table>
          </div>
          {page && t.total_rows > 50 && (
            <div className="flex items-center gap-2 text-xs">
              <Button size="sm" variant="ghost" disabled={page.offset === 0} onClick={() => go(Math.max(0, page.offset - 50))}>Previous</Button>
              <span>rows {page.offset + 1}–{Math.min(page.offset + 50, t.total_rows)} of {t.total_rows}</span>
              <Button size="sm" variant="ghost" disabled={page.offset + 50 >= Math.min(t.total_rows, t.profile.analysed_rows)} onClick={() => go(page.offset + 50)}>Next</Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Extract({ id }: { id: string }) {
  const [presets, setPresets] = useState<Record<string, { name: string; description: string }[]>>({});
  const [preset, setPreset] = useState("");
  const [fields, setFields] = useState<{ name: string; description: string }[]>([{ name: "", description: "" }]);
  const r = useRun<{ fields: ExtractedField[] }>();
  useEffect(() => { api<typeof presets>("/api/documents/presets").then(setPresets).catch(() => {}); }, []);
  const body = () => (preset ? { preset } : { fields: fields.filter((f) => f.name.trim()) });
  const download = async (format: "csv" | "json") => {
    const res = await fetch(`/api/documents/${id}/extract?format=${format}`, { method: "POST", credentials: "same-origin", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify(body()) });
    const blob = format === "json" ? new Blob([JSON.stringify(await res.json(), null, 2)], { type: "application/json" }) : await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `extraction.${format}`;
    a.click();
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>Template:</span>
        <button onClick={() => setPreset("")} className={cn("rounded-lg border px-2.5 py-1", !preset ? "border-brand bg-brand/5" : "hover:bg-accent")}>Custom fields</button>
        {Object.keys(presets).map((p) => <button key={p} onClick={() => setPreset(p)} className={cn("rounded-lg border px-2.5 py-1 capitalize", preset === p ? "border-brand bg-brand/5" : "hover:bg-accent")}>{p.replace("_", " ")}</button>)}
      </div>
      {!preset ? (
        <div className="space-y-1.5">
          {fields.map((f, i) => (
            <div key={i} className="flex gap-2">
              <Input value={f.name} onChange={(e) => setFields((fs) => fs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} placeholder="Field name, e.g. renewal_date" className="max-w-[14rem]" />
              <Input value={f.description} onChange={(e) => setFields((fs) => fs.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} placeholder="What it means (optional)" />
              <Button variant="ghost" size="icon" onClick={() => setFields((fs) => fs.filter((_, j) => j !== i))}><X /></Button>
            </div>
          ))}
          <Button variant="ghost" size="sm" onClick={() => setFields((fs) => [...fs, { name: "", description: "" }])}>+ Add field</Button>
        </div>
      ) : <p className="text-xs text-muted-foreground">Fields: {presets[preset]?.map((f) => f.name).join(", ")}</p>}
      <div className="flex gap-2">
        <Button disabled={r.busy || (!preset && !fields.some((f) => f.name.trim()))} onClick={() => r.run(() => api(`/api/documents/${id}/extract`, { method: "POST", body: body() }))}>
          {r.busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Extract</Button>
        {r.data && <><Button variant="outline" onClick={() => download("csv")}><Download /> CSV</Button><Button variant="outline" onClick={() => download("json")}><Download /> JSON</Button></>}
      </div>
      {r.error && <p className="text-sm text-destructive">{r.error}</p>}
      {r.data && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs"><tr><th className="p-2">Field</th><th className="p-2">Value</th><th className="p-2">Status</th><th className="p-2">Source text</th></tr></thead>
            <tbody>{r.data.fields.map((f) => (
              <tr key={f.field} className="border-t align-top">
                <td className="p-2 font-mono text-xs">{f.field}</td>
                <td className="p-2">{f.value === null ? <span className="text-muted-foreground">—</span> : typeof f.value === "object" ? <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(f.value, null, 1)}</pre> : String(f.value)}</td>
                <td className="p-2 text-xs">{f.status === "verified" ? <span className="text-emerald-700 dark:text-emerald-400">✓ verified</span> : f.status === "unverified" ? <span title="The value's supporting text could not be found in the document" className="text-amber-700 dark:text-amber-400">⚠ unverified</span> : <span className="text-muted-foreground">not found</span>}</td>
                <td className="p-2 text-xs italic text-muted-foreground">{f.quote ? `“${f.quote}”` : ""}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Sharing({ doc, onChanged }: { doc: DocDetail; onChanged: () => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!doc.owned) return (
    <p className="flex items-start gap-2 text-sm"><Users className="mt-0.5 h-4 w-4" /> {doc.owner_name} shared this document with you as a viewer. You can read, search, ask questions about and analyse it; only the owner can change, share or delete it.</p>
  );
  const share = () => api(`/api/documents/${doc.id}/access`, { method: "POST", body: { email } }).then(() => { setEmail(""); setError(null); onChanged(); }).catch((e) => setError(e.message));
  const revoke = (uid: string) => api(`/api/documents/${doc.id}/access/${uid}`, { method: "DELETE" }).then(onChanged).catch((e) => setError(e.message));
  return (
    <div className="max-w-xl space-y-3 text-sm">
      <p className="text-muted-foreground">Only you can see this document. Share it read-only with other Aria users: they can read, search, ask about and analyse it, but can't edit, reshare or delete it.</p>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (email.trim()) share(); }}>
        <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="colleague@example.com" />
        <Button disabled={!email.trim()}><Share2 /> Share</Button>
      </form>
      {error && <p className="text-destructive">{error}</p>}
      {(doc.access ?? []).length === 0 ? <p className="text-xs text-muted-foreground">Not shared with anyone.</p> : (doc.access ?? []).map((g) => (
        <div key={g.user_id} className="flex items-center gap-2 rounded-lg border px-3 py-2">
          <span className="flex-1">{g.name} <span className="text-muted-foreground">{g.email}</span></span><span className="text-xs text-muted-foreground">{g.role}</span>
          <Button size="sm" variant="ghost" onClick={() => revoke(g.user_id)}>Revoke</Button>
        </div>
      ))}
    </div>
  );
}
