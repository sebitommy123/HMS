import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { HealthIndicator } from "@/components/HealthIndicator";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// HealthIndicator queries two endpoints in parallel: Core's `/health` and the
// AI service's `/health`. Tests route by URL so each can be configured
// independently.
type Routes = {
  core?: () => Response | Promise<Response>;
  coreReject?: unknown;
  ai?: () => Response | Promise<Response>;
  aiReject?: unknown;
};

function mockHealthFetch(routes: Routes) {
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockImplementation(async (url: string) => {
    if (url.includes(":5002")) {
      // AI service is on port 5002 by default.
      if (routes.aiReject !== undefined) throw routes.aiReject;
      return (routes.ai ?? (() => jsonResponse({ error: "not configured" }, 500)))();
    }
    if (routes.coreReject !== undefined) throw routes.coreReject;
    return (routes.core ?? (() => jsonResponse({ error: "not configured" }, 500)))();
  });
}

describe("HealthIndicator", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders 'ok' when Core + AI both reachable", async () => {
    mockHealthFetch({
      core: () => jsonResponse({ status: "ok", postgres: "reachable", trino: "reachable" }),
      ai: () =>
        jsonResponse({
          status: "ok",
          postgres: "reachable",
          core: "reachable",
          anthropic: "configured",
        }),
    });

    renderWithClient(<HealthIndicator />);

    await waitFor(() => {
      expect(screen.getByText(/^postgres$/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/^trino$/i)).toBeInTheDocument();
    expect(screen.getByText(/^ai$/i)).toBeInTheDocument();
    expect(screen.getByText(/^ok$/i)).toBeInTheDocument();
  });

  it("renders 'degraded' when Trino is unreachable but Core itself responded", async () => {
    mockHealthFetch({
      core: () =>
        jsonResponse(
          { status: "degraded", postgres: "reachable", trino: "unreachable" },
          503,
        ),
      ai: () =>
        jsonResponse({
          status: "ok",
          postgres: "reachable",
          core: "reachable",
          anthropic: "configured",
        }),
    });

    renderWithClient(<HealthIndicator />);

    await waitFor(() => expect(screen.getByText(/degraded/i)).toBeInTheDocument());
    // Both dots present with the simplified labels — color carries up/down.
    expect(screen.getByText(/^trino$/i)).toBeInTheDocument();
    expect(screen.getByText(/^postgres$/i)).toBeInTheDocument();
  });

  it("renders 'offline' when Core itself is unreachable (network error / CORS rejection)", async () => {
    mockHealthFetch({
      coreReject: new TypeError("Failed to fetch"),
      ai: () =>
        jsonResponse({
          status: "ok",
          postgres: "reachable",
          core: "reachable",
          anthropic: "configured",
        }),
    });

    renderWithClient(<HealthIndicator />);

    await waitFor(() => {
      expect(screen.getByText(/^offline$/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/^core$/i)).toBeInTheDocument();
  });

  it("flags 'ai: no key' when AI is up but ANTHROPIC_API_KEY is missing", async () => {
    mockHealthFetch({
      core: () => jsonResponse({ status: "ok", postgres: "reachable", trino: "reachable" }),
      ai: () =>
        jsonResponse(
          {
            status: "degraded",
            postgres: "reachable",
            core: "reachable",
            anthropic: "missing",
          },
          503,
        ),
    });

    renderWithClient(<HealthIndicator />);

    await waitFor(() => {
      expect(screen.getByText(/ai: no key/i)).toBeInTheDocument();
    });
  });

  it("renders the ai dot as bad when AI service is down", async () => {
    mockHealthFetch({
      core: () => jsonResponse({ status: "ok", postgres: "reachable", trino: "reachable" }),
      aiReject: new TypeError("Failed to fetch"),
    });

    const { container } = renderWithClient(<HealthIndicator />);

    // Wait for both queries to settle.
    await waitFor(() => expect(screen.getByText(/^ai$/i)).toBeInTheDocument());

    // The dot next to the "ai" label should be red (tone="bad").
    const aiSpan = screen.getByText(/^ai$/i).closest("span");
    expect(aiSpan).not.toBeNull();
    const dot = aiSpan!.querySelector("span");
    expect(dot?.className).toMatch(/bg-red-500/);
    // Sanity: container actually has a red dot somewhere.
    expect(container.querySelector(".bg-red-500")).not.toBeNull();
  });
});
