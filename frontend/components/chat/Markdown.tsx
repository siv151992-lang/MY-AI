"use client";

import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy } from "lucide-react";
import { copyText } from "@/lib/utils";

function textOf(node: any): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node?.props?.children) return textOf(node.props.children);
  return "";
}

function CodeBlock({ children, ...props }: any) {
  const [copied, setCopied] = useState(false);
  const child = Array.isArray(children) ? children[0] : children;
  const lang = /language-([\w+-]+)/.exec(child?.props?.className || "")?.[1] ?? "text";
  const code = textOf(child?.props?.children).replace(/\n$/, "");
  return (
    <div className="not-prose my-4 overflow-hidden rounded-xl border bg-muted/40">
      <div className="flex items-center justify-between border-b bg-muted/60 px-4 py-1.5 text-xs text-muted-foreground">
        <span className="font-mono">{lang}</span>
        <button
          onClick={async () => { if (await copyText(code)) { setCopied(true); setTimeout(() => setCopied(false), 1500); } }}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:bg-accent hover:text-foreground"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre {...props} className="thin-scroll overflow-x-auto p-4 text-[13px] leading-6">
        {children}
      </pre>
    </div>
  );
}

export const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeHighlight, { detect: true }]]}
      components={{
        pre: CodeBlock,
        a: ({ node, ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
        table: ({ node, ...p }) => (
          <div className="thin-scroll overflow-x-auto"><table {...p} /></div>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
});
