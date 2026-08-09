import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import {
  DataSourceFactoriesPanel,
  ObjectTypeFactoriesPanel,
} from "@/components/ObjectFactoriesPanel";

const ISO = new Date().toISOString();
const TYPE_ID = "a1b2c3d4-e5f6-4789-8abc-def012345678";
const TYPE_ID_2 = "b2c3d4e5-f6a7-4890-9bcd-ef0123456789";
const DATA_SOURCE_ID = "c3d4e5f6-a7b8-4901-acde-f01234567890";
const FACTORY_ID = "d4e5f6a7-b8c9-4012-bdef-012345678901";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function render_(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
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

const factoryRow = {
  id: FACTORY_ID,
  data_source_id: DATA_SOURCE_ID,
  catalog_name: "tpch_demo",
  schema_name: "tiny",
  table_name: "nation",
  data_source_path: "tpch_demo.tiny.nation",
  object_type_id: TYPE_ID,
  object_type_name: "Company",
  object_type_traits: [] as string[],
  description: "",
  use_all_columns: true,
  column_spec: [] as string[],
  trait_config: {} as Record<string, Record<string, unknown>>,
  status: "ok" as const,
  last_error: null as string | null,
  created_at: ISO,
  updated_at: ISO,
};

describe("DataSourceFactoriesPanel", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("shows the empty state when the source has no factories", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/object-factories")) return jsonResponse([]);
      if (url.includes("/object-types")) return jsonResponse([typeRow]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    render_(<DataSourceFactoriesPanel dataSourceId={DATA_SOURCE_ID} />);

    await waitFor(() =>
      expect(screen.getByTestId("factories-empty")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Produces objects/i)).toBeInTheDocument();
  });

  it("lists factories with the object type name as the row label", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/object-factories")) return jsonResponse([factoryRow]);
      if (url.includes("/object-types")) return jsonResponse([typeRow]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    render_(<DataSourceFactoriesPanel dataSourceId={DATA_SOURCE_ID} />);

    await waitFor(() => expect(screen.getByTestId("factories-list")).toBeInTheDocument());
    expect(screen.getByTestId(`factory-row-${FACTORY_ID}`)).toHaveTextContent("Company");
  });

  it("attaches a new object type via the picker", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    let posted = false;
    let postBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/object-factories") && method === "POST") {
        posted = true;
        postBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({ ...factoryRow }, 201);
      }
      if (url.includes("/object-factories") && method === "GET") {
        return jsonResponse(posted ? [factoryRow] : []);
      }
      if (url.includes("/object-types") && method === "GET") {
        return jsonResponse([typeRow]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    render_(<DataSourceFactoriesPanel dataSourceId={DATA_SOURCE_ID} />);
    await waitFor(() => screen.getByTestId("attach-button"));
    await user.click(screen.getByTestId("attach-button"));

    await user.selectOptions(screen.getByTestId("attach-select"), TYPE_ID);
    await user.click(screen.getByTestId("attach-submit"));

    await waitFor(() => expect(posted).toBe(true));
    expect(postBody).toEqual({
      data_source_id: DATA_SOURCE_ID,
      object_type_id: TYPE_ID,
      description: "",
    });
    await waitFor(() => expect(screen.getByTestId("factories-list")).toBeInTheDocument());
  });

  it("filters the picker to types not already attached", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/object-factories")) return jsonResponse([factoryRow]);
      if (url.includes("/object-types"))
        return jsonResponse([typeRow, { ...typeRow, id: TYPE_ID_2, name: "Filing" }]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    render_(<DataSourceFactoriesPanel dataSourceId={DATA_SOURCE_ID} />);
    await waitFor(() => screen.getByTestId("attach-button"));
    await user.click(screen.getByTestId("attach-button"));

    const select = screen.getByTestId("attach-select") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain(TYPE_ID_2);
    expect(options).not.toContain(TYPE_ID); // already attached
  });
});

describe("ObjectTypeFactoriesPanel", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("lists factories with the data source path as the row label", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/object-factories")) return jsonResponse([factoryRow]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    render_(<ObjectTypeFactoriesPanel typeId={TYPE_ID} />);

    await waitFor(() => expect(screen.getByTestId("factories-list")).toBeInTheDocument());
    expect(screen.getByTestId(`factory-row-${FACTORY_ID}`)).toHaveTextContent(
      "tpch_demo.tiny.nation",
    );
  });

  it("shows a red dot next to broken factories", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/object-factories"))
        return jsonResponse([
          { ...factoryRow, status: "broken", last_error: "catalog gone" },
        ]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    render_(<ObjectTypeFactoriesPanel typeId={TYPE_ID} />);

    await waitFor(() =>
      expect(screen.getByTestId(`factory-broken-dot-${FACTORY_ID}`)).toBeInTheDocument(),
    );
  });

  it("is read-only — no attach button on the object-type side", async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/object-factories")) return jsonResponse([factoryRow]);
      throw new Error(`unexpected fetch: ${url}`);
    });

    render_(<ObjectTypeFactoriesPanel typeId={TYPE_ID} />);
    await waitFor(() => expect(screen.getByTestId("factories-list")).toBeInTheDocument());
    expect(screen.queryByTestId("attach-button")).toBeNull();
  });
});
