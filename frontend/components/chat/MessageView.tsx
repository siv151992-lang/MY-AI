"use client";

import { useState } from "react";
import { AlertCircle, Check, Copy, FileText, RotateCcw } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import { cn, copyText } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { AgentActivity } from "./AgentActivity";
import { Markdown } from "./Markdown";
import { Logo } from "./Logo";
import { MemorySuggestions, MemoryUsed } from "./MemoryNotes";
import { RouteBadge } from "@/components/hub/RouteBadge";
import { SpeakButton } from "@/components/hub/VoiceButton";

export function MessageView({ m, isLast, onRegenerate, busy, onOpenMemory, prompt }: {
  m: ChatMessage; isLast: boolean; onRegenerate: () => void; busy: boolean; prompt?: string;
  onOpenMemory?: (tab?: "active" | "pending" | "chat") => void;
}) {
  const [copied, setCopied] = useState(false);

  if (m.role === "user") {
    return (
      <div className="flex flex-col items-end gap-2 animate-fade-up">
        {m.files && m.files.length > 0 && (
          <div className="flex flex-wrap justify-end gap-2">
            {m.files.map((f) =>
              f.kind === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={f.id} src={`/api/files/${f.id}/content`} alt={f.filename} className="h-28 max-w-[14rem] rounded-xl border object-cover" />
              ) : (
                <a key={f.id} href={`/api/files/${f.id}/content`} target="_blank" className="flex items-center gap-2 rounded-xl border bg-card px-3 py-2 text-sm hover:bg-accent">
                  <FileText className="h-4 w-4 text-brand" /> <span className="max-w-[12rem] truncate">{f.filename}</span>
                </a>
              ),
            )}
          </div>
        )}
        {m.content && (
          <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-secondary px-4 py-2.5 text-[15px] leading-7">
            {m.content}
          </div>
        )}
      </div>
    );
  }

  const empty = !m.content && m.streaming;
  return (
    <div className="group flex gap-3 animate-fade-up sm:gap-4">
      <div className="mt-1 hidden shrink-0 sm:block"><Logo className={cn("h-6 w-6", m.streaming && "animate-spin [animation-duration:3s]")} /></div>
      <div className="min-w-0 flex-1">
        {m.memory && <MemoryUsed memory={m.memory} onOpen={() => onOpenMemory?.(m.memory?.compacted || m.memory?.recalled ? "chat" : "active")} />}
        <AgentActivity m={m} />
        {empty && !m.status && (
          <div className="flex gap-1 py-3">
            {[0, 1, 2].map((i) => (
              <span key={i} className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: `${i * 0.15}s` }} />
            ))}
          </div>
        )}
        {m.content && (
          <div className={cn("answer text-[15px]", m.streaming && "streaming")}>
            <Markdown content={m.content} />
          </div>
        )}
        {m.error && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> <span>{m.error}</span>
          </div>
        )}
        {m.interrupted && !m.streaming && <p className="mt-2 text-xs text-muted-foreground">Response stopped.</p>}
        {m.route && !m.streaming && <RouteBadge route={m.route} prompt={prompt} />}
        {m.suggestions && m.suggestions.length > 0 && <MemorySuggestions items={m.suggestions} onOpen={() => onOpenMemory?.("pending")} />}
        {!m.streaming && (m.content || m.error) && (
          <div className={cn("mt-2 flex items-center gap-1 text-muted-foreground transition-opacity", isLast ? "opacity-100" : "opacity-0 group-hover:opacity-100")}>
            {m.content && (
              <Button variant="ghost" size="icon-sm" title="Copy response"
                onClick={async () => { if (await copyText(m.content)) { setCopied(true); setTimeout(() => setCopied(false), 1500); } }}>
                {copied ? <Check /> : <Copy />}
              </Button>
            )}
            {isLast && (
              <Button variant="ghost" size="icon-sm" title="Regenerate response" onClick={onRegenerate} disabled={busy}>
                <RotateCcw />
              </Button>
            )}
            {m.content && <SpeakButton text={m.content} />}
            {m.model && <span className="ml-1 text-xs">{m.model}</span>}
            {m.skill && <span className="ml-1 text-xs">· skill: {m.skill}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ name, onPick }: { name: string; onPick: (s: string) => void }) {
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const suggestions = [
    "Explain how pgvector indexes work, with an example",
    "Write a Python script that deduplicates rows in a CSV, and test it",
    "Research the latest stable Spring Boot release and summarise what changed",
    "Help me plan a migration from REST polling to Server-Sent Events",
  ];
  return (
    <div className="flex flex-col items-center gap-6 pb-6 text-center animate-fade-up">
      <div className="flex items-center gap-3">
        <Logo className="h-8 w-8" />
        <h1 className="font-serif text-3xl sm:text-4xl">{greet}{name ? `, ${name.split(" ")[0]}` : ""}</h1>
      </div>
      <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
        {suggestions.map((s) => (
          <button key={s} onClick={() => onPick(s)} className="rounded-xl border bg-card/60 px-4 py-3 text-left text-sm text-muted-foreground transition-all hover:-translate-y-0.5 hover:bg-card hover:text-foreground hover:shadow-sm">
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

