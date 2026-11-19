/** Incremental Server-Sent Events parser (works on fetch() streams, which EventSource can't do for POST). */
export type SSEEvent = { event: string; data: string };

export function createSSEParser(onEvent: (e: SSEEvent) => void) {
  let buffer = "";
  let event = "message";
  let data: string[] = [];

  const dispatch = () => {
    if (data.length) onEvent({ event, data: data.join("\n") });
    event = "message";
    data = [];
  };

  return {
    feed(chunk: string) {
      buffer += chunk;
      let nl: number;
      while ((nl = buffer.search(/\r?\n/)) !== -1) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + (buffer[nl] === "\r" ? 2 : 1));
        if (line === "") dispatch();
        else if (line.startsWith(":")) continue;
        else {
          const idx = line.indexOf(":");
          const field = idx === -1 ? line : line.slice(0, idx);
          let value = idx === -1 ? "" : line.slice(idx + 1);
          if (value.startsWith(" ")) value = value.slice(1);
          if (field === "event") event = value;
          else if (field === "data") data.push(value);
        }
      }
    },
    end() {
      if (buffer) this.feed("\n");
      dispatch();
    },
  };
}
