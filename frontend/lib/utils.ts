import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Works on plain-http LAN addresses too (crypto.randomUUID needs a secure context). */
export function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

export function groupByDate<T extends { updated_at: string }>(items: T[]) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86_400_000;
  const groups: { label: string; items: T[] }[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 days", items: [] },
    { label: "Previous 30 days", items: [] },
    { label: "Older", items: [] },
  ];
  for (const it of items) {
    const t = new Date(it.updated_at).getTime();
    const g = t >= startOfToday ? 0 : t >= startOfToday - day ? 1 : t >= startOfToday - 7 * day ? 2
      : t >= startOfToday - 30 * day ? 3 : 4;
    groups[g].items.push(it);
  }
  return groups.filter((g) => g.items.length);
}

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}
