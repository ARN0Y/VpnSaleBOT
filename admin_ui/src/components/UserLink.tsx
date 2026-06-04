import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

// Clickable user reference usable from any table/row → opens the user's profile.
export function UserLink({
  userId,
  name,
  username,
  className,
}: {
  userId: number | string;
  name?: unknown;
  username?: unknown;
  className?: string;
}) {
  const display = name ? String(name) : username ? `@${String(username)}` : String(userId);
  const sub = name && username ? `@${String(username)}` : name || username ? String(userId) : "";
  return (
    <Link
      to={`/users/${userId}`}
      className={cn("group inline-flex flex-col text-right transition hover:opacity-90", className)}
    >
      <span className="font-bold text-white underline-offset-4 group-hover:underline">{display}</span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </Link>
  );
}
