import { z } from "zod";
import { api } from "@/api/client";

// Wire schemas match Core's /query and /preview-query-plan response shapes.

const ResultStatus = z.object({
  all_ok: z.boolean(),
  factories_used: z.array(
    z.object({
      factory_id: z.string(),
      data_source_path: z.string(),
    }),
  ),
  factories_skipped: z.array(
    z.object({
      factory_id: z.string(),
      data_source_path: z.string(),
      reason: z.string(),
    }),
  ),
  errors: z.array(
    z.object({
      kind: z.string(),
      message: z.string(),
    }),
  ),
  sql: z.string(),
  trino_query_id: z.string().nullable(),
  elapsed_seconds: z.number(),
});
export type ResultStatus = z.infer<typeof ResultStatus>;

export const QueryResult = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  result_status: ResultStatus,
});
export type QueryResult = z.infer<typeof QueryResult>;

export const QueryPlanPreview = z.object({
  from: z.string(),
  object_type_id: z.string(),
  limit: z.number(),
  timeout_seconds: z.number(),
  sql: z.string(),
  factories_used: z.array(
    z.object({
      factory_id: z.string(),
      data_source_id: z.string(),
      data_source_path: z.string(),
      use_all_columns: z.boolean(),
      column_spec: z.array(z.string()),
    }),
  ),
  factories_skipped: z.array(
    z.object({
      factory_id: z.string(),
      data_source_path: z.string(),
      reason: z.string(),
    }),
  ),
});
export type QueryPlanPreview = z.infer<typeof QueryPlanPreview>;

export interface ObjectsQuery {
  from: string;
  limit?: number;
  timeout_seconds?: number;
}

function buildBody(q: ObjectsQuery): Record<string, unknown> {
  const body: Record<string, unknown> = { from: q.from };
  if (q.limit !== undefined) body.limit = q.limit;
  if (q.timeout_seconds !== undefined) body.timeout_seconds = q.timeout_seconds;
  return body;
}

export async function executeObjectsQuery(q: ObjectsQuery): Promise<QueryResult> {
  const raw = await api.post<unknown>("/query", buildBody(q));
  return QueryResult.parse(raw);
}

export async function previewQueryPlan(q: ObjectsQuery): Promise<QueryPlanPreview> {
  const raw = await api.post<unknown>("/preview-query-plan", buildBody(q));
  return QueryPlanPreview.parse(raw);
}
