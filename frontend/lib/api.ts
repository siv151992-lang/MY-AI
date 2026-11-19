import { createSSEParser } from "./sse";

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

const HEADERS = { "X-Requested-With": "aria" }; // CSRF guard header required by the backend

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText || "Request failed";
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {}
    if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: init.method ?? "GET",
    credentials: "same-origin",
    headers: init.body !== undefined ? { ...HEADERS, "Content-Type": "application/json" } : HEADERS,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  return handle<T>(res);
}

export function uploadFile(file: File, onProgress?: (pct: number) => void): Promise<any> {
  // XHR so we can report upload progress
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files");
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "aria");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: any = {};
      try { body = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(body.detail || `Upload failed (${xhr.status})`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Network error during upload", 0));
    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
  });
}

export type StreamEvent = { type: string; [k: string]: any };

export async function streamChat(
  conversationId: string,
  body: { content: string; file_ids: string[]; model_id?: string | null; agent_mode: boolean; regenerate?: boolean; skill_id?: string | null },
  onEvent: (e: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { ...HEADERS, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) await handle(res);
  const parser = createSSEParser(({ data }) => {
    try {
      onEvent(JSON.parse(data));
    } catch {
      /* ignore malformed frame */
    }
  });
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    parser.feed(value);
  }
  parser.end();
}
