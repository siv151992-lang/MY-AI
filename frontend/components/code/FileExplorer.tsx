"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, File, FilePlus, Folder, FolderOpen, FolderPlus, Pencil, Trash2 } from "lucide-react";
import type { FileStatus, TreeEntry } from "@/lib/workspace";
import { cn } from "@/lib/utils";

type Node = { name: string; path: string; type: "file" | "dir"; children: Node[] };

function build(entries: TreeEntry[]): Node[] {
  const root: Node = { name: "", path: "", type: "dir", children: [] };
  const dirs = new Map<string, Node>([["", root]]);
  for (const e of entries) {
    const parent = dirs.get(e.path.includes("/") ? e.path.slice(0, e.path.lastIndexOf("/")) : "") ?? root;
    const node: Node = { name: e.path.split("/").pop()!, path: e.path, type: e.type, children: [] };
    parent.children.push(node);
    if (e.type === "dir") dirs.set(e.path, node);
  }
  const sort = (n: Node) => {
    n.children.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1));
    n.children.forEach(sort);
  };
  sort(root);
  return root.children;
}

export const STATUS_COLOR: Record<FileStatus, string> = {
  modified: "text-amber-600 dark:text-amber-400", added: "text-emerald-600 dark:text-emerald-400",
  untracked: "text-emerald-600 dark:text-emerald-400", deleted: "text-red-600 line-through", renamed: "text-sky-600 dark:text-sky-400",
};
const LETTER: Record<FileStatus, string> = { modified: "M", added: "A", untracked: "U", deleted: "D", renamed: "R" };

export function FileExplorer({ entries, changes, activePath, onOpen, onCreate, onRename, onDelete }: {
  entries: TreeEntry[]; changes: Record<string, FileStatus>; activePath: string | null;
  onOpen: (path: string) => void; onCreate: (dir: string, type: "file" | "dir") => void;
  onRename: (path: string) => void; onDelete: (path: string, type: "file" | "dir") => void;
}) {
  const tree = useMemo(() => build(entries), [entries]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const dirChanged = (p: string) => Object.keys(changes).some((c) => c.startsWith(p + "/"));

  const render = (nodes: Node[], depth: number): React.ReactNode =>
    nodes.map((n) => {
      const isOpen = open[n.path] ?? (depth === 0 && nodes.length < 8);
      const st = changes[n.path];
      return (
        <div key={n.path}>
          <div className={cn("group flex h-7 cursor-pointer items-center gap-1 rounded pr-1 text-[13px] hover:bg-accent",
            activePath === n.path && "bg-accent")}
            style={{ paddingLeft: 6 + depth * 12 }}
            onClick={() => (n.type === "dir" ? setOpen((o) => ({ ...o, [n.path]: !isOpen })) : onOpen(n.path))}>
            {n.type === "dir" ? (
              <>
                {isOpen ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
                {isOpen ? <FolderOpen className="h-4 w-4 shrink-0 text-brand" /> : <Folder className="h-4 w-4 shrink-0 text-brand" />}
              </>
            ) : <File className="ml-[18px] h-4 w-4 shrink-0 text-muted-foreground" />}
            <span className={cn("min-w-0 flex-1 truncate", st && STATUS_COLOR[st], n.type === "dir" && dirChanged(n.path) && "text-amber-600 dark:text-amber-400")}>{n.name}</span>
            <span className="hidden shrink-0 gap-0.5 group-hover:flex">
              {n.type === "dir" && (
                <>
                  <IconBtn title="New file" onClick={() => { setOpen((o) => ({ ...o, [n.path]: true })); onCreate(n.path, "file"); }}><FilePlus /></IconBtn>
                  <IconBtn title="New folder" onClick={() => { setOpen((o) => ({ ...o, [n.path]: true })); onCreate(n.path, "dir"); }}><FolderPlus /></IconBtn>
                </>
              )}
              <IconBtn title="Rename / move" onClick={() => onRename(n.path)}><Pencil /></IconBtn>
              <IconBtn title="Delete" onClick={() => onDelete(n.path, n.type)}><Trash2 /></IconBtn>
            </span>
            {st && <span className={cn("w-3 shrink-0 text-center text-[11px] font-semibold", STATUS_COLOR[st])}>{LETTER[st]}</span>}
          </div>
          {n.type === "dir" && isOpen && render(n.children, depth + 1)}
        </div>
      );
    });

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-8 shrink-0 items-center justify-between px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Files
        <span className="flex gap-0.5">
          <IconBtn title="New file" onClick={() => onCreate("", "file")}><FilePlus /></IconBtn>
          <IconBtn title="New folder" onClick={() => onCreate("", "dir")}><FolderPlus /></IconBtn>
        </span>
      </div>
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto px-1 pb-4">
        {tree.length ? render(tree, 0) : <p className="p-3 text-xs text-muted-foreground">Empty project. Create a file or ask the AI to build something.</p>}
      </div>
    </div>
  );
}

function IconBtn({ children, title, onClick }: { children: React.ReactNode; title: string; onClick: () => void }) {
  return (
    <button title={title} onClick={(e) => { e.stopPropagation(); onClick(); }}
      className="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-foreground [&_svg]:h-3.5 [&_svg]:w-3.5">
      {children}
    </button>
  );
}
