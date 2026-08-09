import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { Chats } from "@/pages/Chats";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <Chats />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("Chats page", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the empty state when no conversations", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse([]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });
  });

  it("renders rows for each conversation with preview + message_count", async () => {
    const now = new Date().toISOString();
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse([
        {
          id: "abc",
          title: "First chat",
          model: "claude-opus-4-8",
          system_prompt: null,
          created_at: now,
          updated_at: now,
          message_count: 4,
          preview: "What catalogs are registered?",
        },
        {
          id: "def",
          title: "Second chat",
          model: "claude-sonnet-4-6",
          system_prompt: null,
          created_at: now,
          updated_at: now,
          message_count: 2,
          preview: null,
        },
      ]),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("chats-list")).toBeInTheDocument());
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.getByText(/What catalogs are registered/)).toBeInTheDocument();
    expect(screen.getByText("4 messages")).toBeInTheDocument();
    expect(screen.getByText("2 messages")).toBeInTheDocument();
  });

  it("shows an error state when the AI service is down", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new TypeError("Failed to fetch"),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("error-state")).toBeInTheDocument();
    });
  });
});
