import { test } from "node:test";
import assert from "node:assert/strict";
import { linkCitations } from "./research.ts";

test("links citation markers to the sources that were read", () => {
  const md = "Fact [S1]. Both [S1, S2]. Unknown [S9]. Already linked [S1](http://x).";
  const out = linkCitations(md, [{ n: 1, url: "https://a.org" }, { n: 2, url: "https://b.org" }, { n: null, url: "https://failed" }]);
  assert.equal(out, "Fact [[S1]](https://a.org). Both [[S1]](https://a.org)[[S2]](https://b.org). Unknown [S9]. Already linked [S1](http://x).");
});
