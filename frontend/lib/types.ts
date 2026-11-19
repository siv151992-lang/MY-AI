export type User = { id: string; email: string; name: string };

export type ModelInfo = { id: string; label: string; provider: string; vision: boolean; tools: boolean };

export type Conversation = { id: string; title: string; model_id: string | null; updated_at: string };

export type FileRef = { id: string; filename: string; kind: "document" | "image"; mime_type?: string };

export type ToolRun = {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  is_error?: boolean;
  running?: boolean;
};

export type Verification = { ok: boolean; issues: string[] } | null;

/** What the reasoning engine understood — never its private deliberation. */
export type Analysis = {
  task_types: string[];
  complexity: "simple" | "moderate" | "complex";
  parts: string[];
  assumptions: string[];
  missing_info: string[];
  interpretation: string | null;
} | null;

export type ChatMessage = {
  key: string; // stable client key
  id?: string;
  role: "user" | "assistant";
  content: string;
  files?: FileRef[];
  analysis?: Analysis;
  clarification?: boolean;
  plan?: string[] | null;
  tools?: ToolRun[];
  verification?: Verification;
  revised?: boolean;
  status?: string | null;
  error?: string | null;
  streaming?: boolean;
  interrupted?: boolean;
  model?: string;
  memory?: MessageMemory | null;
  suggestions?: MemorySuggestion[];
};

export type ServerMessage = {
  id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  file_ids: string[];
  files?: FileRef[];
  meta: {
    analysis?: Analysis;
    clarification?: boolean;
    plan?: string[] | null;
    tools?: ToolRun[];
    verification?: Verification;
    revisions?: number;
    interrupted?: boolean;
    model?: string;
    memory?: { ids: string[]; recalled: number; compacted: boolean };
  };
};

// ---------------- memory ----------------
export type MemoryCategory = "instruction" | "profile" | "preference" | "project" | "relationship" | "fact" | "other";
export type MemoryStatus = "pending" | "active" | "archived";

export type Memory = {
  id: string; content: string; category: MemoryCategory; status: MemoryStatus;
  source: "manual" | "explicit" | "extracted" | "import";
  importance: number; confidence: number; sensitivity: "normal" | "personal" | "sensitive";
  pinned: boolean; access_count: number; version: number;
  created_at: string; updated_at: string; last_accessed_at: string | null;
  source_conversation_id: string | null; source_excerpt: string | null; supersedes_id: string | null;
  score?: number; relevance?: number; sim?: number;
};

export type MemoryRevision = {
  version: number; content: string; category: MemoryCategory; importance: number; status: MemoryStatus;
  changed_by: "user" | "assistant" | "system"; reason: string; created_at: string;
};

export type MemoryDetail = Memory & {
  revisions: MemoryRevision[];
  source_conversation: { id: string; title: string } | null;
};

export type MemoryList = { items: Memory[]; counts: Record<MemoryStatus, number>; categories: MemoryCategory[] };

export type MemorySettings = {
  memory_enabled: boolean; auto_extract: boolean; require_approval: boolean; use_in_chat: boolean;
  allow_sensitive: boolean; store_source_excerpts: boolean; summarize_conversations: boolean;
  cross_conversation_recall: boolean; retention_days: number | null; pending_expiry_days: number;
};

export type AuditEntry = { id: number; memory_id: string | null; action: string; detail: Record<string, unknown>; created_at: string };

export type ConversationMemory = {
  conversation_id: string; memory_enabled: boolean; summary: string | null; summarized_upto: number;
  total_messages: number; state: Record<string, string[]>;
  segments: { id: number; from_seq: number; to_seq: number; summary: string; created_at: string }[];
  memories: Memory[];
};

/** Memory used for one answer (live from the stream; after reload only ids/counts are kept). */
export type MessageMemory = {
  used: { id: string; content?: string; category?: MemoryCategory; pinned?: boolean }[];
  recalled: number;
  compacted: boolean;
};

export type MemorySuggestion = {
  id: string; content: string; category: MemoryCategory; status: "pending" | "active";
  importance: number; supersedes_id: string | null;
  decision?: "saved" | "dismissed" | "error";
};

// ---------------- autonomous agent ----------------
export type RunStatus = "planning" | "running" | "awaiting_approval" | "verifying" | "completed"
  | "completed_with_issues" | "failed" | "cancelled";

export type AgentStep = {
  id: string; idx: number; title: string; description: string; tool: string; success_criteria: string;
  status: "pending" | "running" | "done" | "failed" | "skipped"; attempts: number; result: string; error: string;
  origin: "plan" | "replan" | "remediation"; started_at: string | null; finished_at: string | null;
};

export type AgentApproval = {
  id: string; run_id: string; step_idx: number; tool: string; args: Record<string, unknown>; summary: string;
  status: "pending" | "approved" | "denied" | "expired"; created_at: string;
};

export type AgentAction = {
  step_idx: number; tool: string; args_summary: string; ok: boolean; output: string; approval: string | null;
};

export type AgentRun = {
  id: string; goal: string; status: RunStatus; model_id: string | null; report: string; error: string;
  verification: { achieved: string; issues: string[]; evidence: string } | null;
  created_at: string; updated_at: string; finished_at: string | null;
};

export type AgentRunDetail = AgentRun & {
  active: boolean; steps: AgentStep[]; approvals: AgentApproval[]; actions: AgentAction[];
};

export type AgentEvent = { seq: number; type: string; data: any; at: string };

export type ToolInfo = { name: string; description: string; needs_approval: boolean };
