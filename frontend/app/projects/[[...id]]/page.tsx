import { ProjectsApp } from "@/components/hub/ProjectsApp";

export default async function ProjectsPage({ params }: { params: Promise<{ id?: string[] }> }) {
  const { id } = await params;
  return <ProjectsApp id={id?.[0] ?? null} />;
}
