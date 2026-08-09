import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type ContentBlock,
  type ConversationDetail,
  type Message,
  type StreamEvent,
  cancelActiveStream,
  deleteConversation,
  getConversation,
  sendMessageStream,
  subscribeActiveStream,
} from "@/api/conversations";
import { AiApiError } from "@/api/ai-client";
import { MessageBlocks } from "@/components/MessageBlocks";
import { relativeTime } from "@/lib/format";

// Live state we accumulate while the SSE stream is open. Reset between sends.
interface ToolCallInFlight {
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: { output: string; isError: boolean };
}

interface InProgressTurn {
  // The user message the server persisted (carried in stream_start) — we show
  // it immediately so the textarea-cleared state feels instant.
  userMessage: Message | null;
  textSoFar: string;
  toolCalls: ToolCallInFlight[];
}

const emptyInProgress: InProgressTurn = {
  userMessage: null,
  textSoFar: "",
  toolCalls: [],
};

// If no event arrives in this long, treat the stream as stalled. We abort
// the fetch and refetch the conversation — the server may have actually
// finished (we just lost the wire); if so the canonical state will show
// the completed messages and the in-progress overlay clears cleanly.
const STREAM_STALL_TIMEOUT_MS = 60_000;

export interface ChatConversationProps {
  id: string;
  /** "panel" is the narrow side-panel embedding; "full" is the full page. */
  variant?: "panel" | "full";
  /** Where to go after the conversation is deleted. */
  onDeleted: () => void;
  /** Show a back arrow (side panel → list). */
  onBack?: () => void;
  /** Show an expand affordance (side panel → full screen). */
  onExpand?: () => void;
}

/**
 * The full chat conversation UI + streaming state machine. Used both in the
 * always-present side panel (variant="panel") and the full-screen route
 * (variant="full"). Height-agnostic: the parent must bound its height so the
 * message list scrolls internally.
 */
