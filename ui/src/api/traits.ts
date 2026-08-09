/**
 * Trait discovery + object-type trait attachments.
 *
 * Discovery (`listTraits`) is a one-shot — the registry is hardcoded
 * in Core. Attach/detach use the object-type endpoints (PUT/DELETE
 * `/object-types/{id}/traits/{name}`) which return the updated
 * ObjectType so the caller can refresh in place.
 */

import { z } from "zod";
import { api } from "@/api/client";
import { ObjectType } from "@/api/objectTypes";

export const TraitDescriptor = z.object({
  name: z.string(),
  description: z.string(),
  required_config_keys: z.array(z.string()),
});
export type TraitDescriptor = z.infer<typeof TraitDescriptor>;

export async function listTraits(): Promise<TraitDescriptor[]> {
  const raw = await api.get<unknown>("/traits");
  return z.array(TraitDescriptor).parse(raw);
}

export async function addTraitToObjectType(
  typeId: string,
  traitName: string,
): Promise<ObjectType> {
  const raw = await api.put<unknown>(
    `/object-types/${encodeURIComponent(typeId)}/traits/${encodeURIComponent(traitName)}`,
  );
  return ObjectType.parse(raw);
}

export async function removeTraitFromObjectType(
  typeId: string,
  traitName: string,
): Promise<ObjectType> {
  const raw = await api.delete<unknown>(
    `/object-types/${encodeURIComponent(typeId)}/traits/${encodeURIComponent(traitName)}`,
  );
  return ObjectType.parse(raw);
}
