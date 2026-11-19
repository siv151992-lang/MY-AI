import { TeamApp } from "@/components/team/TeamApp";

export default async function TeamPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return <TeamApp initialId={id?.[0] ?? null} />;
}
