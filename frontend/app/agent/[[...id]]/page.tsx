import { AgentApp } from "@/components/agent/AgentApp";

export default async function AgentPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return <AgentApp initialRunId={id?.[0] ?? null} />;
}
