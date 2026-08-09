import { z } from "zod";
import { api } from "@/api/client";

// Data sources are sync-owned: discovered from Trino by Core's reconciler,
// never created/edited/deleted from the client. This module is read-only.
export const DataSourceStatus = z.enum(["active", "deleted"]);
export type DataSourceStatus = z.infer<typeof DataSourceStatus>;

export const DataSource = z.object({
  id: z.string().uuid(),
  catalog_name: z.string(),
  schema_name: z.string(),
  table_name: z.string(),
  path: z.string(),
  description: z.string(),
  status: DataSourceStatus,
  created_at: z.string(),
  updated_at: z.string(),
});
export type DataSource = z.infer<typeof DataSource>;

export async function listDataSources(catalog?: string): Promise<DataSource[]> {
  const qs = catalog ? `?catalog=${encodeURIComponent(catalog)}` : "";
  const raw = await api.get<unknown>(`/data-sources${qs}`);
  return z.array(DataSource).parse(raw);
}

export async function getDataSource(id: string): Promise<DataSource> {
  const raw = await api.get<unknown>(`/data-sources/${encodeURIComponent(id)}`);
  return DataSource.parse(raw);
}

export const DataSourceColumn = z.object({
  name: z.string(),
  type: z.string(),
});
export type DataSourceColumn = z.infer<typeof DataSourceColumn>;

export const DataSourceColumns = z.object({
  data_source_id: z.string().uuid(),
  path: z.string(),
  columns: z.array(DataSourceColumn),
});
export type DataSourceColumns = z.infer<typeof DataSourceColumns>;

export async function getDataSourceColumns(
  id: string,
): Promise<DataSourceColumns> {
  const raw = await api.get<unknown>(
    `/data-sources/${encodeURIComponent(id)}/columns`,
  );
  return DataSourceColumns.parse(raw);
}
