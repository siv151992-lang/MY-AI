import { DocumentsApp } from "@/components/documents/DocumentsApp";

export default async function DocumentsPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return <DocumentsApp initialId={id?.[0] ?? null} />;
}
