import { z } from "zod";
import { api } from "@/api/client";

export const ObjectFactoryStatus = z.enum(["ok", "broken"]);
export type ObjectFactoryStatus = z.infer<typeof ObjectFactoryStatus>;

export const ObjectFactory = z.object({
  id: z.string().uuid(),
  // Denormalized from the parent data source for UI convenience.
  data_source_id: z.string().uuid(),
  catalog_name: z.string().nullable(),
  schema_name: z.string().nullable(),
  table_name: z.string().nullable(),
  data_source_path: z.string().nullable(),
  object_type_id: z.string().uuid(),
  object_type_name: z.string().nullable(),
  // Denormalized so the factory UI can render trait widgets without a
  // second /object-types/{id} fetch. Sorted by name for stable output.
  object_type_traits: z.array(z.string()),
  description: z.string(),
  use_all_columns: z.boolean(),
  column_spec: z.array(z.string()),
  // Per-trait config: { trait_name: { trait-specific keys } }.
  // Required when the parent object type has traits enabled — missing
  // entries here surface as broken status with a precise last_error.
  trait_config: z.record(z.string(), z.record(z.string(), z.unknown())),
  status: ObjectFactoryStatus,
  last_error: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ObjectFactory = z.infer<typeof ObjectFactory>;

export interface ListObjectFactoriesFilter {
  catalog?: string;
  data_source_id?: string;
  object_type_id?: string;
}

export async function listObjectFactories(
  filter: ListObjectFactoriesFilter = {},
): Promise<ObjectFactory[]> {
  const params = new URLSearchParams();
  if (filter.catalog) params.set("catalog", filter.catalog);
  if (filter.data_source_id)
    params.set("data_source_id", filter.data_source_id);
  if (filter.object_type_id) params.set("object_type_id", filter.object_type_id);
  const qs = params.toString();
  const raw = await api.get<unknown>(
    `/object-factories${qs ? `?${qs}` : ""}`,
  );
  return z.array(ObjectFactory).parse(raw);
}

export async function getObjectFactory(id: string): Promise<ObjectFactory> {
  const raw = await api.get<unknown>(`/object-factories/${encodeURIComponent(id)}`);
  return ObjectFactory.parse(raw);
}

export interface CreateObjectFactoryInput {
  data_source_id: string;
  object_type_id: string;
  description?: string;
  use_all_columns?: boolean;
  column_spec?: string[];
  trait_config?: Record<string, Record<string, unknown>>;
}

export async function createObjectFactory(
  input: CreateObjectFactoryInput,
): Promise<ObjectFactory> {
  const raw = await api.post<unknown>("/object-factories", input);
  return ObjectFactory.parse(raw);
}

export interface UpdateObjectFactoryInput {
  description?: string;
  use_all_columns?: boolean;
  column_spec?: string[];
  // Fully REPLACES the trait_config dict — to change one trait's slot,
  // GET first and merge.
  trait_config?: Record<string, Record<string, unknown>>;
}

export async function updateObjectFactory(
  id: string,
  input: UpdateObjectFactoryInput,
): Promise<ObjectFactory> {
  const raw = await api.patch<unknown>(
    `/object-factories/${encodeURIComponent(id)}`,
    input,
  );
  return ObjectFactory.parse(raw);
}

export async function deleteObjectFactory(id: string): Promise<void> {
  await api.delete<unknown>(`/object-factories/${encodeURIComponent(id)}`);
}
