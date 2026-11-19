/** Multi-agent run types and pure helpers (dependency-free so they run under node --test). */
export type AgentName = "planning" | "research" | "coding" | "data_analyst" | "writing" | "browser" | "verification";
export type TeamTask = {
  id: string; agent: AgentName; title: string; instruction: string; depends_on: string[]; expected_output: string;
  status: "pending" | "running" | "done" | "failed" | "skipped"; attempts: number; result: string; sources: string[];
  confidence: string; notes: string; error: string; tool_calls: number; started_at: string | null; finished_at: string | null;
};
export type Conflict = { topic: string; positions: { task: string; claim: string }[]; needs_check: boolean; resolution: string;
  resolved_by: string; status: "open" | "resolved" | "unresolved" };
export type Verdict = { ok: boolean; issues: string[]; checked: string[]; round: number } | null;
export type TeamRun = {
  id: string; goal: string; status: string; plan_notes: string[]; conflicts: Conflict[]; verification: Verdict; final: string;
  error: string; created_at: string; finished_at: string | null; active: boolean; tasks: TeamTask[];
};
export type AgentInfo = { name: AgentName; label: string; description: string; tools: string[]; missing?: string[]; available: boolean; side_effects: boolean };
export type TeamEvent = { seq: number; type: string; data: any; at: string };

/** Dependency waves: tasks in one wave only depend on earlier waves (they may run in parallel). */
export function waves(tasks: { id: string; depends_on: string[] }[]): string[][] {
  const done = new Set<string>();
  const left = new Map(tasks.map((t) => [t.id, t]));
  const out: string[][] = [];
  while (left.size) {
    const layer = [...left.values()].filter((t) => t.depends_on.every((d) => done.has(d) || !left.has(d) && !tasks.some((x) => x.id === d))).map((t) => t.id);
    if (!layer.length) { out.push([...left.keys()]); break; }
    out.push(layer);
    layer.forEach((id) => { done.add(id); left.delete(id); });
  }
  return out;
}

/** Live state of each agent from the event stream: what it is working on right now. */
export function agentStates(events: TeamEvent[]): Record<string, { state: "idle" | "working"; task: string | null; title: string; done: number }> {
  const s: Record<string, { state: "idle" | "working"; task: string | null; title: string; done: number }> = {};
  for (const e of events) {
    if (e.type !== "agent_status") continue;
    const cur = s[e.data.agent] ?? { state: "idle", task: null, title: "", done: 0 };
    s[e.data.agent] = { state: e.data.state, task: e.data.task, title: e.data.title ?? "", done: cur.done + (e.data.state === "idle" ? 1 : 0) };
  }
  return s;
}
