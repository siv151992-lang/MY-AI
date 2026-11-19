"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ToolInfo } from "@/lib/hub";
import type { ModelInfo } from "@/lib/types";

export function useCatalog() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  useEffect(() => {
    api<{ models: ModelInfo[] }>("/api/models").then((m) => setModels(m.models)).catch(() => {});
    api<ToolInfo[]>("/api/tools").then(setTools).catch(() => {});
  }, []);
  return { models, tools };
}
