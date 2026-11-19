import { test } from "node:test";
import assert from "node:assert/strict";

import { parseUnifiedDiff } from "./diff.ts";

test("parses unified diffs with added, deleted and renamed files", () => {
  const d = [
    "diff --git a/src/app.py b/src/app.py",
    "index 1..2 100644",
    "--- a/src/app.py",
    "+++ b/src/app.py",
    "@@ -1,3 +1,3 @@",
    " import os",
    "-print('a')",
    "+print('b')",
    " x = 1",
    "diff --git a/new.txt b/new.txt",
    "new file mode 100644",
    "--- /dev/null",
    "+++ b/new.txt",
    "@@ -0,0 +1 @@",
    "+hello",
    "diff --git a/old.py b/moved.py",
    "similarity index 100%",
    "rename from old.py",
    "rename to moved.py",
  ].join("\n");
  const files = parseUnifiedDiff(d);
  assert.equal(files.length, 3);
  assert.deepEqual([files[0].added, files[0].removed], [1, 1]);
  assert.deepEqual(files[0].hunks[0].lines.map((l) => [l.kind, l.oldNo, l.newNo]), [["ctx", 1, 1], ["del", 2, undefined], ["add", undefined, 2], ["ctx", 3, 3]]);
  assert.equal(files[1].status, "added");
  assert.deepEqual([files[2].status, files[2].oldPath, files[2].newPath], ["renamed", "old.py", "moved.py"]);
});
