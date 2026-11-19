"use client";

import Link from "next/link";
import { Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import { duration, type TaskItem } from "@/lib/hub";
import { Button } from "@/components/ui/button";
import { StatusPill } from "./Shell";

export function TaskList({ items, empty, cancellable = true }: { items: TaskItem[]; empty: string; cancellable?: boolean }) {
  if (!items.length) return <p className="py-3 text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="divide-y">
      {items.map((t) => (
        <li key={`${t.kind}-${t.id}`} className="flex items-center gap-3 py-2.5">
          {t.active ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-brand" /> : <span className="w-4" />}
          <div className="min-w-0 flex-1">
            <Link href={t.url} className="line-clamp-1 text-sm font-medium hover:underline">{t.title || "(untitled)"}</Link>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{t.kind_label}</span><StatusPill status={t.status} />
              <span>{t.active ? `running ${duration(Math.max(0, Math.floor((Date.now() - new Date(t.created_at).getTime()) / 1000)))}` : duration(t.duration_s)}</span>
              {t.progress && <span>step {Math.min(t.progress.done + (t.active ? 1 : 0), t.progress.total)}/{t.progress.total}{t.progress.current ? ` · ${t.progress.current}` : ""}</span>}
              {t.error && <span className="text-destructive">{t.error}</span>}
            </div>
          </div>
          {cancellable && t.active && (
            <Button variant="ghost" size="sm" title="Cancel" onClick={() => api(`/api/tasks/${t.kind}/${t.id}/cancel`, { method: "POST" }).catch(() => {})}>
              <X /> Cancel
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}
