import { ApiError, api } from "./api";
import { createSSEParser } from "./sse";

export type Workspace = { id: string; name: string; description: string; source: string; created_at: string; updated_at: string };
export type TreeEntry = { path: string; type: "file" | "dir"; size?: number };
export type FileStatus = "modified" | "added" | "deleted" | "untracked" | "renamed";
export type GitFile = { path: string; status: FileStatus; code: string; from: string | null; added: number; removed: number };
export type GitStatus = { branch: string; ahead: number; behind: number; clean: boolean; remote: string | null; files: GitFile[] };
export type Commit = { sha: string; short: string; author: string; email: string; date: string; message: string; stat: string };
export type Approval = {
  id: string; workspace_id: string; kind: "delete" | "commit" | "command_deletes" | string; summary: string;
  details: { paths?: string[]; files?: { path: string; added: number; removed: number }[]; message?: string; command?: string; reason?: string };
  status: "pending" | "approved" | "denied" | "expired"; created_at: string;
};
export type ExecResult = {
  exit_code: number; stdout: string; stderr: string; timed_out: boolean; written: string[]; deleted: string[]; skipped: string[];
};
export type EditorContext = { path: string; selection?: string; start_line?: number; end_line?: number };

const H = { "X-Requested-With": "aria" };
export const wsApi = (id: string) => `/api/workspaces/${id}`;

export async function confirmFetch<T>(path: string, body: Record<string, unknown>, method = "POST"): Promise<T> {
  return api<T>(path, { method, body: { ...body, confirm: true } });
}

export async function uploadZip(file: File, name: string): Promise<Workspace> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`/api/workspaces/import?name=${encodeURIComponent(name)}`, {
    method: "POST", body: fd, credentials: "same-origin", headers: H,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.detail ?? "Import failed", res.status);
  return body;
}

export async function streamWorkspaceChat(
  wsId: string,
  body: { content: string; conversation_id?: string | null; model_id?: string | null; editor?: EditorContext | null; regenerate?: boolean },
  onEvent: (e: { type: string; [k: string]: any }) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`${wsApi(wsId)}/chat`, {
    method: "POST", credentials: "same-origin", signal,
    headers: { ...H, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const b = await res.json().catch(() => ({}));
    throw new ApiError(b.detail ?? res.statusText, res.status);
  }
  const parser = createSSEParser(({ data }) => { try { onEvent(JSON.parse(data)); } catch { /* ignore */ } });
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    parser.feed(value);
  }
  parser.end();
}

export { parseUnifiedDiff } from "./diff";
export type { DiffFile, DiffHunk, DiffLine } from "./diff";
