/**
 * Flex module source CRUD + preview.
 *
 * The catalog row owns lifecycle (`/api/catalogs` POST/DELETE).
 * Source updates between catalog create and catalog delete go
 * through here. Preview runs in a transient catalog server-side
 * and returns sample rows so the UI can render before commit.
 */

import { z } from "zod";
import { api } from "@/api/client";

export const FlexModule = z.object({
  id: z.string().uuid(),
  catalog_name: z.string(),
  source_text: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type FlexModule = z.infer<typeof FlexModule>;

export const FlexPreviewColumn = z.object({
  name: z.string(),
  type: z.string(),
});
export type FlexPreviewColumn = z.infer<typeof FlexPreviewColumn>;

export const FlexPreviewTable = z.object({
  schema: z.string(),
  name: z.string(),
  columns: z.array(FlexPreviewColumn).optional(),
  sample_columns: z.array(z.string()).optional(),
  sample_rows: z.array(z.array(z.unknown())).optional(),
  // If preview hit a per-table error (e.g. read_table threw), Core
  // returns the error here instead of columns/rows so the UI can
  // surface it without failing the whole preview.
  error: z.string().optional(),
});
export type FlexPreviewTable = z.infer<typeof FlexPreviewTable>;

export const FlexPreviewResult = z.object({
  tables: z.array(FlexPreviewTable),
});
export type FlexPreviewResult = z.infer<typeof FlexPreviewResult>;

export async function getFlexModule(catalogName: string): Promise<FlexModule> {
  const raw = await api.get<unknown>(`/flex-modules/${encodeURIComponent(catalogName)}`);
  return FlexModule.parse(raw);
}

export async function replaceFlexModule(
  catalogName: string,
  source: string,
): Promise<FlexModule> {
  const raw = await api.put<unknown>(
    `/flex-modules/${encodeURIComponent(catalogName)}`,
    { source },
  );
  return FlexModule.parse(raw);
}

export async function previewFlexModule(
  source: string,
  sampleLimit = 10,
): Promise<FlexPreviewResult> {
  const raw = await api.post<unknown>("/flex-modules/preview", {
    source,
    sample_limit: sampleLimit,
  });
  return FlexPreviewResult.parse(raw);
}
