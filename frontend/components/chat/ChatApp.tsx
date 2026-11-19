"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDown, Loader2, PanelLeft, SquarePen } from "lucide-react";
import { api, streamChat, type StreamEvent } from "@/lib/api";
import type { ChatMessage, Conversation, FileRef, ModelInfo, ServerMessage, User } from "@/lib/types";
import { cn, uid } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Composer } from "./Composer";
import { MemoryDialog } from "./MemoryDialog";
import { EmptyState, MessageView } from "./MessageView";
import { Sidebar } from "./Sidebar";

const key = uid;

function fromServer(m: ServerMessage): ChatMessage {
  return {
    key: m.id, id: m.id, role: m.role, content: m.content, files: m.files,
    analysis: m.meta?.analysis ?? null, clarification: m.meta?.clarification,
    plan: m.meta?.plan ?? null, tools: m.meta?.tools ?? [], verification: m.meta?.verification ?? null,
    revised: (m.meta?.revisions ?? 0) > 0, interrupted: m.meta?.interrupted, model: m.meta?.model,
  };
}

export function ChatApp({ initialConversationId }: { initialConversationId: string | null }) {
  const [user, setUser] = useState<User | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(initialConversationId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingConv, setLoadingConv] = useState(false);
  const [busy, setBusy] = useState(false);
  const [agentMode, setAgentMode] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [prefill, setPrefill] = useState<{ text: string; n: number }>();
  const [atBottom, setAtBottom] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const defaultModel = useRef<string | null>(null);

  // ---------- bootstrap ----------
  useEffect(() => {
    setSidebarOpen(window.matchMedia("(min-width: 768px)").matches);
    setAgentMode(localStorage.getItem("aria.agentMode") === "1");
    (async () => {
      try {
        const [me, ms, convs] = await Promise.all([
          api<User>("/api/auth/me"),
          api<{ default: string | null; models: ModelInfo[] }>("/api/models"),
          api<Conversation[]>("/api/conversations"),
        ]);
        setUser(me);
        setModels(ms.models);
        defaultModel.current = localStorage.getItem("aria.model") && ms.models.some((m) => m.id === localStorage.getItem("aria.model"))
          ? localStorage.getItem("aria.model") : ms.default;
        setModelId((cur) => cur ?? defaultModel.current);
        setConversations(convs);
        if (!ms.models.length) setBanner("No AI provider is configured on the server. Add an API key to the backend .env file.");
      } catch (e: any) {
        if (e.status !== 401) setBanner(e.message || "Could not reach the server.");
      }
    })();
  }, []);

  const loadConversation = useCallback(async (id: string | null) => {
    abortRef.current?.abort();
    setActiveId(id);
    setMessages([]);
    if (!id) { setModelId(defaultModel.current); return; }
    setLoadingConv(true);
    try {
      const c = await api<{ id: string; model_id: string | null; messages: ServerMessage[] }>(`/api/conversations/${id}`);
      setMessages(c.messages.map(fromServer));
      if (c.model_id) setModelId(c.model_id);
    } catch (e: any) {
      setBanner(e.status === 404 ? "That conversation no longer exists." : e.message);
      setActiveId(null);
      window.history.replaceState(null, "", "/chat");
    } finally {
      setLoadingConv(false);
    }
  }, []);

  useEffect(() => { if (initialConversationId) loadConversation(initialConversationId); }, [initialConversationId, loadConversation]);

  useEffect(() => {
    const onPop = () => {
      const m = location.pathname.match(/^\/chat\/([\w-]+)/);
      loadConversation(m ? m[1] : null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [loadConversation]);

  // ---------- scrolling ----------
  useEffect(() => {
    if (atBottom) scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [messages, atBottom]);

  const onScroll = () => {
    const el = scroller.current;
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };

  // ---------- navigation ----------
  const selectConversation = (id: string) => {
    if (id === activeId) return;
    window.history.pushState(null, "", `/chat/${id}`);
    loadConversation(id);
    if (!window.matchMedia("(min-width: 768px)").matches) setSidebarOpen(false);
  };

  const newChat = () => {
    window.history.pushState(null, "", "/chat");
    loadConversation(null);
    if (!window.matchMedia("(min-width: 768px)").matches) setSidebarOpen(false);
  };

  // ---------- streaming ----------
  const patchLast = (fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((ms) => (ms.length && ms[ms.length - 1].role === "assistant" ? [...ms.slice(0, -1), fn(ms[ms.length - 1])] : ms));

  const handleEvent = (convId: string) => (ev: StreamEvent) => {
    switch (ev.type) {
      case "user_saved":
        setMessages((ms) => ms.map((m, i) => (i === ms.length - 2 && m.role === "user" ? { ...m, id: ev.message.id } : m)));
        break;
      case "status": patchLast((m) => ({ ...m, status: ev.text })); break;
      case "analysis": {
        const { type, ...a } = ev;
        patchLast((m) => ({ ...m, analysis: a as any }));
        break;
      }
      case "plan": patchLast((m) => ({ ...m, plan: ev.steps })); break;
      case "token": patchLast((m) => ({ ...m, content: m.content + ev.text, status: null })); break;
      case "tool_start":
        patchLast((m) => ({ ...m, status: null, tools: [...(m.tools ?? []), { id: ev.id, name: ev.name, input: ev.input, running: true }] }));
        break;
      case "tool_end":
        patchLast((m) => ({ ...m, tools: (m.tools ?? []).map((t) => (t.id === ev.id ? { ...t, output: ev.output, is_error: ev.is_error, running: false } : t)) }));
        break;
      case "verification": patchLast((m) => ({ ...m, verification: { ok: ev.ok, issues: ev.issues ?? [] }, status: null })); break;
      case "reset": patchLast((m) => ({ ...m, content: "", revised: true })); break;
      case "error": patchLast((m) => ({ ...m, error: ev.message, status: null })); break;
      case "done": patchLast((m) => ({ ...m, id: ev.message.id, model: ev.message.meta?.model, status: null })); break;
      case "title":
        setConversations((cs) => cs.map((c) => (c.id === convId ? { ...c, title: ev.title } : c)));
        break;
    }
  };

  async function run(convId: string, body: { content: string; file_ids: string[]; regenerate?: boolean }) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy(true);
    setAtBottom(true);
    try {
      await streamChat(convId, { ...body, model_id: modelId, agent_mode: agentMode }, handleEvent(convId), ctrl.signal);
    } catch (e: any) {
      if (e.name === "AbortError") patchLast((m) => ({ ...m, interrupted: true }));
      else patchLast((m) => ({ ...m, error: e.message || "Connection lost." }));
    } finally {
      patchLast((m) => ({ ...m, streaming: false, status: null, tools: m.tools?.map((t) => ({ ...t, running: false })) }));
      setBusy(false);
      abortRef.current = null;
      setConversations((cs) => {
        const c = cs.find((x) => x.id === convId);
        return c ? [{ ...c, updated_at: new Date().toISOString() }, ...cs.filter((x) => x.id !== convId)] : cs;
      });
    }
  }

  async function send(text: string, files: FileRef[]) {
    let convId = activeId;
    if (!convId) {
      try {
        const c = await api<Conversation>("/api/conversations", { method: "POST", body: { model_id: modelId } });
        setConversations((cs) => [c, ...cs]);
        convId = c.id;
        setActiveId(c.id);
        window.history.replaceState(null, "", `/chat/${c.id}`);
      } catch (e: any) {
        setBanner(e.message);
        return;
      }
    }
    setMessages((ms) => [
      ...ms,
      { key: key(), role: "user", content: text, files },
      { key: key(), role: "assistant", content: "", streaming: true, tools: [] },
    ]);
    await run(convId, { content: text, file_ids: files.map((f) => f.id) });
  }

  async function regenerate() {
    if (!activeId || busy) return;
    setMessages((ms) => {
      const trimmed = ms[ms.length - 1]?.role === "assistant" ? ms.slice(0, -1) : ms;
      return [...trimmed, { key: key(), role: "assistant", content: "", streaming: true, tools: [] }];
    });
    await run(activeId, { content: "", file_ids: [], regenerate: true });
  }

  async function rename(id: string, title: string) {
    setConversations((cs) => cs.map((c) => (c.id === id ? { ...c, title } : c)));
    await api(`/api/conversations/${id}`, { method: "PATCH", body: { title } }).catch((e) => setBanner(e.message));
  }

  async function remove(id: string) {
    try {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
      setConversations((cs) => cs.filter((c) => c.id !== id));
      if (id === activeId) newChat();
    } catch (e: any) { setBanner(e.message); }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  }

  const changeModel = (id: string) => { setModelId(id); localStorage.setItem("aria.model", id); };
  const toggleAgent = (v: boolean) => { setAgentMode(v); localStorage.setItem("aria.agentMode", v ? "1" : "0"); };

  const composer = (
    <Composer models={models} modelId={modelId} onModelChange={changeModel} onSend={send} busy={busy}
      onStop={() => abortRef.current?.abort()} agentMode={agentMode} onAgentMode={toggleAgent} prefill={prefill} />
  );
  const empty = !activeId && messages.length === 0;
  const title = conversations.find((c) => c.id === activeId)?.title;

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} user={user} conversations={conversations}
        activeId={activeId} onSelect={selectConversation} onNew={newChat} onRename={rename} onDelete={remove}
        onLogout={logout} onMemory={() => setMemoryOpen(true)} />
      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-1 px-2">
          {!sidebarOpen && (
            <>
              <Button variant="ghost" size="icon" title="Open sidebar" onClick={() => setSidebarOpen(true)}><PanelLeft /></Button>
              <Button variant="ghost" size="icon" title="New chat" onClick={newChat}><SquarePen /></Button>
            </>
          )}
          <div className="min-w-0 flex-1 truncate px-2 text-sm font-medium">{title}</div>
        </header>
        {banner && (
          <div className="mx-auto mb-2 flex w-full max-w-3xl items-center justify-between gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm">
            <span>{banner}</span>
            <button className="text-xs underline" onClick={() => setBanner(null)}>Dismiss</button>
          </div>
        )}
        {empty ? (
          <div className="flex flex-1 flex-col items-center justify-center px-4">
            <div className="w-full max-w-3xl space-y-6">
              <EmptyState name={user?.name ?? ""} onPick={(s) => setPrefill({ text: s, n: Date.now() })} />
              {composer}
            </div>
          </div>
        ) : (
          <>
            <div ref={scroller} onScroll={onScroll} className="thin-scroll flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-3xl space-y-8 px-4 pb-10 pt-4">
                {loadingConv && <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>}
                {messages.map((m, i) => (
                  <MessageView key={m.key} m={m} busy={busy} onRegenerate={regenerate}
                    isLast={i === messages.length - 1 && m.role === "assistant"} />
                ))}
              </div>
            </div>
            <div className="relative mx-auto w-full max-w-3xl px-4 pb-4">
              {!atBottom && (
                <Button variant="outline" size="icon" title="Scroll to bottom"
                  className={cn("absolute -top-12 left-1/2 -translate-x-1/2 rounded-full shadow-md")}
                  onClick={() => { setAtBottom(true); scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }}>
                  <ArrowDown />
                </Button>
              )}
              {composer}
              <p className="mt-2 text-center text-xs text-muted-foreground">Aria can make mistakes. Check important information.</p>
            </div>
          </>
        )}
      </main>
      <MemoryDialog open={memoryOpen} onOpenChange={setMemoryOpen} />
    </div>
  );
}
