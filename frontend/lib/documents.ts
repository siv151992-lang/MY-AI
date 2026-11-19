export type DocType = "pdf" | "docx" | "xlsx" | "pptx" | "csv" | "txt" | "image";
export type Doc = {
  id: string; filename: string; mime_type: string; size_bytes: number; kind: "document" | "image"; status: string;
  doc_type: DocType | null; pages: number | null; summary: string | null; meta: Record<string, any>; ocr: boolean;
  error: string; warnings: string[]; created_at: string; updated_at: string; owned: boolean; chunks: number; tables: number;
  shared_with: number; owner_name: string | null;
};
export type ColumnProfile = { name: string; type: "number" | "text" | "date" | "boolean"; non_empty: number; empty: number; distinct: number;
  min?: number | string; max?: number | string; mean?: number; sum?: number; median?: number; top?: [string, number][] };
export type DocTable = { id: string; idx: number; name: string; source: string; location: string; columns: string[];
  total_rows: number; formulas: number; profile: { rows: number; analysed_rows: number; columns: ColumnProfile[] }; rows?: any[][] };
export type Grant = { user_id: string; email: string; name: string; role: string; created_at: string };
export type DocDetail = Doc & { tables_list: DocTable[]; access: Grant[] | null };
export type Citation = { n: number; file_id: string; filename: string; page: number | null; section: string; location: string; snippet: string };
export type ExtractedField = { field: string; description: string; value: unknown; quote: string | null; status: "verified" | "unverified" | "not_found" };

export function fmtSize(n: number): string {
  return n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
}

/** Replace [P3] markers with links to an in-page anchor for that citation. */
export function linkPassages(md: string, cites: Citation[]): string {
  const known = new Set(cites.map((c) => c.n));
  return md.replace(/\[(P\d+(?:\s*[,;]\s*P\d+)*)\](?!\()/g, (_m, inner: string) =>
    inner.split(/\s*[,;]\s*/).map((t) => (known.has(Number(t.slice(1))) ? `[[${t}]](#cite-${t.slice(1)})` : `[${t}]`)).join(""));
}
