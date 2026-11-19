import { test } from "node:test";
import assert from "node:assert/strict";
import { createSSEParser, type SSEEvent } from "./sse.ts";

test("parses events split across arbitrary chunk boundaries", () => {
  const got: SSEEvent[] = [];
  const p = createSSEParser((e) => got.push(e));
  const raw =
    'event: token\ndata: {"type":"token","text":"Hel"}\n\n' +
    ": keep-alive comment\n\n" +
    'event: token\r\ndata: {"type":"token","text":"lo"}\r\n\r\n' +
    'event: done\ndata: {"type":"done"}\n\n';
  for (let i = 0; i < raw.length; i += 7) p.feed(raw.slice(i, i + 7));
  p.end();
  assert.deepEqual(got.map((e) => e.event), ["token", "token", "done"]);
  assert.equal(got.map((e) => JSON.parse(e.data).text ?? "").join(""), "Hello");
});

test("multi-line data and trailing event without blank line", () => {
  const got: SSEEvent[] = [];
  const p = createSSEParser((e) => got.push(e));
  p.feed("data: line1\ndata: line2\n\ndata: tail");
  p.end();
  assert.deepEqual(got, [
    { event: "message", data: "line1\nline2" },
    { event: "message", data: "tail" },
  ]);
});
