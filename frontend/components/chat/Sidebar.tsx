"use client";

import { useState } from "react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { BookOpenCheck, Bot, Brain, Check, Code2, FileText, LogOut, Monitor, Moon, MoreHorizontal, PanelLeftClose, Pencil, SquarePen, Sun, Trash2, X } from "lucide-react";
import type { Conversation, User } from "@/lib/types";
import { cn, groupByDate } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Logo } from "./Logo";

export function Sidebar({ open, onClose, user, conversations, activeId, onSelect, onNew, onRename, onDelete, onLogout, onMemory }: {
  open: boolean;
  onClose: () => void;
  user: User | null;
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
  onMemory: () => void;
}) {
  const { theme, setTheme } = useTheme();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const commit = () => {
    if (editing && draft.trim()) onRename(editing, draft.trim());
    setEditing(null);
  };

  return (
    <>
      {/* mobile backdrop */}
      <div onClick={onClose} className={cn("fixed inset-0 z-30 bg-black/30 transition-opacity md:hidden", open ? "opacity-100" : "pointer-events-none opacity-0")} />
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r bg-sidebar transition-transform duration-300 ease-out md:static md:z-0",
          open ? "translate-x-0" : "-translate-x-full md:hidden",
        )}
      >
        <div className="flex items-center gap-2 px-3 pb-2 pt-3">
          <Logo className="h-6 w-6" />
          <span className="font-serif text-lg">Aria</span>
          <Button variant="ghost" size="icon-sm" className="ml-auto" title="Close sidebar" onClick={onClose}>
            <PanelLeftClose />
          </Button>
        </div>
        <div className="px-2">
          <Button variant="ghost" className="w-full justify-start text-brand hover:text-brand" onClick={onNew}>
            <SquarePen /> New chat
          </Button>
          <Button asChild variant="ghost" className="w-full justify-start">
            <Link href="/agent"><Bot /> Autonomous agent</Link>
          </Button>
          <Button asChild variant="ghost" className="w-full justify-start">
            <Link href="/code"><Code2 /> Code workspace</Link>
          </Button>
          <Button asChild variant="ghost" className="w-full justify-start">
            <Link href="/research"><BookOpenCheck /> Deep research</Link>
          </Button>
          <Button asChild variant="ghost" className="w-full justify-start">
            <Link href="/documents"><FileText /> Documents</Link>
          </Button>
        </div>
        <nav className="thin-scroll mt-2 flex-1 overflow-y-auto px-2 pb-4">
          {conversations.length === 0 && <p className="px-3 py-6 text-sm text-muted-foreground">No conversations yet.</p>}
          {groupByDate(conversations).map((g) => (
            <div key={g.label} className="mb-3">
              <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground">{g.label}</div>
              {g.items.map((c) => (
                <div key={c.id} className={cn("group relative flex items-center rounded-lg text-sm transition-colors",
                  c.id === activeId ? "bg-accent" : "hover:bg-accent/60")}>
                  {editing === c.id ? (
                    <div className="flex w-full items-center gap-1 px-2 py-1">
                      <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(null); }}
                        className="min-w-0 flex-1 rounded-md border bg-background px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-ring" />
                      <Button variant="ghost" size="icon-sm" onClick={commit}><Check /></Button>
                      <Button variant="ghost" size="icon-sm" onClick={() => setEditing(null)}><X /></Button>
                    </div>
                  ) : (
                    <>
                      <button onClick={() => onSelect(c.id)} className="min-w-0 flex-1 truncate px-3 py-2 text-left">{c.title}</button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon-sm" className="mr-1 opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100" title="Options">
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" onCloseAutoFocus={(e) => e.preventDefault()}>
                          <DropdownMenuItem onSelect={() => { setEditing(c.id); setDraft(c.title); }}><Pencil /> Rename</DropdownMenuItem>
                          <DropdownMenuItem className="text-destructive focus:text-destructive"
                            onSelect={() => { if (confirm(`Delete “${c.title}”? This cannot be undone.`)) onDelete(c.id); }}>
                            <Trash2 /> Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t p-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-accent">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-sm font-semibold text-white">
                  {(user?.name || user?.email || "?").charAt(0).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{user?.name || "Account"}</div>
                  <div className="truncate text-xs text-muted-foreground">{user?.email}</div>
                </div>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start" className="w-64">
              <DropdownMenuItem onSelect={onMemory}><Brain /> Memory</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>Theme</DropdownMenuLabel>
              <DropdownMenuItem selected={theme === "light"} onSelect={() => setTheme("light")}><Sun /> Light</DropdownMenuItem>
              <DropdownMenuItem selected={theme === "dark"} onSelect={() => setTheme("dark")}><Moon /> Dark</DropdownMenuItem>
              <DropdownMenuItem selected={theme === "system"} onSelect={() => setTheme("system")}><Monitor /> System</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onLogout}><LogOut /> Log out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </aside>
    </>
  );
}
