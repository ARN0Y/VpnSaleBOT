import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function toman(value: unknown): string {
  const n = Number((value as number) || 0);
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "0";
}

export function formatGb(value: unknown): string {
  const n = Number((value as number) || 0);
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "0";
}

// Panel traffic fields are stored in BYTES — convert to GB for display.
export function gbFromBytes(bytes: unknown): string {
  const n = Number((bytes as number) || 0);
  if (!Number.isFinite(n) || n <= 0) return "0";
  return (n / 1024 ** 3).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/** Just the day, no clock. Use where a range is shown: the time adds nothing
 *  and its comma makes the bidi algorithm reorder the two dates into nonsense. */
export function jalaliDay(ts: unknown): string {
  const seconds = Number((ts as number) || 0);
  if (!seconds) return "—";
  try {
    return new Intl.DateTimeFormat("fa-IR", { dateStyle: "short" }).format(new Date(seconds * 1000));
  } catch {
    return new Date(seconds * 1000).toLocaleDateString();
  }
}

export function jalaliDate(ts: unknown): string {
  const seconds = Number((ts as number) || 0);
  if (!seconds) return "—";
  try {
    return new Intl.DateTimeFormat("fa-IR", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(seconds * 1000));
  } catch {
    return new Date(seconds * 1000).toLocaleString();
  }
}
