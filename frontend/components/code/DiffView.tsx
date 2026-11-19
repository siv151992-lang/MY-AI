"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileCode2 } from "lucide-react";
import { parseUnifiedDiff } from "@/lib/diff";
import { cn } from "@/lib/utils";

const STATUS_BADGE: Record<string, string> = {
  added: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400", deleted: "bg-red-500/15 text-red-700 dark:text-red-400",
  renamed: "bg-sky-500/15 text-sky-700 dark:text-sky-400", modified: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
};

export function DiffView({ diff, onOpenFile, emptyText = "No changes." }: { diff: string; onOpenFile?: (path: string) => void; emptyText?: string }) {
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  if (!files.length) return <p className="p-6 text-center text-sm text-muted-foreground">{emptyText}</p>;
  return (
    <div className="space-y-3 p-3">
      <p className="text-xs text-muted-foreground">
        {files.length} file{files.length === 1 ? "" : "s"} changed ·{" "}
        <span className="text-emerald-600 dark:text-emerald-400">+{files.reduce((a, f) => a + f.added, 0)}</span>{" "}
        <span className="text-red-600 dark:text-red-400">−{files.reduce((a, f) => a + f.removed, 0)}</span>
      </p>
      {files.map((f) => {
        const key = f.newPath || f.oldPath;
        const isCollapsed = collapsed[key];
        return (
          <div key={key} className="overflow-hidden rounded-lg border">
            <div className="flex items-center gap-2 border-b bg-muted/50 px-3 py-1.5 text-sm">
              <button onClick={() => setCollapsed((c) => ({ ...c, [key]: !c[key] }))} className="text-muted-foreground" title={isCollapsed ? "Expand" : "Collapse"}>
                {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
              <FileCode2 className="h-4 w-4 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate font-mono text-xs">
                {f.status === "renamed" ? `${f.oldPath} → ${f.newPath}` : key}
              </span>
              <span className={cn("rounded px-1.5 text-[11px]", STATUS_BADGE[f.status])}>{f.status}</span>
              <span className="font-mono text-xs"><span className="text-emerald-600 dark:text-emerald-400">+{f.added}</span> <span className="text-red-600 dark:text-red-400">−{f.removed}</span></span>
              {onOpenFile && f.status !== "deleted" && (
                <button className="text-xs underline" onClick={() => onOpenFile(f.newPath)}>Open</button>
              )}
            </div>
            {!isCollapsed && (
              f.binary ? <p className="px-3 py-2 text-xs text-muted-foreground">Binary file changed</p> : (
                <div className="overflow-x-auto font-mono text-xs leading-5">
                  {f.hunks.map((h, i) => (
                    <div key={i}>
                      <div className="bg-sky-500/10 px-3 text-sky-700 dark:text-sky-300">{h.header}</div>
                      {h.lines.map((l, j) => (
                        <div key={j} className={cn("flex whitespace-pre",
                          l.kind === "add" && "bg-emerald-500/15", l.kind === "del" && "bg-red-500/15", l.kind === "meta" && "text-muted-foreground")}>
                          <span className="w-10 shrink-0 select-none pr-1 text-right text-muted-foreground/70">{l.oldNo ?? ""}</span>
                          <span className="w-10 shrink-0 select-none pr-1 text-right text-muted-foreground/70">{l.newNo ?? ""}</span>
                          <span className="w-4 shrink-0 select-none text-center">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}</span>
                          <span className="pr-4">{l.text}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}
