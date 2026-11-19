import { AutomationsApp } from "@/components/hub/AutomationsApp";

export default async function AutomationsPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return <AutomationsApp id={id?.[0] ?? null} />;
}
