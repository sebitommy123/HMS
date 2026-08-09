import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ChatDetail } from "@/pages/ChatDetail";

const ISO = new Date().toISOString();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// SSE response with the given event sequence. Each entry serializes to
// `event: <name>\ndata: <json>\n\n` — matches the AI server's wire format.
function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const encoder = new TextEncoder();
  const body = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/chats/:id" element={<ChatDetail />} />
          <Route path="/chats" element={<div data-testid="chats-page">list</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fullConvWithMessages(id: string) {
  return {
    id,
    title: "Test chat",
    model: "claude-opus-4-8",
    system_prompt: null,
    created_at: ISO,
    updated_at: ISO,
    messages: [
      {
        id: "msg_user_1",
        conversation_id: id,
        position: 0,
        role: "user",
        content: [{ type: "text", text: "Hello Claude" }],
        stop_reason: null,
        usage: null,
        created_at: ISO,
      },
      {
        id: "msg_asst_1",
        conversation_id: id,
        position: 1,
        role: "assistant",
        content: [
          { type: "text", text: "Let me look at your catalogs." },
          {
            type: "tool_use",
            id: "toolu_1",
            name: "list_catalogs",
            input: {},
          },
        ],
        stop_reason: "tool_use",
        usage: {
          input_tokens: 100,
          output_tokens: 25,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
        },
        created_at: ISO,
      },
      {
        id: "msg_tool_1",
        conversation_id: id,
        position: 2,
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_1",
            content: "[]",
            is_error: false,
          },
        ],
        stop_reason: null,
        usage: null,
        created_at: ISO,
      },
      {
        id: "msg_asst_2",
        conversation_id: id,
        position: 3,
        role: "assistant",
        content: [{ type: "text", text: "You have no catalogs registered." }],
        stop_reason: "end_turn",
        usage: {
          input_tokens: 200,
          output_tokens: 12,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
        },
        created_at: ISO,
      },
    ],
  };
}

