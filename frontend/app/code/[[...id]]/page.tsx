import { CodeApp } from "@/components/code/CodeApp";
import { WorkspaceHome } from "@/components/code/WorkspaceHome";

export default async function CodePage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return id?.[0] ? <CodeApp wsId={id[0]} /> : <WorkspaceHome />;
}
