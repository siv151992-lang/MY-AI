/** Unified diff parser (dependency-free so it runs under node --test). */
export type DiffLine = { kind: "add" | "del" | "ctx" | "meta"; text: string; oldNo?: number; newNo?: number };
export type DiffHunk = { header: string; lines: DiffLine[] };
export type DiffFile = { oldPath: string; newPath: string; status: "added" | "deleted" | "modified" | "renamed"; binary: boolean; hunks: DiffHunk[]; added: number; removed: number };

export function parseUnifiedDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let f: DiffFile | null = null;
  let h: DiffHunk | null = null;
  let oldNo = 0, newNo = 0;
  for (const line of text.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const m = line.match(/^diff --git a\/(.*) b\/(.*)$/);
      f = { oldPath: m?.[1] ?? "", newPath: m?.[2] ?? "", status: "modified", binary: false, hunks: [], added: 0, removed: 0 };
      files.push(f);
      h = null;
      continue;
    }
    if (!f) continue;
    if (line.startsWith("new file mode")) f.status = "added";
    else if (line.startsWith("deleted file mode")) f.status = "deleted";
    else if (line.startsWith("rename from ")) { f.status = "renamed"; f.oldPath = line.slice(12); }
    else if (line.startsWith("rename to ")) f.newPath = line.slice(10);
    else if (line.startsWith("Binary files")) f.binary = true;
    else if (line.startsWith("@@")) {
      const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldNo = Number(m?.[1] ?? 0); newNo = Number(m?.[2] ?? 0);
      h = { header: line, lines: [] };
      f.hunks.push(h);
    } else if (h) {
      if (line.startsWith("+") && !line.startsWith("+++")) { h.lines.push({ kind: "add", text: line.slice(1), newNo: newNo++ }); f.added++; }
      else if (line.startsWith("-") && !line.startsWith("---")) { h.lines.push({ kind: "del", text: line.slice(1), oldNo: oldNo++ }); f.removed++; }
      else if (line.startsWith(" ")) h.lines.push({ kind: "ctx", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
      else if (line.startsWith("\\")) h.lines.push({ kind: "meta", text: line });
    }
  }
  return files;
}
