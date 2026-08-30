import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { Catalogs } from "@/pages/Catalogs";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <Catalogs />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("Catalogs page", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the empty state when no catalogs", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse([]),
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });
    expect(screen.getByText(/no catalogs registered/i)).toBeInTheDocument();
  });

  it("renders rows for each catalog", async () => {
    const now = new Date().toISOString();
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse([
        {
          name: "tpch_demo",
          connector: "tpch",
          properties: {},
          status: "enabled",
          last_error: null,
          factory_count: 3,
          created_at: now,
          updated_at: now,
        },
        {
          name: "broken_one",
          connector: "postgresql",
          properties: { "connection-url": "jdbc:postgresql://nowhere.invalid:5432/x" },
          status: "broken",
          last_error: "CREATE CATALOG failed: TrinoError: oops",
          factory_count: 0,
          created_at: now,
          updated_at: now,
        },
      ]),
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("catalogs-table")).toBeInTheDocument();
    });
    expect(screen.getByText("tpch_demo")).toBeInTheDocument();
    expect(screen.getByText("broken_one")).toBeInTheDocument();
    expect(screen.getByTestId("status-enabled")).toBeInTheDocument();
    expect(screen.getByTestId("status-broken")).toBeInTheDocument();
    expect(screen.getByTestId("last-error-broken_one")).toBeInTheDocument();
    // Factory-count column reflects each catalog's factory total.
    expect(screen.getByTestId("factory-count-tpch_demo")).toHaveTextContent("3");
    expect(screen.getByTestId("factory-count-broken_one")).toHaveTextContent("0");
  });

  it("renders an error state on fetch failure", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("network down"),
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("error-state")).toBeInTheDocument();
    });
    expect(screen.getByText(/couldn't load catalogs/i)).toBeInTheDocument();
  });
});
