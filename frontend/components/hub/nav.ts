import { Activity, BarChart3, BookOpenCheck, Bot, Code2, FileText, FolderKanban, Home, MessageSquare, Plug, Sparkles,
  UserCog, Users, Workflow } from "lucide-react";

export const NAV = [
  { href: "/home", label: "Home", icon: Home },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/research", label: "Deep research", icon: BookOpenCheck },
  { href: "/code", label: "Code workspace", icon: Code2 },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/team", label: "Agent team", icon: Users },
  { href: "/agent", label: "Autonomous agent", icon: Bot },
  { href: "/agents", label: "Custom agents", icon: UserCog },
  { href: "/skills", label: "Skills", icon: Sparkles },
  { href: "/automations", label: "Automations", icon: Workflow },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/tasks", label: "Tasks", icon: Activity },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
];
