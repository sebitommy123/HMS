import { z } from "zod";
import { aiApi, AiApiError } from "@/api/ai-client";

// ---- Action + changeset schemas --------------------------------------------
//
// Mirrors the authoritative action schema in
// `ai/src/datapro_ai/staging/actions.py`. Modeled loosely (op + passthrough)
// like ContentBlock: the server owns the exact shape per op and the renderer
// switches on `op`, so we don't want to break when a field is added.

export const Ref = z
  .object({ source: z.enum(["prod", "action"]) })
  .catchall(z.unknown());
export type Ref = z.infer<typeof Ref>;

export const StagedAction = z
  .object({ op: z.string(), id: z.string() })
  .catchall(z.unknown());
export type StagedAction = z.infer<typeof StagedAction>;

export const StageTest = z
  .object({ name: z.string(), from: z.string() })
  .catchall(z.unknown());
export type StageTest = z.infer<typeof StageTest>;

export const Changeset = z.object({
  actions: z.array(StagedAction),
  tests: z.array(StageTest),
});
export type Changeset = z.infer<typeof Changeset>;

export const PromoteConflict = z
  .object({ kind: z.string() })
  .catchall(z.unknown());
export type PromoteConflict = z.infer<typeof PromoteConflict>;

export const ApplyResult = z.object({
  applied: z.boolean(),
  result: z.record(z.string(), z.number()).optional(),
  conflicts: z.array(PromoteConflict).optional(),
});
export type ApplyResult = z.infer<typeof ApplyResult>;

export const TestResult = z
  .object({ name: z.string().nullable(), passed: z.boolean(), objects: z.number().optional() })
  .catchall(z.unknown());
export const BuildTestResult = z.object({
  tests: z.array(TestResult),
  all_passed: z.boolean().nullable(),
});
export type BuildTestResult = z.infer<typeof BuildTestResult>;

// ---- API calls -------------------------------------------------------------

function base(id: string): string {
  return `/conversations/${encodeURIComponent(id)}/changeset`;
}

export async function getChangeset(id: string): Promise<Changeset> {
  return Changeset.parse(await aiApi.get<unknown>(base(id)));
}

export async function removeAction(id: string, actionId: string): Promise<StagedAction[]> {
  const raw = await aiApi.delete<unknown>(`${base(id)}/actions/${encodeURIComponent(actionId)}`);
  return z.object({ actions: z.array(StagedAction) }).parse(raw).actions;
}

export async function reorderActions(id: string, actionIds: string[]): Promise<StagedAction[]> {
  const raw = await aiApi.post<unknown>(`${base(id)}/reorder`, { action_ids: actionIds });
  return z.object({ actions: z.array(StagedAction) }).parse(raw).actions;
}

export async function clearChangeset(id: string): Promise<StagedAction[]> {
  const raw = await aiApi.post<unknown>(`${base(id)}/clear`);
  return z.object({ actions: z.array(StagedAction) }).parse(raw).actions;
}

export async function buildAndTestStage(id: string): Promise<BuildTestResult> {
  return BuildTestResult.parse(await aiApi.post<unknown>(`${base(id)}/build-and-test`));
}

/**
 * Promote the changeset to production. Resolves to ``{applied:true, result}`` on
 * success, or ``{applied:false, conflicts}`` when production drifted (HTTP 409) —
 * both are normal outcomes the UI renders, so the 409 is caught here rather than
 * thrown.
 */
export async function applyChangeset(id: string): Promise<ApplyResult> {
  try {
    return ApplyResult.parse(await aiApi.post<unknown>(`${base(id)}/apply`));
  } catch (err) {
    if (err instanceof AiApiError && err.status === 409) {
      const parsed = ApplyResult.safeParse(err.body);
      if (parsed.success) return parsed.data;
    }
    throw err;
  }
}
