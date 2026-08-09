import { z } from "zod";
import { api } from "@/api/client";

export const HealthResponse = z.object({
  status: z.enum(["ok", "degraded"]),
  postgres: z.enum(["reachable", "unreachable"]),
  trino: z.enum(["reachable", "unreachable"]),
});
export type Health = z.infer<typeof HealthResponse>;

/**
 * Fetch Core's health. Returns the parsed body — including the 503 case where
 * Core itself responded but its dependencies aren't reachable.
 *
 * If Core itself is unreachable (network error, CORS rejection, etc.) we let
 * the error propagate so the component can render a distinct state.
 */
export async function fetchHealth(): Promise<Health> {
  try {
    const raw = await api.get<unknown>("/health");
    return HealthResponse.parse(raw);
  } catch (err) {
    // ApiError carries the body even on 5xx — recover the structured health
    // payload from a 503 response.
    if (
      err &&
      typeof err === "object" &&
      "status" in err &&
      "body" in err &&
      typeof (err as { status: unknown }).status === "number"
    ) {
      const parsed = HealthResponse.safeParse((err as { body: unknown }).body);
      if (parsed.success) return parsed.data;
    }
    throw err;
  }
}