export function ChatConversation({
  id,
  variant = "full",
  onDeleted,
  onBack,
  onExpand,
}: ChatConversationProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [inProgress, setInProgress] = useState<InProgressTurn>(emptyInProgress);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const chat = useQuery({
    queryKey: ["conversations", id],
    queryFn: () => getConversation(id),
    enabled: Boolean(id),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteConversation(id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["conversations"] });
      onDeleted();
    },
  });

  // Auto-scroll to bottom when new persisted messages arrive, when the stream
  // updates, or while streaming so deltas remain visible.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [
    chat.data?.messages.length,
    isStreaming,
    inProgress.textSoFar,
    inProgress.toolCalls.length,
  ]);

  // Consume a stream (either a freshly-started one or a resumed in-flight one)
  // and drive the in-progress state from its events. Aborts the underlying
  // fetch on stall (no events for STREAM_STALL_TIMEOUT_MS) and on unmount.
  const consumeStream = useCallback(
    async (
      iter: AsyncGenerator<StreamEvent, void, void>,
      ctrl: AbortController,
    ) => {
      let streamErrorMessage: string | null = null;
      const stallReason = await drainWithStallDetection(
        iter,
        ctrl,
        STREAM_STALL_TIMEOUT_MS,
        (ev) => {
          applyEvent(ev, setInProgress, qc, id);
          if (ev.type === "stream_error") {
            streamErrorMessage = ev.details ?? ev.error;
          }
        },
      );
      // Fire-and-forget: trigger the canonical-state refetch but DON'T await
      // it. Awaiting here previously blocked the caller's finally block —
      // and therefore setIsStreaming(false) — whenever the refetch hung
      // (slow network / AI blip), leaving the UI stuck in "streaming"
      // forever. Cache invalidation is the only side-effect we need
      // synchronously; the refetch can land whenever it lands.
      void qc.invalidateQueries({ queryKey: ["conversations", id] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      if (streamErrorMessage) {
        setSendError(streamErrorMessage);
      } else if (stallReason === "stalled") {
        setSendError(
          "Stream went quiet — refreshed the conversation. " +
            "If the agent is still running you'll see updates roll in.",
        );
      }
    },
    [id, qc],
  );

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || isStreaming) return;
    setDraft("");
    setSendError(null);
    setInProgress(emptyInProgress);
    setIsStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await consumeStream(sendMessageStream(id, text, ctrl.signal), ctrl);
    } catch (err) {
      if (!ctrl.signal.aborted) {
        setSendError(toErrorMessage(err));
      }
    } finally {
      setIsStreaming(false);
      setIsCancelling(false);
      setInProgress(emptyInProgress);
      abortRef.current = null;
    }
  }, [consumeStream, draft, id, isStreaming]);

  const handleStop = useCallback(async () => {
    if (!isStreaming || isCancelling) return;
    setIsCancelling(true);
    // Server cancel: signals the agent loop to exit at its next checkpoint.
    // The local fetch stays open so we receive the trailing cancelled-state
    // events (assistant_persisted with the partial text, stream_done with
    // final_stop_reason=cancelled).
    try {
      await cancelActiveStream(id);
    } catch {
      // If the server cancel fails, fall back to a hard local abort.
      abortRef.current?.abort();
    }
  }, [id, isStreaming, isCancelling]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        void handleSend();
      }
    },
    [handleSend],
  );

  // On mount (and when the chat id changes), check whether there's an in-flight
  // agent turn on the server and attach to it. This is what makes reloading
  // the page during a long response just work.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const ctrl = new AbortController();
    (async () => {
      try {
        const iter = subscribeActiveStream(id, ctrl.signal);
        // Peek the first event to know if there's anything to subscribe to.
        // subscribeActiveStream returns immediately (no events) when the
        // server reports 204 — we don't want to flip into streaming state
        // for empty subscriptions.
        const first = await iter.next();
        if (cancelled || first.done) return;
        // There IS an in-flight turn. Take over as if we'd just hit send.
        setSendError(null);
        setInProgress(emptyInProgress);
        setIsStreaming(true);
        abortRef.current = ctrl;
        applyEvent(first.value, setInProgress, qc, id);
        try {
          await consumeStream(repackage(iter), ctrl);
        } finally {
          if (!cancelled) {
            setIsStreaming(false);
            setIsCancelling(false);
            setInProgress(emptyInProgress);
            abortRef.current = null;
          }
        }
      } catch {
        /* probe failed — nothing to attach to */
      }
    })();
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [id, qc, consumeStream]);

  if (chat.isLoading) {
    return <Skeleton />;
  }

  if (chat.isError) {
    const status = chat.error instanceof AiApiError ? chat.error.status : undefined;
    if (status === 404) {
      return <NotFound id={id} onBack={onBack} />;
    }
    return <ErrorState message={(chat.error as Error).message} />;
  }
  if (!chat.data) {
    return <NotFound id={id} onBack={onBack} />;
  }

  const conv = chat.data;
  const inProgressBlocks = buildInProgressBlocks(inProgress);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start justify-between gap-2 border-b border-zinc-100 px-1 pb-3">
        <div className="flex min-w-0 items-start gap-2">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="mt-0.5 shrink-0 rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800"
              title="Back to chats"
              aria-label="Back to chats"
              data-testid="chat-back-button"
            >
              <BackIcon />
            </button>
          )}
          <div className="min-w-0">
            <h1
              className={
                variant === "panel"
                  ? "truncate text-sm font-semibold tracking-tight"
                  : "truncate text-xl font-semibold tracking-tight"
              }
            >
              {conv.title}
            </h1>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
              <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono">
                {conv.model}
              </code>
              <span>·</span>
              <span>{conv.messages.length} msgs</span>
              {variant === "full" && (
                <>
                  <span>·</span>
                  <span title={conv.updated_at}>
                    updated {relativeTime(conv.updated_at)}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {onExpand && (
            <button
              type="button"
              onClick={onExpand}
              className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800"
              title="Open full screen"
              aria-label="Open full screen"
              data-testid="chat-expand-button"
            >
              <ExpandIcon />
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              if (window.confirm(`Delete "${conv.title}"? This can't be undone.`)) {
                deleteMutation.mutate();
              }
            }}
            disabled={deleteMutation.isPending}
            className="rounded p-1 text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
            title="Delete chat"
            aria-label="Delete chat"
            data-testid="delete-chat-button"
          >
            <TrashIcon />
          </button>
        </div>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 min-w-0 flex-1 overflow-y-auto px-1 py-4"
        data-testid="message-scroll"
      >
        {conv.messages.length === 0 && !isStreaming ? (
          <FirstTurnHint />
        ) : (
          <ol className="space-y-4" data-testid="messages">
            {conv.messages.map((msg) => (
              <li key={msg.id}>
                <MessageRow message={msg} />
              </li>
            ))}
            {isStreaming && inProgress.userMessage && (
              <li key="in-progress-user">
                <MessageRow message={inProgress.userMessage} />
              </li>
            )}
            {isStreaming && inProgressBlocks.length > 0 && (
              <li key="in-progress-assistant">
                <InProgressAssistantRow blocks={inProgressBlocks} />
              </li>
            )}
          </ol>
        )}
        {isStreaming && inProgressBlocks.length === 0 && <PendingTurn />}
      </div>

      {sendError && (
        <div
          className="mb-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
          data-testid="send-error"
        >
          {sendError}
        </div>
      )}

      <div className="border-t border-zinc-100 px-1 pt-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          spellCheck={true}
          placeholder="Ask about your catalogs, run a query, explore the data…"
          className="block w-full resize-y rounded border border-zinc-200 bg-white p-3 text-sm shadow-inner focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          rows={variant === "panel" ? 2 : 3}
          disabled={isStreaming}
          data-testid="message-input"
        />
        <div className="mt-2 flex items-center justify-between text-xs text-zinc-500">
          <span className="hidden sm:inline">⌘/Ctrl + Enter to send</span>
          {isStreaming ? (
            <button
              type="button"
              onClick={() => void handleStop()}
              disabled={isCancelling}
              className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="stop-button"
            >
              {isCancelling ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void handleSend()}
              disabled={!draft.trim()}
              className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="send-button"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- icons -------------------------------------------------------------

function BackIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
      <path
        fillRule="evenodd"
        d="M12.79 5.23a.75.75 0 0 1 0 1.06L9.06 10l3.73 3.71a.75.75 0 1 1-1.06 1.06l-4.24-4.24a.75.75 0 0 1 0-1.06l4.24-4.24a.75.75 0 0 1 1.06 0Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
      <path d="M13 3a1 1 0 0 0 0 2h1.586l-4.293 4.293a1 1 0 0 0 1.414 1.414L16 6.414V8a1 1 0 1 0 2 0V4a1 1 0 0 0-1-1h-4ZM7 17a1 1 0 1 0 0-2H5.414l4.293-4.293a1 1 0 0 0-1.414-1.414L4 13.586V12a1 1 0 1 0-2 0v4a1 1 0 0 0 1 1h4Z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
      <path
        fillRule="evenodd"
        d="M8.75 1a1 1 0 0 0-.96.71L7.56 2.5H4a.75.75 0 0 0 0 1.5h.293l.82 11.48A2 2 0 0 0 7.108 17.5h5.784a2 2 0 0 0 1.995-1.52L15.707 4H16a.75.75 0 0 0 0-1.5h-3.56l-.23-.79A1 1 0 0 0 11.25 1h-2.5ZM9 6.75a.75.75 0 0 0-1.5 0v6a.75.75 0 0 0 1.5 0v-6Zm3.5 0a.75.75 0 0 0-1.5 0v6a.75.75 0 0 0 1.5 0v-6Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

// ---- streaming plumbing (moved verbatim from the old ChatDetail) --------

// Wrap an async generator into a fresh one — used after we've consumed the
// first .next() during the reload-resume probe but want the same iterator to
// keep going inside consumeStream.
async function* repackage<T>(
  iter: AsyncGenerator<T, void, void>,
): AsyncGenerator<T, void, void> {
  while (true) {
    const r = await iter.next();
    if (r.done) return;
    yield r.value;
  }
}

// Drains ``iter``, calling ``onEvent`` for each event. If no event arrives in
// ``timeoutMs``, aborts ``ctrl`` and returns ``"stalled"``. Returns ``"done"``
// when the iterator finishes normally.
async function drainWithStallDetection(
  iter: AsyncGenerator<StreamEvent, void, void>,
  ctrl: AbortController,
  timeoutMs: number,
  onEvent: (ev: StreamEvent) => void,
): Promise<"done" | "stalled"> {
  while (true) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stallPromise = new Promise<"stalled">((resolve) => {
      timer = setTimeout(() => resolve("stalled"), timeoutMs);
    });
    const nextPromise = iter.next().then((r) => ({ stalled: false as const, r }));
    try {
      const winner = await Promise.race([
        nextPromise,
        stallPromise.then((s) => ({ stalled: true as const, kind: s })),
      ]);
      if (winner.stalled) {
        ctrl.abort();
        return "stalled";
      }
      if (winner.r.done) return "done";
      onEvent(winner.r.value);
      const ev = winner.r.value;
      if (ev.type === "stream_error" || ev.type === "stream_done") return "done";
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
}

function applyEvent(
  ev: StreamEvent,
  setInProgress: React.Dispatch<React.SetStateAction<InProgressTurn>>,
  qc: ReturnType<typeof useQueryClient>,
  conversationId: string,
) {
  switch (ev.type) {
    case "stream_start":
      setInProgress((p) => ({ ...p, userMessage: ev.user_message }));
      return;
    case "assistant_start":
      // Reset the text buffer at the START of each iteration rather than
      // when the previous iteration was persisted. Keeping the last
      // iteration's text visible right up until the next one begins avoids
      // a visible "flash empty" on the final turn: there's no
      // assistant_start after the last assistant_persisted, just
      // stream_done + a refetch, so clearing on persisted would leave the
      // overlay textless while we wait for the canonical state to land.
      setInProgress((p) => ({ ...p, textSoFar: "" }));
      return;
    case "text_delta":
      setInProgress((p) => ({ ...p, textSoFar: p.textSoFar + ev.text }));
      return;
    case "tool_executing":
      setInProgress((p) => ({
        ...p,
        toolCalls: [
          ...p.toolCalls,
          { id: ev.tool_use_id, name: ev.name, input: ev.input },
        ],
      }));
      return;
    case "tool_result":
      setInProgress((p) => ({
        ...p,
        toolCalls: p.toolCalls.map((tc) =>
          tc.id === ev.tool_use_id
            ? { ...tc, result: { output: ev.output, isError: ev.is_error } }
            : tc,
        ),
      }));
      return;
    case "title_updated":
      qc.setQueryData<ConversationDetail | undefined>(
        ["conversations", conversationId],
        (prev) => (prev ? { ...prev, title: ev.title } : prev),
      );
      return;
    case "heartbeat":
      // No state change — its only job is to arrive, which resets the client's
      // stall detector so a long-running tool isn't mistaken for a dead stream.
      return;
    case "assistant_persisted":
    case "tool_results_persisted":
    case "stream_done":
    case "stream_error":
      return;
  }
}

function buildInProgressBlocks(p: InProgressTurn): ContentBlock[] {
  // Order matters chronologically: tool calls from earlier iterations come
  // first, then the text currently being streamed (the newest content) at
  // the bottom, where the user expects to see it land.
  const blocks: ContentBlock[] = [];
  for (const tc of p.toolCalls) {
    blocks.push({ type: "tool_use", id: tc.id, name: tc.name, input: tc.input });
    if (tc.result) {
      blocks.push({
        type: "tool_result",
        tool_use_id: tc.id,
        content: tc.result.output,
        is_error: tc.result.isError,
      });
    }
  }
  if (p.textSoFar) {
    blocks.push({ type: "text", text: p.textSoFar });
  }
  return blocks;
}

function toErrorMessage(err: unknown): string {
  if (err instanceof AiApiError) {
    const body = (err.body ?? {}) as { error?: string; details?: unknown };
    if (err.status === 503 && body.error === "anthropic_not_configured") {
      return "AI service has no ANTHROPIC_API_KEY configured. Set it on the AI process and restart.";
    }
    if (body.details) {
      return typeof body.details === "string"
        ? body.details
        : JSON.stringify(body.details);
    }
    return `${body.error ?? "Request failed"} (HTTP ${err.status})`;
  }
  return (err as Error).message;
}

function MessageRow({ message }: { message: Message }) {
  // Tool results are persisted as user-role messages per Anthropic's content
  // convention, but visually they're feedback from the tool runner, not the
  // human. Detect and relabel so the badge isn't misleading.
  const isToolResults =
    message.role === "user" &&
    message.content.length > 0 &&
    message.content.every((b) => b.type === "tool_result");
  const displayRole = isToolResults ? "tool result" : message.role;
  const tone = roleTone(isToolResults ? "tool_result" : message.role);
  return (
    <article
      className={`rounded-lg border px-4 py-3 ${tone.container}`}
      data-testid={`message-${isToolResults ? "tool-result" : message.role}`}
    >
      <div className="mb-2 flex items-baseline justify-between">
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tone.badge}`}
        >
          {displayRole}
        </span>
        {message.usage?.input_tokens != null && (
          <span className="text-[10px] text-zinc-400" title="tokens (in / out)">
            {message.usage.input_tokens}↓ · {message.usage.output_tokens ?? 0}↑
          </span>
        )}
      </div>
      <MessageBlocks blocks={message.content} />
      {message.stop_reason && message.stop_reason !== "end_turn" && (
        <p className="mt-2 text-[11px] uppercase tracking-wide text-zinc-500">
          stop: {message.stop_reason}
        </p>
      )}
    </article>
  );
}

function InProgressAssistantRow({ blocks }: { blocks: ContentBlock[] }) {
  const tone = roleTone("assistant");
  return (
    <article
      className={`rounded-lg border px-4 py-3 ${tone.container}`}
      data-testid="message-assistant-streaming"
    >
      <div className="mb-2 flex items-baseline justify-between">
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tone.badge}`}
        >
          assistant
        </span>
        <span className="flex items-center gap-1 text-[10px] text-zinc-400">
          <span className="size-1.5 animate-pulse rounded-full bg-zinc-500" />
          streaming
        </span>
      </div>
      <MessageBlocks blocks={blocks} />
    </article>
  );
}

function roleTone(role: string) {
  switch (role) {
    case "user":
      return {
        container: "border-zinc-200 bg-white",
        badge: "bg-zinc-200 text-zinc-800",
      };
    case "assistant":
      return {
        container: "border-zinc-200 bg-zinc-50",
        badge: "bg-zinc-900 text-white",
      };
    case "tool_result":
      return {
        container: "border-zinc-200 bg-white",
        badge: "bg-emerald-100 text-emerald-800",
      };
    case "system":
      return {
        container: "border-amber-200 bg-amber-50",
        badge: "bg-amber-300 text-amber-900",
      };
    default:
      return {
        container: "border-zinc-200 bg-white",
        badge: "bg-zinc-200 text-zinc-800",
      };
  }
}

function PendingTurn() {
  return (
    <div
      className="mt-4 flex items-center gap-2 text-sm text-zinc-500"
      data-testid="pending-turn"
    >
      <span className="size-2 animate-pulse rounded-full bg-zinc-400" />
      Claude is thinking and may call tools — this can take a few seconds.
    </div>
  );
}

function FirstTurnHint() {
  return (
    <div className="rounded border border-dashed border-zinc-200 p-6 text-center text-sm text-zinc-500">
      No messages yet. Try{" "}
      <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">
        list my catalogs
      </code>{" "}
      or{" "}
      <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">
        how many companies are in sec_edgar?
      </code>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-3 p-1" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-16 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function NotFound({ id, onBack }: { id: string; onBack?: () => void }) {
  return (
    <div
      className="rounded border border-zinc-200 bg-white p-8 text-center"
      data-testid="not-found"
    >
      <h1 className="text-base font-medium text-zinc-900">Chat not found</h1>
      <p className="mt-2 text-sm text-zinc-600">
        No chat with id{" "}
        <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{id}</code>.
      </p>
      {onBack && (
        <button
          type="button"
          onClick={onBack}
          className="mt-4 inline-block text-sm font-medium text-zinc-900 underline underline-offset-2"
        >
          Back to chats
        </button>
      )}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      className="rounded border border-zinc-200 bg-white p-8 text-center"
      data-testid="error-state"
    >
      <h2 className="text-base font-medium text-red-700">Couldn't load chat</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
    </div>
  );
}
