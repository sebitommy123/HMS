import { z } from "zod";
import { aiApi } from "@/api/ai-client";

// ---- Content blocks ---------------------------------------------------------
//
// Mirrors Anthropic's content-blocks shape. We persist these verbatim on the
// server, so the UI gets the same JSON the model produced. We model the union
// loosely (just .type) because (a) Anthropic adds new block types and we
// shouldn't break, and (b) the renderer switches on .type anyway.

export const ContentBlock = z
  .object({
    type: z.string(),
  })
  .catchall(z.unknown());
export type ContentBlock = z.infer<typeof ContentBlock>;

// Narrowing helpers — the renderer switches on .type, so we use these to give
// TS a useful narrowed type per branch instead of repeating `as` casts.

export interface TextBlock extends ContentBlock {
  type: "text";
  text: string;
}
export interface ThinkingBlock extends ContentBlock {
  type: "thinking";
  thinking: string;
}
export interface ToolUseBlock extends ContentBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
}
export interface ToolResultBlock extends ContentBlock {
  type: "tool_result";
  tool_use_id: string;
  content: string | ContentBlock[];
  is_error?: boolean;
}

export const Usage = z.object({
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  cache_read_tokens: z.number().nullable(),
  cache_creation_tokens: z.number().nullable(),
});

export const Message = z.object({
  id: z.string(),
  conversation_id: z.string(),
  position: z.number(),
  role: z.string(),
  content: z.array(ContentBlock),
  stop_reason: z.string().nullable(),
  usage: Usage.nullable(),
  created_at: z.string(),
});
export type Message = z.infer<typeof Message>;

export const Conversation = z.object({
  id: z.string(),
  title: z.string(),
  model: z.string(),
  system_prompt: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Conversation = z.infer<typeof Conversation>;

export const ConversationListItem = Conversation.extend({
  message_count: z.number(),
  preview: z.string().nullable(),
});
export type ConversationListItem = z.infer<typeof ConversationListItem>;

export const ConversationDetail = Conversation.extend({
  messages: z.array(Message),
});
export type ConversationDetail = z.infer<typeof ConversationDetail>;

export const SendMessageResponse = z.object({
  conversation_id: z.string(),
  messages: z.array(Message),
  final_stop_reason: z.string(),
  iterations: z.number(),
  truncated_by_iteration_cap: z.boolean(),
});
export type SendMessageResponse = z.infer<typeof SendMessageResponse>;

// ---- API calls --------------------------------------------------------------

export async function listConversations(): Promise<ConversationListItem[]> {
  const raw = await aiApi.get<unknown>("/conversations");
  return z.array(ConversationListItem).parse(raw);
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const raw = await aiApi.get<unknown>(`/conversations/${encodeURIComponent(id)}`);
  return ConversationDetail.parse(raw);
}

export interface CreateConversationInput {
  title?: string;
  model?: string;
  system_prompt?: string;
}

export async function createConversation(input: CreateConversationInput): Promise<Conversation> {
  const raw = await aiApi.post<unknown>("/conversations", input);
  return Conversation.parse(raw);
}

export async function updateConversation(
  id: string,
  input: { title?: string; system_prompt?: string },
): Promise<Conversation> {
  const raw = await aiApi.patch<unknown>(`/conversations/${encodeURIComponent(id)}`, input);
  return Conversation.parse(raw);
}

export async function deleteConversation(id: string): Promise<void> {
  await aiApi.delete<unknown>(`/conversations/${encodeURIComponent(id)}`);
}

export async function sendMessage(id: string, text: string): Promise<SendMessageResponse> {
  const raw = await aiApi.post<unknown>(
    `/conversations/${encodeURIComponent(id)}/messages`,
    { text },
  );
  return SendMessageResponse.parse(raw);
}

// ---- Streaming -------------------------------------------------------------

export type StreamEvent =
  | { type: "stream_start"; conversation_id: string; user_message: Message }
  | { type: "assistant_start"; iteration: number }
  | { type: "text_delta"; text: string }
  | { type: "assistant_persisted"; message: Message }
  | {
      type: "tool_executing";
      tool_use_id: string;
      name: string;
      input: Record<string, unknown>;
    }
  | { type: "tool_result"; tool_use_id: string; output: string; is_error: boolean }
  | { type: "tool_results_persisted"; message: Message }
  | { type: "title_updated"; title: string }
  | {
      type: "stream_done";
      final_stop_reason: string;
      iterations: number;
      truncated_by_iteration_cap: boolean;
    }
  | { type: "stream_error"; error: string; details: string | null };

import { AI_URL } from "@/api/ai-client";

/**
 * Open an SSE stream to the messages endpoint and yield each parsed event.
 *
 * Uses raw fetch + ReadableStream so we can carry the JSON body — the browser's
 * built-in `EventSource` is GET-only and can't send a request body. Parser is
 * intentionally small: split incoming bytes on `\n\n`, then read `event:` /
 * `data:` lines from each frame.
 */
export async function* sendMessageStream(
  id: string,
  text: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent, void, void> {
  const res = await fetch(
    `${AI_URL}/conversations/${encodeURIComponent(id)}/messages/stream`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    },
  );
  if (!res.ok || !res.body) {
    // Non-stream error response — drain it as JSON for the caller.
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* ignore parse error */
    }
    const errMsg =
      typeof body === "object" && body !== null && "error" in body
        ? String((body as { error: unknown }).error)
        : `HTTP ${res.status}`;
    yield { type: "stream_error", error: errMsg, details: jsonDetail(body) };
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by blank lines (\n\n).
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseOneFrame(frame);
        if (parsed) yield parsed;
        boundary = buffer.indexOf("\n\n");
      }
    }
    // Anything still in the buffer is an unterminated frame — ignore.
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

/**
 * Subscribe to the in-flight agent turn for a conversation, if any. Returns
 * an async generator like ``sendMessageStream`` — but doesn't START a turn,
 * it just attaches to whatever's already running. Useful for reload-resume.
 *
 * If there's no active turn, the server returns 204 and this generator
 * finishes immediately without yielding anything. Callers should fall back
 * to ``getConversation`` for the persisted state.
 */
export async function* subscribeActiveStream(
  id: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent, void, void> {
  const res = await fetch(
    `${AI_URL}/conversations/${encodeURIComponent(id)}/messages/stream`,
    { method: "GET", signal },
  );
  if (res.status === 204) return;
  if (!res.ok || !res.body) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* ignore parse error */
    }
    const errMsg =
      typeof body === "object" && body !== null && "error" in body
        ? String((body as { error: unknown }).error)
        : `HTTP ${res.status}`;
    yield { type: "stream_error", error: errMsg, details: jsonDetail(body) };
    return;
  }
  yield* readSseFromResponseBody(res);
}

