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
  };
};

export type Memory = { id: string; content: string; created_at: string };
