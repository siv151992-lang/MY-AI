"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, ChevronDown, FileText, Loader2, Paperclip, Route as RouteIcon, Sparkles, Square, Wand2, X } from "lucide-react";
import type { Skill } from "@/lib/hub";
import { VoiceButton } from "@/components/hub/VoiceButton";
import { uploadFile } from "@/lib/api";
import type { FileRef, ModelInfo } from "@/lib/types";
import { cn, uid } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

type Pending = { key: string; name: string; progress: number; file?: FileRef; error?: string; preview?: string };

const ACCEPT = ".pdf,.docx,.xlsx,.pptx,.csv,.tsv,.txt,.md,.json,.py,.js,.ts,.tsx,.jsx,.java,.kt,.go,.rs,.c,.cpp,.h,.cs,.rb,.php,.sql,.html,.css,.xml,.yaml,.yml,.toml,.sh,.log,image/png,image/jpeg,image/gif,image/webp,image/tiff,image/bmp";

export const AUTO_MODEL = "auto";

export function Composer({ models, modelId, onModelChange, onSend, onStop, busy, agentMode, onAgentMode, prefill, skills = [], skillId = null, onSkill }: {
  models: ModelInfo[];
  modelId: string | null;
  onModelChange: (id: string) => void;
  onSend: (text: string, files: FileRef[]) => void;
  onStop: () => void;
  busy: boolean;
  agentMode: boolean;
  onAgentMode: (v: boolean) => void;
  prefill?: { text: string; n: number };
  skills?: Skill[];
  skillId?: string | null;
  onSkill?: (id: string | null) => void;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [dragging, setDragging] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const model = models.find((m) => m.id === modelId);
  const skill = skills.find((s) => s.id === skillId);
  const uploading = pending.some((p) => !p.file && !p.error);
  const ready = pending.filter((p) => p.file).map((p) => p.file!);
  const canSend = !busy && !uploading && (text.trim().length > 0 || ready.length > 0);

  useEffect(() => {
    if (prefill) { setText(prefill.text); ta.current?.focus(); }
  }, [prefill]);

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 280) + "px";
  }, [text]);

  function addFiles(list: FileList | File[]) {
    for (const f of Array.from(list).slice(0, 10)) {
      const key = uid();
      const preview = f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined;
      setPending((p) => [...p, { key, name: f.name, progress: 0, preview }]);
      uploadFile(f, (pct) => setPending((p) => p.map((x) => (x.key === key ? { ...x, progress: pct } : x))))
        .then((rec) => setPending((p) => p.map((x) => (x.key === key ? { ...x, file: { id: rec.id, filename: rec.filename, kind: rec.kind } } : x))))
        .catch((e) => setPending((p) => p.map((x) => (x.key === key ? { ...x, error: e.message } : x))));
    }
  }

  function send() {
    if (!canSend) return;
    onSend(text.trim(), ready);
    setText("");
    pending.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
    setPending([]);
  }

  return (
    <div
      className={cn("relative rounded-2xl border bg-card shadow-[0_2px_12px_-4px_rgba(0,0,0,0.08)] transition-shadow focus-within:shadow-[0_4px_20px_-6px_rgba(0,0,0,0.15)]",
        dragging && "ring-2 ring-brand")}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); }}
    >
      {pending.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3 pt-3">
          {pending.map((p) => (
            <div key={p.key} className={cn("group relative flex items-center gap-2 rounded-xl border bg-background px-2.5 py-2 text-xs", p.error && "border-destructive/50")}>
              {p.preview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={p.preview} alt="" className="h-8 w-8 rounded-md object-cover" />
              ) : <FileText className="h-4 w-4 text-brand" />}
              <div className="max-w-[10rem]">
                <div className="truncate font-medium">{p.name}</div>
                <div className={cn("text-muted-foreground", p.error && "text-destructive")}>
                  {p.error ? p.error : p.file ? "Ready" : `Uploading ${p.progress}%`}
                </div>
              </div>
              {!p.file && !p.error && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
              <button title="Remove" onClick={() => setPending((x) => x.filter((y) => y.key !== p.key))}
                className="absolute -right-1.5 -top-1.5 rounded-full border bg-background p-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      {skill && (
        <div className="flex items-center gap-2 px-4 pt-3 text-xs">
          <span className="inline-flex items-center gap-1 rounded-full bg-brand/10 px-2 py-0.5 text-brand"><Wand2 className="h-3 w-3" /> Skill: {skill.name}</span>
          <span className="text-muted-foreground">applies to your next message</span>
          <button title="Remove skill" onClick={() => onSkill?.(null)}><X className="h-3 w-3" /></button>
        </div>
      )}
      <textarea
        ref={ta}
        rows={1}
        value={text}
        placeholder="How can I help you today?"
        onChange={(e) => setText(e.target.value)}
        onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
        }}
        className="thin-scroll block max-h-[280px] w-full resize-none bg-transparent px-4 pt-4 text-[15px] leading-6 outline-none placeholder:text-muted-foreground"
      />
      <div className="flex items-center gap-1 px-2.5 pb-2.5 pt-2">
        <input ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden"
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <Button variant="ghost" size="icon" title="Attach files" onClick={() => fileInput.current?.click()}>
          <Paperclip />
        </Button>
        <Button
          variant="ghost" size="sm" title="Agent mode: plan, use tools and self-check every answer"
          onClick={() => onAgentMode(!agentMode)}
          className={cn("rounded-full", agentMode && "bg-brand/10 text-brand hover:bg-brand/15 hover:text-brand")}
        >
          <Sparkles /> <span className="hidden sm:inline">Agent</span>
        </Button>
        {skills.length > 0 && onSkill && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" title="Apply a skill to this message"><Wand2 /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="max-h-80 w-64 overflow-y-auto">
              <DropdownMenuLabel>Skills</DropdownMenuLabel>
              {skills.map((s) => (
                <DropdownMenuItem key={s.id} selected={s.id === skillId} onSelect={() => onSkill(s.id)}>
                  <div><div>{s.name}</div><div className="text-xs text-muted-foreground">{s.description}</div></div>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        <VoiceButton onText={(v) => { setText((x) => (x ? x + " " : "") + v); ta.current?.focus(); }} />
        <div className="ml-auto flex items-center gap-1">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="max-w-[11rem] text-muted-foreground" disabled={!models.length}>
                {modelId === AUTO_MODEL && <RouteIcon />}
                <span className="truncate">{modelId === AUTO_MODEL && models.length ? "Auto" : model?.label ?? (models.length ? "Select model" : "No models configured")}</span>
                <ChevronDown />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuLabel>Model</DropdownMenuLabel>
              <DropdownMenuItem selected={modelId === AUTO_MODEL} onSelect={() => onModelChange(AUTO_MODEL)}>
                <div>
                  <div>Auto</div>
                  <div className="text-xs text-muted-foreground">Picks the best model for each message, with fallback</div>
                </div>
              </DropdownMenuItem>
              {models.map((m) => (
                <DropdownMenuItem key={m.id} selected={m.id === modelId} onSelect={() => onModelChange(m.id)}>
                  <div>
                    <div>{m.label}</div>
                    <div className="text-xs text-muted-foreground">
                      {m.provider}{m.vision ? " · vision" : ""}{m.tools ? " · tools" : ""}
                    </div>
                  </div>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          {busy ? (
            <Button size="icon" variant="secondary" title="Stop" onClick={onStop} className="rounded-xl">
              <Square className="fill-current !size-3.5" />
            </Button>
          ) : (
            <Button size="icon" title="Send" onClick={send} disabled={!canSend} className="rounded-xl">
              <ArrowUp />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
