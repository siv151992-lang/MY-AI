"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import CodeMirror, { EditorView, type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { languages } from "@codemirror/language-data";
import { oneDark } from "@codemirror/theme-one-dark";
import type { Extension } from "@codemirror/state";
import { useTheme } from "next-themes";

export type Selection = { text: string; startLine: number; endLine: number } | null;

export function CodeEditor({ path, value, onChange, onSave, onSelection, jumpTo, readOnly }: {
  path: string; value: string; onChange: (v: string) => void; onSave: () => void;
  onSelection: (s: Selection) => void; jumpTo?: { line: number; n: number } | null; readOnly?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const [lang, setLang] = useState<Extension | null>(null);
  const ref = useRef<ReactCodeMirrorRef>(null);
  const saveRef = useRef(onSave);
  saveRef.current = onSave;

  useEffect(() => {
    let cancelled = false;
    setLang(null);
    const name = path.split("/").pop() ?? path;
    const desc = languages.find((l) => l.filename?.test(name)) ??
      languages.find((l) => l.extensions.some((e) => name.toLowerCase().endsWith("." + e)));
    desc?.load().then((support) => { if (!cancelled) setLang(support); }).catch(() => {});
    return () => { cancelled = true; };
  }, [path]);

  useEffect(() => {
    const view = ref.current?.view;
    if (!view || !jumpTo) return;
    const line = view.state.doc.line(Math.min(Math.max(1, jumpTo.line), view.state.doc.lines));
    view.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
    view.focus();
  }, [jumpTo]);

  const extensions = useMemo(() => {
    const ext: Extension[] = [
      EditorView.lineWrapping,
      EditorView.domEventHandlers({
        keydown: (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveRef.current(); return true; }
          return false;
        },
      }),
      EditorView.updateListener.of((u) => {
        if (!u.selectionSet) return;
        const r = u.state.selection.main;
        if (r.empty) return onSelection(null);
        onSelection({ text: u.state.sliceDoc(r.from, r.to), startLine: u.state.doc.lineAt(r.from).number, endLine: u.state.doc.lineAt(r.to).number });
      }),
    ];
    if (lang) ext.push(lang);
    return ext;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

  return (
    <CodeMirror ref={ref} value={value} onChange={onChange} extensions={extensions} readOnly={readOnly}
      theme={resolvedTheme === "dark" ? oneDark : "light"} height="100%" className="h-full text-[13px]"
      basicSetup={{ foldGutter: true, highlightActiveLine: true, tabSize: 4 }} />
  );
}
