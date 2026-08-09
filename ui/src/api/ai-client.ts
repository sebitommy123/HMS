/**
 * Tiny typed fetch wrapper for the DataPro AI service.
 *
 * Same shape as ./client.ts but pointed at the AI URL. AI lives at its own
 * origin (different process, different port) and needs its own ApiError so
 * callers can distinguish "Core was unreachable" from "AI was unreachable".
 */

export const AI_URL: string =
  (import.meta.env.VITE_AI_URL as string | undefined) ?? "http://127.0.0.1:5002";

export class AiApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${AI_URL}${path}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const contentType = res.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await res.json()
    : await res.text();
  if (!res.ok) {
    const msg =
      typeof payload === "object" && payload !== null && "error" in payload
        ? String((payload as { error: unknown }).error)
        : `HTTP ${res.status}`;
    throw new AiApiError(res.status, payload, msg);
  }
  return payload as T;
}

export const aiApi = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};
