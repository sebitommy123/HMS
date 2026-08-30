import { z } from "zod";
import { api } from "@/api/client";

export const CatalogStatus = z.enum(["enabled", "disabled", "broken", "down"]);
export type CatalogStatus = z.infer<typeof CatalogStatus>;

export const Catalog = z.object({
  name: z.string(),
  connector: z.string(),
  properties: z.record(z.string(), z.string()),
  status: CatalogStatus,
  last_error: z.string().nullable(),
  factory_count: z.number().int().nonnegative(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Catalog = z.infer<typeof Catalog>;

export const TrinoCatalogRow = z.object({
  name: z.string(),
  connector: z.string(),
});
export type TrinoCatalogRow = z.infer<typeof TrinoCatalogRow>;

export const ReconcileAction = z.object({
  kind: z.enum(["create", "drop"]),
  name: z.string(),
  ok: z.boolean(),
  error: z.string().nullable(),
});
export type ReconcileAction = z.infer<typeof ReconcileAction>;

export const ReconcileResult = z.object({
  all_ok: z.boolean(),
  actions: z.array(ReconcileAction),
});
export type ReconcileResult = z.infer<typeof ReconcileResult>;

export async function listCatalogs(): Promise<Catalog[]> {
  const raw = await api.get<unknown>("/catalogs");
  return z.array(Catalog).parse(raw);
}

export async function getCatalog(name: string): Promise<Catalog> {
  const raw = await api.get<unknown>(`/catalogs/${encodeURIComponent(name)}`);
  return Catalog.parse(raw);
}

export interface CreateCatalogInput {
  name: string;
  connector: string;
  properties: Record<string, string>;
  // Flex-only: the Python module source. When connector === "flex"
  // and source is provided, Core materializes it and auto-populates
  // flex.module_path. Mutually exclusive with properties["flex.module_path"].
  source?: string;
}

const CreateCatalogResponse = z.object({
  catalog: Catalog,
  reconcile: ReconcileResult,
});
export type CreateCatalogResponse = z.infer<typeof CreateCatalogResponse>;

export async function createCatalog(input: CreateCatalogInput): Promise<CreateCatalogResponse> {
  const raw = await api.post<unknown>("/catalogs", input);
  return CreateCatalogResponse.parse(raw);
}

export async function deleteCatalog(name: string): Promise<void> {
  await api.delete<unknown>(`/catalogs/${encodeURIComponent(name)}`);
}

export interface UpdateCatalogInput {
  connector?: string;
  // ``properties`` is a FULL REPLACEMENT — to add or remove a single key,
  // start from the current dict, mutate, then call this.
  properties?: Record<string, string>;
}

// On a no-op PATCH the server returns ``reconcile: null``. On any real change
// it returns the same shape as create.
const UpdateCatalogResponse = z.object({
  catalog: Catalog,
  reconcile: ReconcileResult.nullable(),
});
export type UpdateCatalogResponse = z.infer<typeof UpdateCatalogResponse>;

export async function updateCatalog(
  name: string,
  input: UpdateCatalogInput,
): Promise<UpdateCatalogResponse> {
  const raw = await api.patch<unknown>(
    `/catalogs/${encodeURIComponent(name)}`,
    input,
  );
  return UpdateCatalogResponse.parse(raw);
}

export async function fetchTrinoState(): Promise<TrinoCatalogRow[]> {
  const raw = await api.get<unknown>("/trino/state");
  return z.array(TrinoCatalogRow).parse(raw);
}

export async function triggerReconcile(): Promise<ReconcileResult> {
  const raw = await api.post<unknown>("/reconcile");
  return ReconcileResult.parse(raw);
}
