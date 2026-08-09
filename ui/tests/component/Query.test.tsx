import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { Query } from "@/pages/Query";

const ISO = new Date().toISOString();
const TYPE_ID = "a1b2c3d4-e5f6-4789-8abc-def012345678";
const FACTORY_ID = "c3d4e5f6-a7b8-4901-acde-f01234567890";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/query"]}>
        <Routes>
          <Route path="/query" element={<Query />} />
          <Route
            path="/raw-trino-query"
            element={<div data-testid="raw-page">raw</div>}
          />
          <Route
            path="/object-types"
            element={<div data-testid="types-page">types</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const typeRow = {
  id: TYPE_ID,
  name: "Company",
  description: "",
  traits: [] as string[],
  created_at: ISO,
  updated_at: ISO,
};

const successResult = {
  columns: ["_datasource", "name", "sic"],
  rows: [
    ["tpch.tiny.companies", "Acme", "3711"],
    ["tpch.tiny.companies", "Globex", "4922"],
  ],
  result_status: {
    all_ok: true,
    factories_used: [
      { factory_id: FACTORY_ID, data_source_path: "tpch.tiny.companies" },
    ],
    factories_skipped: [],
    errors: [],
    sql: "SELECT 'tpch.tiny.companies' AS _datasource, * FROM ...",
    trino_query_id: "20260620_140012_00023_abcde",
    elapsed_seconds: 0.042,
  },
};

const partialResult = {
  columns: ["_datasource"],
  rows: [],
  result_status: {
    all_ok: false,
    factories_used: [],
    factories_skipped: [
      {
        factory_id: FACTORY_ID,
        data_source_path: "ghost.public.t",
        reason: "catalog 'ghost' is not currently registered in Trino",
      },
    ],
    errors: [],
    sql: "",
    trino_query_id: null,
    elapsed_seconds: 0,
  },
};

const previewBody = {
  from: "Company",
  object_type_id: TYPE_ID,
  limit: 25,
  timeout_seconds: 10,
  sql: "SELECT '...' AS _datasource, name FROM \"tpch\".\"tiny\".\"companies\" LIMIT 25",
  factories_used: [
    {
      factory_id: FACTORY_ID,
      data_source_id: "ds1",
      data_source_path: "tpch.tiny.companies",
      use_all_columns: false,
      column_spec: ["name"],
    },
  ],
  factories_skipped: [],
};

describe("Query page", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("disables Run when no object type is selected", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse([typeRow]),
    );
    renderPage();
    await waitFor(() => screen.getByTestId("from-select"));
    expect(screen.getByTestId("run-button")).toBeDisabled();
    expect(screen.getByTestId("preview-button")).toBeDisabled();
  });

  it("shows an empty-types hint when nothing is registered", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse([]),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("no-types-hint")).toBeInTheDocument());
  });

  it("runs a query and renders the result table + factories used", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    let queryBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/object-types?search=") || url.endsWith("/object-types")) {
        return jsonResponse([typeRow]);
      }
      if (url.endsWith("/query") && method === "POST") {
        queryBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse(successResult);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderPage();
    await waitFor(() =>
      expect(
        (screen.getByTestId("from-select") as HTMLSelectElement).options.length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(screen.getByTestId("from-select"), "Company");
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => expect(screen.getByTestId("result-panel")).toBeInTheDocument());
    expect(queryBody).toEqual({ from: "Company", limit: 25, timeout_seconds: 10 });
    expect(screen.getByTestId("result-status-pill")).toHaveTextContent(/OK/i);
    // Both rows render.
    expect(screen.getByTestId("results-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("results-row-1")).toBeInTheDocument();
    // The used-factory shows up as a link to the factory detail page.
    const usedList = screen.getByTestId("factories-used");
    expect(usedList).toHaveTextContent("tpch.tiny.companies");
  });

  it("preview shows the SQL and which factories would be used", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/object-types")) return jsonResponse([typeRow]);
      if (url.endsWith("/preview-query-plan") && method === "POST") {
        return jsonResponse(previewBody);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderPage();
    await waitFor(() =>
      expect(
        (screen.getByTestId("from-select") as HTMLSelectElement).options.length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(screen.getByTestId("from-select"), "Company");
    await user.click(screen.getByTestId("preview-button"));

    await waitFor(() => expect(screen.getByTestId("preview-panel")).toBeInTheDocument());
    expect(screen.getByTestId("preview-sql")).toHaveTextContent("SELECT");
    expect(screen.getByTestId("factories-used")).toHaveTextContent(
      "tpch.tiny.companies",
    );
  });

  it("renders partial state with skipped factories when a catalog is missing", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/object-types")) return jsonResponse([typeRow]);
      if (url.endsWith("/query") && method === "POST") {
        return jsonResponse(partialResult);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderPage();
    await waitFor(() =>
      expect(
        (screen.getByTestId("from-select") as HTMLSelectElement).options.length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(screen.getByTestId("from-select"), "Company");
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => expect(screen.getByTestId("result-panel")).toBeInTheDocument());
    expect(screen.getByTestId("result-status-pill")).toHaveTextContent(/PARTIAL/i);
    expect(screen.getByTestId("factories-skipped")).toHaveTextContent(
      "ghost.public.t",
    );
    expect(screen.getByTestId("results-empty")).toBeInTheDocument();
  });

  it("404 from server when type is unknown surfaces an error banner", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/object-types")) return jsonResponse([typeRow]);
      if (url.endsWith("/query") && method === "POST") {
        return jsonResponse(
          {
            error: "object_type_not_found",
            details: "No object type named 'Company'.",
          },
          404,
        );
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderPage();
    await waitFor(() =>
      expect(
        (screen.getByTestId("from-select") as HTMLSelectElement).options.length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(screen.getByTestId("from-select"), "Company");
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() =>
      expect(screen.getByTestId("error-banner")).toHaveTextContent(/object type/i),
    );
  });
});
