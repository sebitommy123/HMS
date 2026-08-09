import { aiApi, AI_URL } from "@/api/ai-client";

/** One thing the user is looking at that the agent can pull on demand. */
export interface Observation {
  description: string;
  kind: "table" | "json" | "text" | "form" | "list";
  data: unknown;
}

export interface ViewContext {
  route: string;
  title: string | null;
  entity: { type: string; id?: string; [k: string]: unknown } | null;
  observations: Record<string, Observation & { updated_at: number }>;
}

/**
 * Publish what the user is currently looking at to their conversation. The
 * agent reads it via get_current_view / read_observation. Keyed by
 * conversation id, so it's isolated per user/chat.
 *
 * Best-effort: uses sendBeacon-style fire-and-forget semantics via a normal
 * PUT but never throws to callers (a failed publish must not break the app).
 */
export async function putViewContext(
  conversationId: string,
  view: ViewContext,
): Promise<void> {
  try {
    await aiApi.put<void>(
      `/conversations/${encodeURIComponent(conversationId)}/view-context`,
      view,
    );
  } catch {
    /* publishing is advisory — never surface a failure to the user */
  }
}

/** Re-export so callers don't need to reach into ai-client for the base URL. */
export { AI_URL };
