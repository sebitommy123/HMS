import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ObjectFactoryDetail } from "@/pages/ObjectFactoryDetail";

const ISO = new Date().toISOString();
const FACTORY_ID = "c3d4e5f6-a7b8-4901-acde-f01234567890";
const TYPE_ID = "a1b2c3d4-e5f6-4789-8abc-def012345678";
const DATA_SOURCE_ID = "d4e5f6a7-b8c9-4012-bdef-012345678901";

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
          <Route path="/object-factories/:id" element={<ObjectFactoryDetail />} />
          <Route
            path="/catalogs/:name"
            element={<div data-testid="catalog-page">catalog page</div>}
          />
          <Route
            path="/object-types/:id"
            element={<div data-testid="type-page">type page</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const sample = {
  id: FACTORY_ID,
  data_source_id: DATA_SOURCE_ID,
  catalog_name: "tpch_demo",
  schema_name: "tiny",
  table_name: "nation",
  data_source_path: "tpch_demo.tiny.nation",
  object_type_id: TYPE_ID,
  object_type_name: "Company",
  object_type_traits: [] as string[],
  description: "hello world",
  use_all_columns: true,
  column_spec: [] as string[],
  trait_config: {} as Record<string, Record<string, unknown>>,
  status: "ok" as const,
  last_error: null as string | null,
  created_at: ISO,
  updated_at: ISO,
};

describe("ObjectFactoryDetail", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("renders the factory with links to both parents", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(sample),
    );
    renderAt(`/object-factories/${FACTORY_ID}`);

    await waitFor(() => screen.getByTestId("parent-source-link"));
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(
      "tpch_demo.tiny.nation produces Company objects",
    );
    expect(screen.getByTestId("parent-source-link")).toHaveAttribute(
      "href",
      `/data-sources/${DATA_SOURCE_ID}`,
    );
    expect(screen.getByTestId("parent-type-link")).toHaveAttribute(
      "href",
      `/object-types/${TYPE_ID}`,
    );
    expect(screen.getByText("Object factory")).toBeInTheDocument();
    expect(screen.getByTestId("description-text")).toHaveTextContent("hello world");
  });

  it("edits the description and PATCHes", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    let patched = false;
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "GET") {
        return jsonResponse(
          patched ? { ...sample, description: "edited" } : sample,
        );
      }
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "PATCH") {
        patched = true;
        patchBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({ ...sample, description: "edited" });
      }
      if (url.includes("/object-factories") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));

    await user.click(screen.getByTestId("edit-button"));
    const input = screen.getByTestId("edit-description-input");
    await user.clear(input);
    await user.type(input, "edited");
    await user.click(screen.getByTestId("save-button"));

    await waitFor(() => expect(patched).toBe(true));
    expect(patchBody).toEqual({ description: "edited" });
    await waitFor(() => expect(screen.queryByTestId("editor")).toBeNull());
    await waitFor(() =>
      expect(screen.getByTestId("description-text")).toHaveTextContent("edited"),
    );
  });

  it("toggles columns mode and PATCHes use_all_columns + a list of columns", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    let patched = false;
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "GET") {
        return jsonResponse(
          patched
            ? { ...sample, use_all_columns: false, column_spec: ["id", "name"] }
            : sample,
        );
      }
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "PATCH") {
        patched = true;
        patchBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({
          ...sample,
          use_all_columns: false,
          column_spec: ["id", "name"],
        });
      }
      // Editor fetches the data source's column list to populate the picker.
      if (
        url.endsWith(`/data-sources/${DATA_SOURCE_ID}/columns`) &&
        method === "GET"
      ) {
        return jsonResponse({
          data_source_id: DATA_SOURCE_ID,
          path: "tpch_demo.tiny.nation",
          columns: [
            { name: "id", type: "bigint" },
            { name: "name", type: "varchar" },
          ],
        });
      }
      if (url.includes("/object-factories") && method === "GET") return jsonResponse([]);
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));

    // Toggle off, then pick two columns from the dropdowns one at a time.
    const toggle = screen.getByTestId("use-all-columns-toggle") as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    await user.click(toggle);
    expect(toggle.checked).toBe(false);

    // Wait for the columns picker to populate (data-source columns load).
    await waitFor(() =>
      expect(
        (screen.getByTestId("add-column-button") as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    await user.click(screen.getByTestId("add-column-button"));
    await user.selectOptions(screen.getByTestId("column-input-0"), "id");
    await user.click(screen.getByTestId("add-column-button"));
    await user.selectOptions(screen.getByTestId("column-input-1"), "name");

    await user.click(screen.getByTestId("save-button"));

    await waitFor(() => expect(patched).toBe(true));
    expect(patchBody).toEqual({
      use_all_columns: false,
      column_spec: ["id", "name"],
    });
    await waitFor(() =>
      expect(screen.getByTestId("columns-specific")).toBeInTheDocument(),
    );
  });

  it("removes a column row from the editor before saving", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    let patchBody: unknown = null;
    const sampleWithCols = {
      ...sample,
      use_all_columns: false,
      column_spec: ["id", "name", "sic"],
    };
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "GET") {
        return jsonResponse(sampleWithCols);
      }
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "PATCH") {
        patchBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({ ...sampleWithCols, column_spec: ["id", "sic"] });
      }
      if (
        url.endsWith(`/data-sources/${DATA_SOURCE_ID}/columns`) &&
        method === "GET"
      ) {
        return jsonResponse({
          data_source_id: DATA_SOURCE_ID,
          path: "tpch_demo.tiny.nation",
          columns: [
            { name: "id", type: "bigint" },
            { name: "name", type: "varchar" },
            { name: "sic", type: "varchar" },
          ],
        });
      }
      if (url.includes("/object-factories") && method === "GET") return jsonResponse([]);
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));

    // Wait for the columns to load so the row testids exist.
    await waitFor(() => screen.getByTestId("remove-column-1"));
    // Remove the middle row ("name").
    await user.click(screen.getByTestId("remove-column-1"));
    await user.click(screen.getByTestId("save-button"));

    await waitFor(() =>
      expect(patchBody).toEqual({ column_spec: ["id", "sic"] }),
    );
  });

  it("flags stale entries that no longer exist on the data source", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const stale = {
      ...sample,
      use_all_columns: false,
      column_spec: ["id", "vanished_col"],
    };
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "GET") {
        return jsonResponse(stale);
      }
      if (url.endsWith(`/data-sources/${DATA_SOURCE_ID}/columns`) && method === "GET") {
        return jsonResponse({
          data_source_id: DATA_SOURCE_ID,
          path: "tpch_demo.tiny.nation",
          columns: [{ name: "id", type: "bigint" }],
        });
      }
      if (url.includes("/object-factories") && method === "GET") return jsonResponse([]);
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));

    // Stale entry's select is rendered with a "not in source" sentinel option.
    await waitFor(() => screen.getByTestId("column-input-1"));
    const staleSelect = screen.getByTestId("column-input-1") as HTMLSelectElement;
    const optionTexts = Array.from(staleSelect.options).map((o) => o.textContent ?? "");
    expect(optionTexts.some((t) => t.includes("not in source"))).toBe(true);
  });

  it("shows an error banner when the data source's columns can't be fetched", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "GET") {
        return jsonResponse(sample);
      }
      if (url.endsWith(`/data-sources/${DATA_SOURCE_ID}/columns`) && method === "GET") {
        return jsonResponse(
          { error: "trino_error", details: "table not found" },
          502,
        );
      }
      if (url.includes("/object-factories") && method === "GET") return jsonResponse([]);
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));

    await waitFor(() =>
      expect(screen.getByTestId("columns-fetch-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("columns-fetch-error")).toHaveTextContent(/trino/i);
    // Add-column is disabled while we can't see the available columns.
    expect(
      (screen.getByTestId("add-column-button") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("deletes after confirm", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    let deleted = false;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "GET") {
        return jsonResponse(sample);
      }
      if (url.endsWith(`/object-factories/${FACTORY_ID}`) && method === "DELETE") {
        deleted = true;
        return jsonResponse({ deleted: FACTORY_ID });
      }
      if (url.includes("/object-factories") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => screen.getByTestId("delete-button"));

    await user.click(screen.getByTestId("delete-button"));
    await user.click(screen.getByTestId("delete-confirm-button"));

    await waitFor(() => expect(deleted).toBe(true));
  });

  it("renders the broken status badge + last_error panel", async () => {
    const broken = {
      ...sample,
      status: "broken" as const,
      last_error: "catalog 'ghost' is not currently registered in Trino",
    };
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(broken),
    );
    renderAt(`/object-factories/${FACTORY_ID}`);

    await waitFor(() =>
      expect(screen.getByTestId("factory-status-badge")).toHaveTextContent(/broken/i),
    );
    expect(screen.getByTestId("factory-last-error")).toHaveTextContent(
      /catalog 'ghost'/,
    );
  });

  it("404 renders the not-found state", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ error: "not_found", id: FACTORY_ID }, 404),
    );
    renderAt(`/object-factories/${FACTORY_ID}`);
    await waitFor(() => expect(screen.getByTestId("not-found")).toBeInTheDocument());
  });
});
