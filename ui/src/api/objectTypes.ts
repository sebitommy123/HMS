import { z } from "zod";
import { api } from "@/api/client";

export const ObjectType = z.object({
  id: z.string().uuid(),
  name: z.string(),
  description: z.string(),
  // Names of trait registry entries enabled on this type, sorted.
  // See @/api/traits for the trait registry shape.
  traits: z.array(z.string()),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ObjectType = z.infer<typeof ObjectType>;

export async function listObjectTypes(search?: string): Promise<ObjectType[]> {
  const qs = search && search.trim() ? `?search=${encodeURIComponent(search.trim())}` : "";
  const raw = await api.get<unknown>(`/object-types${qs}`);
  return z.array(ObjectType).parse(raw);
}

export async function getObjectType(id: string): Promise<ObjectType> {
  const raw = await api.get<unknown>(`/object-types/${encodeURIComponent(id)}`);
  return ObjectType.parse(raw);
}

export interface CreateObjectTypeInput {
  name: string;
  description?: string;
}

export async function createObjectType(input: CreateObjectTypeInput): Promise<ObjectType> {
  const raw = await api.post<unknown>("/object-types", input);
  return ObjectType.parse(raw);
}

export interface UpdateObjectTypeInput {
  name?: string;
  description?: string;
}

export async function updateObjectType(
  id: string,
  input: UpdateObjectTypeInput,
): Promise<ObjectType> {
  const raw = await api.patch<unknown>(
    `/object-types/${encodeURIComponent(id)}`,
    input,
  );
  return ObjectType.parse(raw);
}

export async function deleteObjectType(id: string): Promise<void> {
  await api.delete<unknown>(`/object-types/${encodeURIComponent(id)}`);
}
