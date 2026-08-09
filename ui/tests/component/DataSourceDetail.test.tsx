import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { DataSourceDetail } from "@/pages/DataSourceDetail";

const ISO = new Date().toISOString();
const SOURCE_ID = "a1b2c3d4-e5f6-4789-8abc-def012345678";

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
          <Route path="/data-sources/:id" element={<DataSourceDetail />} />
          <Route
            path="/catalogs/:name"
            element={<div data-testid="catalog-page">catalog</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const sample = {
  id: SOURCE_ID,
  catalog_name: "tpch_demo",
  schema_name: "tiny",
  table_name: "nation",
  path: "tpch_demo.tiny.nation",
  description: "TPCH nation",
  created_at: ISO,
  updated_at: ISO,
};

describe("DataSourceDetail columns section", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("renders columns from the live introspection endpoint", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith(`/data-sources/${SOURCE_ID}`)) return jsonResponse(sample);
      if (url.endsWith(`/data-sources/${SOURCE_ID}/columns`)) {
        return jsonResponse({
          data_source_id: SOURCE_ID,
          path: "tpch_demo.tiny.nation",
          columns: [
            { name: "nationkey", type: "bigint" },
            { name: "name", type: "varchar(25)" },
            { name: "regionkey", type: "bigint" },
            { name: "comment", type: "varchar(152)" },
          ],
        });
      }
      // The factories panel also lives on this page; return empty for it.
      if (url.includes("/object-factories")) return jsonResponse([]);
      if (url.includes("/object-types")) return jsonResponse([]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    renderAt(`/data-sources/${SOURCE_ID}`);

    await waitFor(() =>
      expect(screen.getByTestId("columns-table")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("column-row-nationkey")).toHaveTextContent("bigint");
    expect(screen.getByTestId("column-row-name")).toHaveTextContent("varchar(25)");
    expect(screen.getByTestId("column-row-regionkey")).toBeInTheDocument();
    expect(screen.getByTestId("column-row-comment")).toBeInTheDocument();
  });

  it("renders an error banner when Trino can't read the columns", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith(`/data-sources/${SOURCE_ID}`)) return jsonResponse(sample);
      if (url.endsWith(`/data-sources/${SOURCE_ID}/columns`)) {
        return jsonResponse(
          {
            error: "trino_error",
            details: "Table 'tpch_demo.tiny.ghost' does not exist",
            path: "tpch_demo.tiny.ghost",
          },
          502,
        );
      }
      if (url.includes("/object-factories")) return jsonResponse([]);
      if (url.includes("/object-types")) return jsonResponse([]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    renderAt(`/data-sources/${SOURCE_ID}`);

    await waitFor(() =>
      expect(screen.getByTestId("columns-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("columns-error")).toHaveTextContent(/trino/i);
    expect(screen.getByTestId("columns-error")).toHaveTextContent(/does not exist/);
  });
});