describe("ChatDetail", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a transcript including tool_use + tool_result", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse(fullConvWithMessages("abc")),
    );
    renderAt("/chats/abc");

    await waitFor(() => expect(screen.getByText("Hello Claude")).toBeInTheDocument());

    expect(screen.getByText("Let me look at your catalogs.")).toBeInTheDocument();
    expect(screen.getByText("You have no catalogs registered.")).toBeInTheDocument();

    expect(screen.getByTestId("tool-use-list_catalogs")).toBeInTheDocument();
    expect(screen.getByTestId("tool-result-ok")).toBeInTheDocument();
  });

  it("404 renders the not-found state", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ error: "not_found" }, 404),
    );
    renderAt("/chats/ghost");
    await waitFor(() => expect(screen.getByTestId("not-found")).toBeInTheDocument());
  });

  it("send button is disabled when the input is empty", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({
        id: "abc",
        title: "Empty",
        model: "claude-opus-4-8",
        system_prompt: null,
        created_at: ISO,
        updated_at: ISO,
        messages: [],
      }),
    );
    renderAt("/chats/abc");
    await waitFor(() => expect(screen.getByTestId("send-button")).toBeInTheDocument());
    expect(screen.getByTestId("send-button")).toBeDisabled();
  });

  it("streams text deltas and re-fetches the conversation when done", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    let hasSent = false;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/conversations/abc/messages/stream") && method === "POST") {
        hasSent = true;
        return sseResponse([
          {
            event: "stream_start",
            data: {
              conversation_id: "abc",
              user_message: {
                id: "msg_user_pending",
                conversation_id: "abc",
                position: 0,
                role: "user",
                content: [{ type: "text", text: "Hello Claude" }],
                stop_reason: null,
                usage: null,
                created_at: ISO,
              },
            },
          },
          { event: "assistant_start", data: { iteration: 0 } },
          { event: "text_delta", data: { text: "Streaming " } },
          { event: "text_delta", data: { text: "in flight…" } },
          {
            event: "stream_done",
            data: {
              final_stop_reason: "end_turn",
              iterations: 1,
              truncated_by_iteration_cap: false,
            },
          },
        ]);
      }
      if (url.endsWith("/conversations/abc") && method === "GET") {
        return hasSent
          ? jsonResponse(fullConvWithMessages("abc"))
          : jsonResponse({
              id: "abc",
              title: "Empty",
              model: "claude-opus-4-8",
              system_prompt: null,
              created_at: ISO,
              updated_at: ISO,
              messages: [],
            });
      }
      if (url.endsWith("/conversations") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/chats/abc");
    await waitFor(() => expect(screen.getByTestId("send-button")).toBeInTheDocument());

    await user.type(screen.getByTestId("message-input"), "Hello Claude");
    await user.click(screen.getByTestId("send-button"));

    // After the stream completes the canonical transcript is re-fetched.
    await waitFor(() =>
      expect(screen.getByText("You have no catalogs registered.")).toBeInTheDocument(),
    );

    const sendCall = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(sendCall).toBeTruthy();
    expect(String(sendCall![0])).toContain("/messages/stream");
    const body = JSON.parse((sendCall![1] as RequestInit).body as string);
    expect(body).toEqual({ text: "Hello Claude" });
  });

  it("title_updated event optimistically updates the header title", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    let hasSent = false;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/conversations/abc/messages/stream") && method === "POST") {
        hasSent = true;
        return sseResponse([
          {
            event: "stream_start",
            data: {
              conversation_id: "abc",
              user_message: {
                id: "msg_user_pending",
                conversation_id: "abc",
                position: 0,
                role: "user",
                content: [{ type: "text", text: "Hi" }],
                stop_reason: null,
                usage: null,
                created_at: ISO,
              },
            },
          },
          { event: "assistant_start", data: { iteration: 0 } },
          { event: "text_delta", data: { text: "Hi back." } },
          { event: "title_updated", data: { title: "Greeting" } },
          {
            event: "stream_done",
            data: {
              final_stop_reason: "end_turn",
              iterations: 1,
              truncated_by_iteration_cap: false,
            },
          },
        ]);
      }
      if (url.endsWith("/conversations/abc") && method === "GET") {
        return jsonResponse({
          id: "abc",
          title: hasSent ? "Greeting" : "New conversation",
          model: "claude-opus-4-8",
          system_prompt: null,
          created_at: ISO,
          updated_at: ISO,
          messages: [],
        });
      }
      if (url.endsWith("/conversations") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/chats/abc");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "New conversation",
      ),
    );

    await user.type(screen.getByTestId("message-input"), "Hi");
    await user.click(screen.getByTestId("send-button"));

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Greeting"),
    );
  });

  it("shows a Stop button while streaming and posts to the cancel endpoint when clicked", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    // Use a never-resolving SSE body so the stream stays "in flight" for the
    // duration of the test — that's the only way we keep the Stop button on
    // screen long enough to click it.
    let cancelCalled = false;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/conversations/abc") && method === "GET") {
        return jsonResponse({
          id: "abc",
          title: "Cancel",
          model: "claude-opus-4-8",
          system_prompt: null,
          created_at: ISO,
          updated_at: ISO,
          messages: [],
        });
      }
      if (url.endsWith("/conversations/abc/messages/stream") && method === "POST") {
        // Long-lived stream: emit stream_start + a delta, then hold.
        const encoder = new TextEncoder();
        const body = new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                `event: stream_start\ndata: ${JSON.stringify({
                  conversation_id: "abc",
                  user_message: {
                    id: "u1",
                    conversation_id: "abc",
                    position: 0,
                    role: "user",
                    content: [{ type: "text", text: "hi" }],
                    stop_reason: null,
                    usage: null,
                    created_at: ISO,
                  },
                })}\n\n`,
              ),
            );
            controller.enqueue(
              encoder.encode(
                `event: text_delta\ndata: ${JSON.stringify({ text: "in progress…" })}\n\n`,
              ),
            );
            // Don't close — simulates a still-running stream.
          },
        });
        return new Response(body, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }
      if (url.endsWith("/conversations/abc/messages/cancel") && method === "POST") {
        cancelCalled = true;
        return jsonResponse({ cancelled: true });
      }
      if (url.endsWith("/conversations") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/chats/abc");
    await waitFor(() => expect(screen.getByTestId("send-button")).toBeInTheDocument());

    await user.type(screen.getByTestId("message-input"), "hi");
    await user.click(screen.getByTestId("send-button"));

    // Send button gets swapped for the Stop button while streaming.
    await waitFor(() => expect(screen.getByTestId("stop-button")).toBeInTheDocument());
    await user.click(screen.getByTestId("stop-button"));

    await waitFor(() => expect(cancelCalled).toBe(true));
  });

  it("reload-resume: attaches to an in-flight stream on mount", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/conversations/abc") && method === "GET") {
        return jsonResponse({
          id: "abc",
          title: "Resuming",
          model: "claude-opus-4-8",
          system_prompt: null,
          created_at: ISO,
          updated_at: ISO,
          messages: [],
        });
      }
      // The reload-resume probe is a GET to the stream URL. Return SSE
      // events as if the agent had been mid-turn when the page reloaded.
      // Hold the stream open after the last event so the streaming UI stays
      // visible long enough for the assertions below.
      if (url.endsWith("/conversations/abc/messages/stream") && method === "GET") {
        const encoder = new TextEncoder();
        const body = new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                `event: stream_start\ndata: ${JSON.stringify({
                  conversation_id: "abc",
                  user_message: {
                    id: "u1",
                    conversation_id: "abc",
                    position: 0,
                    role: "user",
                    content: [{ type: "text", text: "from before reload" }],
                    stop_reason: null,
                    usage: null,
                    created_at: ISO,
                  },
                })}\n\n`,
              ),
            );
            controller.enqueue(
              encoder.encode(
                `event: assistant_start\ndata: ${JSON.stringify({ iteration: 0 })}\n\n`,
              ),
            );
            controller.enqueue(
              encoder.encode(
                `event: text_delta\ndata: ${JSON.stringify({ text: "resumed text" })}\n\n`,
              ),
            );
            // Keep open — simulates a still-running turn that we've attached to.
          },
        });
        return new Response(body, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }
      if (url.endsWith("/conversations") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/chats/abc");

    // The streaming UI should show the in-flight assistant text the server
    // already produced before the page reload.
    await waitFor(() =>
      expect(screen.getByTestId("message-assistant-streaming")).toBeInTheDocument(),
    );
    expect(screen.getByText(/resumed text/i)).toBeInTheDocument();
  });

  it("503 anthropic_not_configured surfaces a friendly error", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/conversations/abc") && method === "GET") {
        return jsonResponse({
          id: "abc",
          title: "Empty",
          model: "claude-opus-4-8",
          system_prompt: null,
          created_at: ISO,
          updated_at: ISO,
          messages: [],
        });
      }
      if (url.endsWith("/conversations/abc/messages/stream") && method === "POST") {
        return jsonResponse(
          {
            error: "anthropic_not_configured",
            details: "ANTHROPIC_API_KEY is not set",
          },
          503,
        );
      }
      if (url.endsWith("/conversations") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/chats/abc");
    await waitFor(() => expect(screen.getByTestId("send-button")).toBeInTheDocument());

    await user.type(screen.getByTestId("message-input"), "hi");
    await user.click(screen.getByTestId("send-button"));

    await waitFor(() => {
      expect(screen.getByTestId("send-error")).toHaveTextContent(/ANTHROPIC_API_KEY/);
    });
  });
});
