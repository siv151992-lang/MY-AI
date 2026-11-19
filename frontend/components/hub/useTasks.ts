"use client";

import { useEffect, useState } from "react";
import { createSSEParser } from "@/lib/sse";
import type { TaskSnapshot } from "@/lib/hub";

/** Live task snapshot over SSE (reconnects with backoff). */
export function useTasks() {
  const [snap, setSnap] = useState<TaskSnapshot | null>(null);
  const [live, setLive] = useState(false);
  useEffect(() => {
    let stop = false;
    let ctrl: AbortController | null = null;
    let delay = 1000;
    (async function connect() {
      while (!stop) {
        ctrl = new AbortController();
        try {
          const res = await fetch("/api/tasks/stream", { credentials: "same-origin", signal: ctrl.signal, headers: { Accept: "text/event-stream" } });
          if (!res.ok || !res.body) throw new Error(String(res.status));
          setLive(true);
          delay = 1000;
          const parser = createSSEParser(({ data }) => { try { setSnap(JSON.parse(data)); } catch { /* ignore */ } });
          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            parser.feed(value);
          }
        } catch { /* reconnect below */ }
        setLive(false);
        if (stop) break;
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 2, 15000);
      }
    })();
    return () => { stop = true; ctrl?.abort(); };
  }, []);
  return { snap, live };
}
