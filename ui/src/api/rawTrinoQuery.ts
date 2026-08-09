import { z } from "zod";
import { api } from "@/api/client";

export const QueryRequest = z.object({
  sql: z.string().min(1),
  timeout_seconds: z.number().int().positive().max(60).optional(),
  max_rows: z.number().int().positive().max(100_000).optional(),
});
export type QueryRequest = z.infer<typeof QueryRequest>;

export const QueryResult = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number(),
  truncated: z.boolean(),
  elapsed_seconds: z.number(),
  query_id: z.string().nullable(),
  applied_limits: z.object({
    timeout_seconds: z.number(),
    max_rows: z.number(),
  }),
});
export type QueryResult = z.infer<typeof QueryResult>;

/**
 * Execute a raw Trino SQL query through Core's debugging passthrough.
 * The `/query` URL is reserved for the upcoming semantic Core query layer;
 * this endpoint is for direct SQL only.
 */
export async function executeRawTrinoQuery(
  req: QueryRequest,
): Promise<QueryResult> {
  const raw = await api.post<unknown>("/raw-trino-query", req);
  return QueryResult.parse(raw);
}
