import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { CatalogDetail } from "@/pages/CatalogDetail";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/catalogs/:name" element={<CatalogDetail />} />
          <Route path="/catalogs" element={<div data-testid="catalogs-page">list</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const NOW = new Date().toISOString();

const sampleRow = {
  name: "tpch_demo",
  connector: "tpch",
  properties: {} as Record<string, string>,
  status: "enabled" as const,
  last_error: null as string | null,
  version: 1,
  created_at: NOW,
  updated_at: NOW,
};

describe("CatalogDetail", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows catalog details and matches the Trino state", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce(jsonResponse(sampleRow)) // GET /catalogs/tpch_demo
      .mockResolvedValueOnce(jsonResponse([{ name: "tpch_demo", connector: "tpch" }])); // /trino/state

    renderAt("/catalogs/tpch_demo");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "tpch_demo" })).toBeInTheDocument();
    });
    expect(screen.getByTestId("status-enabled")).toBeInTheDocument();
    expect(screen.getByTestId("trino-row")).toBeInTheDocument();
    expect(screen.queryByTestId("trino-missing")).not.toBeInTheDocument();
  });

  it("surfaces connector mismatch when Trino has the wrong connector", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ ...sampleRow, connector: "postgresql" }))
      .mockResolvedValueOnce(jsonResponse([{ name: "tpch_demo", connector: "memory" }]));

    renderAt("/catalogs/tpch_demo");

    await waitFor(() => {
      expect(screen.getByTestId("connector-mismatch")).toBeInTheDocument();
    });
  });

  it("shows the missing-from-Trino warning when applicable", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce(jsonResponse(sampleRow))
      .mockResolvedValueOnce(jsonResponse([])); // trino state empty

    renderAt("/catalogs/tpch_demo");

    await waitFor(() => {
      expect(screen.getByTestId("trino-missing")).toBeInTheDocument();
    });
  });

  it("masks sensitive property values", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          ...sampleRow,
          connector: "postgresql",
          properties: {
            "connection-url": "jdbc:postgresql://h/db",
            "connection-user": "edgar",
            "connection-password": "super-secret",
          },
        }),
      )
      .mockResolvedValueOnce(jsonResponse([]));

    renderAt("/catalogs/tpch_demo");

    await waitFor(() => {
      expect(screen.getByTestId("properties-list")).toBeInTheDocument();
    });
    expect(screen.getByText(/jdbc:postgresql:\/\/h\/db/)).toBeInTheDocument();
    expect(screen.getByText("edgar")).toBeInTheDocument();
    expect(screen.queryByText("super-secret")).not.toBeInTheDocument();
    expect(screen.getByText("••••••••")).toBeInTheDocument();
  });

  it("renders the not-found state for 404s", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error: "not_found", name: "ghost" }, 404))
      .mockResolvedValueOnce(jsonResponse([]));

    renderAt("/catalogs/ghost");

    await waitFor(() => {
      expect(screen.getByTestId("not-found")).toBeInTheDocument();
    });
  });

  it("edits properties and PATCHes the new values", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    const initialRow = {
      ...sampleRow,
      connector: "postgresql",
      properties: {
        "connection-url": "jdbc:postgresql://old.invalid:5432/x",
        "connection-user": "alice",
      },
    };
    const patchedRow = {
      ...initialRow,
      properties: {
        "connection-url": "jdbc:postgresql://new.invalid:5432/x",
        "connection-user": "alice",
        "connection-password": "secret",
      },
    };

    let patched = false;
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/catalogs/tpch_demo") && method === "GET") {
        return jsonResponse(patched ? patchedRow : initialRow);
      }
      if (url.endsWith("/trino/state") && method === "GET") {
        return jsonResponse([{ name: "tpch_demo", connector: "postgresql" }]);
      }
      if (url.endsWith("/catalogs/tpch_demo") && method === "PATCH") {
        patched = true;
        patchBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({
          catalog: patchedRow,
          reconcile: { all_ok: true, actions: [{ kind: "create", name: "tpch_demo", ok: true, error: null }] },
        });
      }
      if (url.endsWith("/catalogs") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/catalogs/tpch_demo");
    await waitFor(() => screen.getByTestId("edit-button"));

    await user.click(screen.getByTestId("edit-button"));
    expect(screen.getByTestId("catalog-editor")).toBeInTheDocument();

    // Add a new property row, fill it in.
    await user.click(screen.getByTestId("add-property-button"));
    await user.type(screen.getByTestId("property-key-2"), "connection-password");
    await user.type(screen.getByTestId("property-value-2"), "secret");

    await user.click(screen.getByTestId("save-button"));

    await waitFor(() => expect(patched).toBe(true));
    expect(patchBody).toEqual({
      connector: "postgresql",
      properties: {
        "connection-url": "jdbc:postgresql://old.invalid:5432/x",
        "connection-user": "alice",
        "connection-password": "secret",
      },
    });

    // Editor closes and the updated properties show up (password masked).
    await waitFor(() => expect(screen.queryByTestId("catalog-editor")).toBeNull());
    expect(screen.getByText("••••••••")).toBeInTheDocument();
  });

  it("flags duplicate property keys without sending a PATCH", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/catalogs/tpch_demo") && method === "GET") {
        return jsonResponse(sampleRow);
      }
      if (url.endsWith("/trino/state") && method === "GET") {
        return jsonResponse([{ name: "tpch_demo", connector: "tpch" }]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/catalogs/tpch_demo");
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));

    // Two rows with the same key.
    await user.click(screen.getByTestId("add-property-button"));
    await user.type(screen.getByTestId("property-key-0"), "dup");
    await user.type(screen.getByTestId("property-value-0"), "1");
    await user.click(screen.getByTestId("add-property-button"));
    await user.type(screen.getByTestId("property-key-1"), "dup");
    await user.type(screen.getByTestId("property-value-1"), "2");

    await user.click(screen.getByTestId("save-button"));

    await waitFor(() =>
      expect(screen.getByTestId("edit-error")).toHaveTextContent(/Duplicate property/),
    );

    // No PATCH was sent.
    expect(
      fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PATCH"),
    ).toBeUndefined();
  });

  it("asks for confirmation before deleting and navigates back on success", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/catalogs/tpch_demo") && method === "GET") {
        return jsonResponse(sampleRow);
      }
      if (url.endsWith("/trino/state") && method === "GET") {
        return jsonResponse([{ name: "tpch_demo", connector: "tpch" }]);
      }
      if (url.endsWith("/catalogs/tpch_demo") && method === "DELETE") {
        return jsonResponse({
          deleted: "tpch_demo",
          reconcile: { all_ok: true, actions: [] },
        });
      }
      // The factories panel queries — return empties; not asserted in this test.
      if (url.includes("/object-factories")) return jsonResponse([]);
      if (url.endsWith("/object-types") || url.includes("/object-types?"))
        return jsonResponse([]);
      if (url.endsWith("/catalogs") && method === "GET") return jsonResponse([]);
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/catalogs/tpch_demo");

    await waitFor(() => screen.getByTestId("delete-button"));

    await user.click(screen.getByTestId("delete-button"));
    expect(screen.getByTestId("delete-confirm")).toBeInTheDocument();

    await user.click(screen.getByTestId("delete-confirm-button"));

    await waitFor(() => {
      expect(screen.getByTestId("catalogs-page")).toBeInTheDocument();
    });
  });
});
