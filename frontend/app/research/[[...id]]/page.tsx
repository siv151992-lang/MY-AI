import { ResearchApp } from "@/components/research/ResearchApp";

export default async function ResearchPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return <ResearchApp initialId={id?.[0] ?? null} />;
}
