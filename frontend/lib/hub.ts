// Types and small helpers for the platform hub (routing, projects, skills, agents, integrations, automations, tasks).

export type Route = {
  model_id: string | null; fallbacks: string[]; category: string; complexity: "low" | "medium" | "high";
  needs: string[]; agent_mode: boolean; reasons: string[]; pinned?: boolean;
  suggestion: { kind: "research" | "team" | "code" | "automation"; label: string; reason: string } | null;
  model_label?: string | null; fallback_labels?: string[]; fallbacks_used?: { model: string; error: string }[];
};

export type Project = {
  id: string; name: string; description: string; instructions: string; archived: boolean;
  chats?: number; documents?: number; created_at: string; updated_at: string;
  items?: Record<string, { id: string; title: string; status?: string; url: string; created_at: string }[]>;
};

export type SkillInput = { name: string; label: string; required: boolean; default?: string };
export type Skill = {
  id: string; name: string; description: string; instructions: string; inputs: SkillInput[]; tools: string[];
  model_id: string | null; builtin: boolean; uses: number | null;
};

export type CustomAgent = {
  id: string; name: string; description: string; instructions: string; tools: string[]; model_id: string | null;
  use_memory: boolean; in_team: boolean; team_key: string; updated_at: string;
};

export type TaskResult = {
  status: "done" | "partial" | "failed"; result: string; sources: string[]; notes: string; error: string; model: string;
  tool_calls: number; tools_used: { tool: string; ok: boolean; input: string }[];
};

export type ToolInfo = { name: string; description: string };

export type Integration = {
  id: string; name: string; slug: string; description: string; base_url: string; auth_type: string; auth_name: string;
  has_secret: boolean; allow_writes: boolean; test_path: string; tool_name: string;
  last_test: { ok: boolean; status?: number; error?: string; preview?: string; at: string } | null;
};
export type BuiltinIntegration = { name: string; configured: boolean; needs: string; enables: string };

export type AutomationStep = { type: string; title?: string; [k: string]: any };
export type Automation = {
  id: string; name: string; enabled: boolean; trigger_type: "manual" | "schedule" | "webhook"; schedule: string;
  timezone: string; steps: AutomationStep[]; has_webhook: boolean; next_run_at: string | null; last_run_at: string | null;
  schedule_text?: string; last_status?: string | null; webhook_url?: string | null; runs?: AutomationRun[]; project_id?: string | null;
};
export type AutomationRun = {
  id: string; automation_id: string; automation_name?: string; trigger: string; status: string; output: string; error: string;
  created_at: string; finished_at: string | null;
  steps: { index: number; type: string; title: string; status: string; output?: string; error?: string }[];
};

export type TaskItem = {
  kind: "agent" | "research" | "team" | "automation"; kind_label: string; id: string; title: string; status: string;
  active: boolean; error: string; created_at: string; updated_at: string; duration_s: number; url: string;
  progress: { done: number; total: number; current: string | null } | null;
};
export type TaskSnapshot = { active: TaskItem[]; recent: TaskItem[]; counts: Record<string, { active: number; failed: number; total: number }> };

export function duration(s: number) {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function ago(iso: string | null | undefined) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export const PENDING_KEY = "aria.pendingMessage";

/** Hand a message to the chat page (it sends it once on load). */
export function queueChatMessage(text: string, skillId?: string) {
  try { sessionStorage.setItem(PENDING_KEY, JSON.stringify({ text, skillId })); } catch { /* storage blocked */ }
}
export function takeQueuedChatMessage(): { text: string; skillId?: string } | null {
  try {
    const v = sessionStorage.getItem(PENDING_KEY);
    sessionStorage.removeItem(PENDING_KEY);
    return v ? JSON.parse(v) : null;
  } catch { return null; }
}