/**
 * Cancel the in-flight agent turn for a conversation. Returns true if a turn
 * was cancelled, false if there was nothing to cancel.
 */
export async function cancelActiveStream(id: string): Promise<boolean> {
  const res = await fetch(
    `${AI_URL}/conversations/${encodeURIComponent(id)}/messages/cancel`,
    { method: "POST" },
  );
  if (res.status === 404) return false;
  return res.ok;
}

/**
 * Read SSE frames from a Response body, yielding parsed events. Shared by
 * sendMessageStream and subscribeActiveStream so the parser logic lives once.
 */
async function* readSseFromResponseBody(
  res: Response,
): AsyncGenerator<StreamEvent, void, void> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseOneFrame(frame);
        if (parsed) yield parsed;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

function parseOneFrame(raw: string): StreamEvent | null {
  let eventType: string | null = null;
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }
  if (!eventType || dataLines.length === 0) return null;
  try {
    const data = JSON.parse(dataLines.join("\n"));
    // The server always sends `type` matching the SSE `event:` line; we trust
    // the server's framing and lift the data payload to the event object.
    return { ...data, type: eventType } as StreamEvent;
  } catch {
    return null;
  }
}

function jsonDetail(body: unknown): string | null {
  if (typeof body === "object" && body !== null && "details" in body) {
    const d = (body as { details: unknown }).details;
    return typeof d === "string" ? d : JSON.stringify(d);
  }
  return null;
}

// ---- Health ----------------------------------------------------------------

export const AiHealth = z.object({
  status: z.enum(["ok", "degraded"]),
  postgres: z.enum(["reachable", "unreachable"]),
  core: z.enum(["reachable", "unreachable"]),
  anthropic: z.enum(["configured", "missing"]),
});
export type AiHealth = z.infer<typeof AiHealth>;

export async function fetchAiHealth(): Promise<AiHealth> {
  try {
    const raw = await aiApi.get<unknown>("/health");
    return AiHealth.parse(raw);
  } catch (err) {
    if (
      err &&
      typeof err === "object" &&
      "status" in err &&
      "body" in err &&
      typeof (err as { status: unknown }).status === "number"
    ) {
      const parsed = AiHealth.safeParse((err as { body: unknown }).body);
      if (parsed.success) return parsed.data;
    }
    throw err;
  }
}
