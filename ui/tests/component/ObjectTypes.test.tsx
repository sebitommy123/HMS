import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ObjectTypes } from "@/pages/ObjectTypes";

const ISO = new Date().toISOString();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function row(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "a1b2c3d4-e5f6-4789-8abc-def012345678",
    name: "Company",
    description: "",
    traits: [] as string[],
    created_at: ISO,
    updated_at: ISO,
    ...over,
  };
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/object-types" element={<ObjectTypes />} />
          <Route
            path="/object-types/:id"
            element={<div data-testid="detail-page" />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ObjectTypes (list)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the list when types exist", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse([
        row({ name: "Company", description: "a publicly traded company" }),
        row({
          id: "b2c3d4e5-f6a7-4890-9bcd-ef0123456789",
          name: "Filing",
          description: "an SEC filing",
        }),
      ]),
    );
    renderAt("/object-types");

    await waitFor(() => expect(screen.getByTestId("object-types-list")).toBeInTheDocument());
    expect(screen.getByTestId("object-type-row-Company")).toBeInTheDocument();
    expect(screen.getByTestId("object-type-row-Filing")).toBeInTheDocument();
  });

  it("renders the empty state when there are no types", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse([]),
    );
    renderAt("/object-types");
    await waitFor(() => expect(screen.getByTestId("empty-state")).toBeInTheDocument());
  });

  it("passes the search query to the server", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("search=comp")) {
        return jsonResponse([row({ name: "Company" })]);
      }
      return jsonResponse([
        row(),
        row({ id: "b2c3d4e5-f6a7-4890-9bcd-ef0123456789", name: "Filing" }),
      ]);
    });

    renderAt("/object-types");
    await waitFor(() =>
      expect(screen.getByTestId("object-type-row-Filing")).toBeInTheDocument(),
    );

    await user.type(screen.getByTestId("search-input"), "comp");

    await waitFor(() => {
      expect(screen.queryByTestId("object-type-row-Filing")).not.toBeInTheDocument();
      expect(screen.getByTestId("object-type-row-Company")).toBeInTheDocument();
    });
  });

  it("creates a new type from the inline form", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    let createBody: unknown = null;
    let created = false;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/object-types") && method === "POST") {
        created = true;
        createBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse(row({ name: "NewType", description: "a description" }), 201);
      }
      if (url.includes("/object-types") && method === "GET") {
        return jsonResponse(
          created ? [row({ name: "NewType", description: "a description" })] : [],
        );
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt("/object-types");
    await waitFor(() => expect(screen.getByTestId("empty-state")).toBeInTheDocument());

    await user.click(screen.getByTestId("new-button"));
    await user.type(screen.getByTestId("new-name-input"), "NewType");
    await user.type(screen.getByTestId("new-description-input"), "a description");
    await user.click(screen.getByTestId("create-button"));

    await waitFor(() => expect(created).toBe(true));
    expect(createBody).toEqual({ name: "NewType", description: "a description" });

    await waitFor(() =>
      expect(screen.getByTestId("object-type-row-NewType")).toBeInTheDocument(),
    );
  });

  it("rejects invalid names client-side without hitting the server", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse([]));

    renderAt("/object-types");
    await waitFor(() => expect(screen.getByTestId("empty-state")).toBeInTheDocument());

    await user.click(screen.getByTestId("new-button"));
    await user.type(screen.getByTestId("new-name-input"), "bad name!");
    await user.click(screen.getByTestId("create-button"));

    expect(screen.getByTestId("new-error")).toHaveTextContent(/alphanumerics/);
    expect(
      fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "POST"),
    ).toBeUndefined();
  });

  it("409 from server shows already-exists error", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/object-types") && method === "POST") {
        return jsonResponse({ error: "already_exists", name: "Company" }, 409);
      }
      return jsonResponse([row({ name: "Company" })]);
    });

    renderAt("/object-types");
    await waitFor(() =>
      expect(screen.getByTestId("object-type-row-Company")).toBeInTheDocument(),
    );

    await user.click(screen.getByTestId("new-button"));
    await user.type(screen.getByTestId("new-name-input"), "Company");
    await user.click(screen.getByTestId("create-button"));

    await waitFor(() =>
      expect(screen.getByTestId("new-error")).toHaveTextContent(/already exists/i),
    );
  });
});
