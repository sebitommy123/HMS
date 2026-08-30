import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { State } from "@/pages/State";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderState() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <State />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const ISO = new Date().toISOString();

function cat(name: string, overrides: Record<string, unknown> = {}) {
  return {
    name,
    connector: "tpch",
    properties: {},
    status: "enabled",
    last_error: null,
    factory_count: 0,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  };
}

describe("State page", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockBoth(catalogs: unknown, trino: unknown) {
    const m = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    m.mockImplementation(async (url: string) => {
      if (url.endsWith("/catalogs")) return jsonResponse(catalogs);
      if (url.endsWith("/trino/state")) return jsonResponse(trino);
      if (url.endsWith("/reconcile")) return jsonResponse({ all_ok: true, actions: [] });
      throw new Error(`unexpected url: ${url}`);
    });
  }

  it("shows aligned rows when Postgres and Trino agree", async () => {
    mockBoth([cat("tpch_demo")], [{ name: "tpch_demo", connector: "tpch" }]);
    renderState();
    await waitFor(() => {
      expect(screen.getByTestId("drift-table")).toBeInTheDocument();
    });
    expect(screen.getByTestId("drift-aligned")).toBeInTheDocument();
  });

  it("shows the four major drift classes together", async () => {
    mockBoth(
      [
        cat("missing"),
        cat("wrong_conn"),
        cat("ok"),
        cat("bad", { status: "broken" }),
      ],
      [
        { name: "ok", connector: "tpch" },
        { name: "wrong_conn", connector: "memory" },
        { name: "extra", connector: "tpch" },
      ],
    );
    renderState();

    await waitFor(() => {
      expect(screen.getByTestId("drift-table")).toBeInTheDocument();
    });

    expect(screen.getByTestId("drift-aligned")).toBeInTheDocument();
    expect(screen.getByTestId("drift-missing-in-trino")).toBeInTheDocument();
    expect(screen.getByTestId("drift-extra-in-trino")).toBeInTheDocument();
    expect(screen.getByTestId("drift-connector-mismatch")).toBeInTheDocument();
    expect(screen.getByTestId("drift-broken")).toBeInTheDocument();
  });

  it("filters to drift rows when 'Only show drift' is toggled", async () => {
    const user = userEvent.setup();
    mockBoth([cat("aligned_one")], [{ name: "aligned_one", connector: "tpch" }, { name: "rogue", connector: "tpch" }]);
    renderState();

    await waitFor(() => expect(screen.getByTestId("drift-table")).toBeInTheDocument());

    expect(screen.getByTestId("drift-aligned")).toBeInTheDocument();
    expect(screen.getByTestId("drift-extra-in-trino")).toBeInTheDocument();

    await user.click(screen.getByTestId("only-drift-toggle"));

    // Aligned row should be hidden; drift row remains.
    expect(screen.queryByTestId("drift-aligned")).not.toBeInTheDocument();
    expect(screen.getByTestId("drift-extra-in-trino")).toBeInTheDocument();
  });

  it("clicking Reconcile fires POST /reconcile and shows the banner", async () => {
    const user = userEvent.setup();
    mockBoth([cat("missing")], []);
    renderState();

    await waitFor(() => expect(screen.getByTestId("drift-table")).toBeInTheDocument());

    // Now stub /reconcile to return a non-trivial result.
    const m = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    m.mockImplementation(async (url: string) => {
      if (url.endsWith("/reconcile")) {
        return jsonResponse({
          all_ok: true,
          actions: [{ kind: "create", name: "missing", ok: true, error: null }],
        });
      }
      if (url.endsWith("/catalogs")) return jsonResponse([cat("missing")]);
      if (url.endsWith("/trino/state")) return jsonResponse([{ name: "missing", connector: "tpch" }]);
      throw new Error(`unexpected url: ${url}`);
    });

    await user.click(screen.getByTestId("reconcile-button"));

    await waitFor(() => {
      expect(screen.getByTestId("reconcile-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("reconcile-banner")).toHaveTextContent(/succeeded/i);
  });

  it("shows an error banner when both endpoints fail", async () => {
    const m = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    m.mockRejectedValue(new Error("network down"));
    renderState();
    await waitFor(() => {
      expect(screen.getByTestId("state-error")).toBeInTheDocument();
    });
  });
});
