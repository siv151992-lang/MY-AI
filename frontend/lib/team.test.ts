import { test } from "node:test";
import assert from "node:assert/strict";
import { agentStates, waves } from "./team.ts";

test("groups tasks into dependency waves", () => {
  const w = waves([{ id: "T1", depends_on: [] }, { id: "T2", depends_on: [] }, { id: "T3", depends_on: ["T1", "T2"] }, { id: "T4", depends_on: ["T3"] }]);
  assert.deepEqual(w, [["T1", "T2"], ["T3"], ["T4"]]);
});

test("tracks which agent is working on what", () => {
  const ev = (seq: number, data: any) => ({ seq, type: "agent_status", data, at: "" });
  const s = agentStates([ev(1, { agent: "research", state: "working", task: "T1", title: "Find data" }),
    ev(2, { agent: "coding", state: "working", task: "T2", title: "Code" }), ev(3, { agent: "research", state: "idle", task: "T1", title: "Find data" })]);
  assert.equal(s.research.state, "idle");
  assert.equal(s.research.done, 1);
  assert.deepEqual([s.coding.state, s.coding.task], ["working", "T2"]);
});
