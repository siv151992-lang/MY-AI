"use client";

import { useState } from "react";
import Link from "next/link";
import { Route as RouteIcon } from "lucide-react";
import type { Route } from "@/lib/hub";

const LAUNCH: Record<string, string> = { research: "/research", team: "/team", code: "/code", automation: "/automations/new" };

/** Shows how the adaptive router handled a message: which model, why, and any fallback. */
export function RouteBadge({ route, prompt }: { route: Route; prompt?: string }) {
  const [open, setOpen] = useState(false);
  const fb = route.fallbacks_used ?? [];
  return (
    <div className="mt-2 text-xs text-muted-foreground">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 hover:bg-accent">
        <RouteIcon className="h-3 w-3" /> Auto · {route.category} · {route.complexity}
        {fb.length > 0 && <span className="text-amber-600"> · fell back {fb.length}×</span>}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 rounded-lg border bg-card p-2.5">
          <div><b>Chosen model:</b> {route.model_id}{route.pinned ? " (pinned by you)" : ""}</div>
          {route.fallbacks.length > 0 && <div><b>Fallbacks:</b> {route.fallbacks.join(", ")}</div>}
          {route.needs.length > 0 && <div><b>Needs:</b> {route.needs.join(", ")}</div>}
          <div><b>Why:</b> {route.reasons.join("; ")}</div>
          {fb.map((f, i) => <div key={i} className="text-amber-700 dark:text-amber-400">{f.model} failed first: {f.error}</div>)}
        </div>
      )}
      {route.suggestion && (
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <span>{route.suggestion.reason}</span>
          <Link href={`${LAUNCH[route.suggestion.kind]}${prompt ? `?prompt=${encodeURIComponent(prompt.slice(0, 1500))}` : ""}`}
            className="rounded-full bg-brand/10 px-2 py-0.5 font-medium text-brand hover:bg-brand/20">{route.suggestion.label} →</Link>
        </div>
      )}
    </div>
  );
}
