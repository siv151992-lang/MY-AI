"use client";

import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

export type ConfirmOptions = {
  title: string; body?: React.ReactNode; confirmLabel?: string; destructive?: boolean;
  /** Optional text input (e.g. a commit message or token); its value is returned. */
  input?: { label: string; placeholder?: string; type?: "text" | "password"; initial?: string; required?: boolean };
};

/** Promise-based confirmation dialog: `if (!(await ask({...}))) return;` */
export function useConfirm(): [React.ReactNode, (o: ConfirmOptions) => Promise<false | { value: string }>] {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const [value, setValue] = useState("");
  const resolver = useRef<((v: false | { value: string }) => void) | null>(null);

  const ask = useCallback((o: ConfirmOptions) => {
    setOpts(o);
    setValue(o.input?.initial ?? "");
    return new Promise<false | { value: string }>((res) => { resolver.current = res; });
  }, []);

  const close = (result: false | { value: string }) => {
    resolver.current?.(result);
    resolver.current = null;
    setOpts(null);
  };

  const element = (
    <Dialog open={!!opts} onOpenChange={(o) => { if (!o) close(false); }}>
      <DialogContent className="max-w-md">
        <div className="pr-6">
          <DialogTitle>{opts?.title}</DialogTitle>
          {opts?.body && <DialogDescription asChild><div className="mt-2 text-sm text-muted-foreground">{opts.body}</div></DialogDescription>}
        </div>
        {opts?.input && (
          <label className="space-y-1 text-sm">
            <span className="font-medium">{opts.input.label}</span>
            <input autoFocus type={opts.input.type ?? "text"} value={value} placeholder={opts.input.placeholder}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (!opts.input?.required || value.trim())) close({ value }); }}
              className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm" />
          </label>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
          <Button variant={opts?.destructive ? "destructive" : "default"} autoFocus={!opts?.input}
            disabled={!!opts?.input?.required && !value.trim()} onClick={() => close({ value })}>
            {opts?.confirmLabel ?? "Confirm"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
  return [element, ask];
}
