"use client";

import { Activity } from "lucide-react";
import { Card, Shell } from "./Shell";
import { TaskList } from "./TaskList";
import { useTasks } from "./useTasks";

const KINDS: Record<string, string> = { agent: "Autonomous agent", research: "Deep research", team: "Agent team", automation: "Automations" };

export function TasksApp() {
  const { snap, live } = useTasks();
  return (
    <Shell title="Tasks" icon={Activity} actions={<span className="text-xs text-muted-foreground">{live ? "● live" : "connecting…"}</span>}>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {Object.entries(KINDS).map(([k, label]) => {
          const c = snap?.counts[k];
          return <Card key={k} className="p-3"><div className="text-xs text-muted-foreground">{label}</div>
            <div className="text-lg font-medium">{c?.active ?? 0} <span className="text-sm font-normal text-muted-foreground">running</span></div>
            <div className="text-xs text-muted-foreground">{c?.total ?? 0} recent · {c?.failed ?? 0} failed</div></Card>;
        })}
      </div>
      <Card className="mb-4"><h2 className="font-medium">Running</h2><TaskList items={snap?.active ?? []} empty="Nothing running right now." /></Card>
      <Card><h2 className="font-medium">Recent</h2><TaskList items={snap?.recent ?? []} empty="No tasks yet." cancellable={false} /></Card>
    </Shell>
  );
}
