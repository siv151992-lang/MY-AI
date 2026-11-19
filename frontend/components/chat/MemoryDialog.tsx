"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Memory } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

export function MemoryDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setItems(null);
    api<Memory[]>("/api/memories").then(setItems).catch((e) => setError(e.message));
  }, [open]);

  async function add() {
    if (!draft.trim()) return;
    try {
      const m = await api<Memory>("/api/memories", { method: "POST", body: { content: draft.trim() } });
      setItems((x) => [m, ...(x ?? [])]);
      setDraft("");
    } catch (e: any) { setError(e.message); }
  }

  async function remove(id: string) {
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      setItems((x) => x?.filter((m) => m.id !== id) ?? null);
    } catch (e: any) { setError(e.message); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <div>
          <DialogTitle>Memory</DialogTitle>
          <DialogDescription>Things Aria remembers about you across chats. Delete anything you don’t want kept.</DialogDescription>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); add(); }} className="flex gap-2">
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add something to remember…" maxLength={500} />
          <Button type="submit" disabled={!draft.trim()}><Plus /> Add</Button>
        </form>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="thin-scroll max-h-80 space-y-1 overflow-y-auto">
          {items === null ? (
            <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : items.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">Nothing saved yet.</p>
          ) : (
            items.map((m) => (
              <div key={m.id} className="group flex items-start gap-2 rounded-lg px-3 py-2 text-sm hover:bg-accent/60">
                <span className="flex-1">{m.content}</span>
                <Button variant="ghost" size="icon-sm" title="Forget" className="opacity-0 group-hover:opacity-100" onClick={() => remove(m.id)}>
                  <Trash2 />
                </Button>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
