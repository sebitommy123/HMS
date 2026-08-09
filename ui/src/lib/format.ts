/** Compact relative-time formatter — "3m ago", "2h ago", "5d ago". */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.floor((Date.now() - then) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

/** A short summary of a properties dict, e.g. "3 properties" or the only key if one. */
export function summarizeProperties(props: Record<string, string>): string {
  const keys = Object.keys(props);
  if (keys.length === 0) return "—";
  if (keys.length === 1) return keys[0];
  return `${keys.length} properties`;
}
