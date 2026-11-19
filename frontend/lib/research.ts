/** Deep research types and pure helpers (dependency-free so they run under node --test). */

export type ResearchStatus = "queued" | "planning" | "searching" | "reading" | "extracting" | "cross_checking"
  | "verifying" | "writing" | "completed" | "failed" | "cancelled";
export type ResearchSource = {
  n: number | null; url: string; title: string; domain: string; reliability: number; reliability_label: "high" | "medium" | "low" | "unknown";
  reliability_reason: string; query: string; snippet: string; status: "read" | "failed" | "irrelevant"; error: string;
  purpose: "search" | "verification"; fetched_at: string;
};
export type ResearchClaim = {
  n: number; subq: number; text: string; quote: string; source: number; importance: "high" | "medium" | "low";
  status: "corroborated" | "single_source" | "conflicting" | "unverified"; supporting: number[]; conflicting: number[]; note: string;
};
export type Conflict = { topic: string; positions: { summary: string; claims: number[] }[]; assessment: string; subquestion: number };
export type ResearchRun = {
  id: string; question: string; depth: "quick" | "standard" | "deep"; status: ResearchStatus; model_id: string | null;
  plan: { question: string; queries: string[] }[]; analysis: { groups?: unknown[]; conflicts?: Conflict[] };
  stats: Record<string, any>; summary: string; report: string; error: string; created_at: string; finished_at: string | null;
};
export type ResearchDetail = ResearchRun & { active: boolean; sources: ResearchSource[]; claims: ResearchClaim[] };
export type ResearchEvent = { seq: number; type: string; data: any; at: string };

export const PHASES: { id: ResearchStatus; label: string }[] = [
  { id: "planning", label: "Plan" }, { id: "searching", label: "Search" }, { id: "reading", label: "Read" },
  { id: "extracting", label: "Extract" }, { id: "cross_checking", label: "Compare" }, { id: "verifying", label: "Verify" },
  { id: "writing", label: "Write" },
];
export const TERMINAL: ResearchStatus[] = ["completed", "failed", "cancelled"];

/** Turn [S3] / [S2, S5] markers into links to the actual source URLs. Unknown numbers are left as plain text. */
export function linkCitations(markdown: string, sources: { n: number | null; url: string }[]): string {
  const urls = new Map(sources.filter((s) => s.n).map((s) => [s.n as number, s.url]));
  return markdown.replace(/\[(S\d+(?:\s*[,;]\s*S\d+)*)\](?!\()/g, (_m, inner: string) =>
    inner.split(/\s*[,;]\s*/).map((tok) => {
      const url = urls.get(Number(tok.slice(1)));
      return url ? `[[${tok}]](${url})` : `[${tok}]`;
    }).join(""));
}
