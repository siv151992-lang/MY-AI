"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

type Mode = "server" | "browser" | null;

/** Microphone input. Uses server transcription when configured (OpenAI key), otherwise the browser's own speech
 * recognition. Renders nothing when neither is available, so there is never a dead button. */
export function VoiceButton({ onText }: { onText: (text: string) => void }) {
  const [mode, setMode] = useState<Mode>(null);
  const [state, setState] = useState<"idle" | "recording" | "working">("idle");
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const speech = useRef<any>(null);

  useEffect(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    api<{ server_transcription: boolean }>("/api/voice/status")
      .then((s) => setMode(s.server_transcription && typeof MediaRecorder !== "undefined" && navigator.mediaDevices ? "server" : SR ? "browser" : null))
      .catch(() => setMode(SR ? "browser" : null));
  }, []);

  if (!mode) return null;

  async function start() {
    setError(null);
    if (mode === "browser") {
      const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      const r = new SR();
      r.lang = navigator.language || "en-US";
      r.interimResults = false;
      r.onresult = (e: any) => onText(Array.from(e.results).map((x: any) => x[0].transcript).join(" "));
      r.onerror = (e: any) => setError(e.error === "not-allowed" ? "Microphone permission denied" : "Speech recognition failed");
      r.onend = () => setState("idle");
      speech.current = r;
      r.start();
      setState("recording");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setState("working");
        const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
        const fd = new FormData();
        fd.append("file", blob, "speech.webm");
        try {
          const res = await fetch("/api/voice/transcribe", { method: "POST", body: fd, credentials: "same-origin",
            headers: { "X-Requested-With": "aria" } });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "Transcription failed");
          if (data.text) onText(data.text);
        } catch (e: any) {
          setError(e.message);
        } finally {
          setState("idle");
        }
      };
      rec.current = mr;
      mr.start();
      setState("recording");
    } catch {
      setError("Microphone permission denied");
    }
  }

  function stop() {
    if (mode === "browser") speech.current?.stop();
    else rec.current?.stop();
  }

  return (
    <span className="relative inline-flex">
      <Button variant="ghost" size="icon" disabled={state === "working"}
        title={state === "recording" ? "Stop recording" : mode === "server" ? "Speak (transcribed on your server)" : "Speak (your browser's speech recognition)"}
        onClick={state === "recording" ? stop : start}
        className={state === "recording" ? "text-red-600 animate-pulse" : undefined}>
        {state === "working" ? <Loader2 className="animate-spin" /> : state === "recording" ? <Square className="fill-current !size-3.5" /> : <Mic />}
      </Button>
      {error && <span className="absolute bottom-full left-0 mb-1 whitespace-nowrap rounded bg-destructive px-2 py-0.5 text-xs text-white" onClick={() => setError(null)}>{error}</span>}
    </span>
  );
}

/** Read text aloud with the browser's speech synthesis (no server needed). Hidden where unsupported. */
export function SpeakButton({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false);
  const [ok, setOk] = useState(false);
  useEffect(() => setOk(typeof window !== "undefined" && "speechSynthesis" in window), []);
  if (!ok || !text) return null;
  const plain = text.replace(/```[\s\S]*?```/g, " (code block) ").replace(/[#*_`>\[\]()|-]/g, " ").slice(0, 6000);
  return (
    <Button variant="ghost" size="icon-sm" title={speaking ? "Stop reading" : "Read aloud"} onClick={() => {
      if (speaking) { speechSynthesis.cancel(); setSpeaking(false); return; }
      const u = new SpeechSynthesisUtterance(plain);
      u.onend = () => setSpeaking(false);
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
      setSpeaking(true);
    }}>
      {speaking ? <Square className="fill-current !size-3" /> : <SpeakerIcon />}
    </Button>
  );
}

function SpeakerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 5 6 9H2v6h4l5 4V5z" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  );
}
