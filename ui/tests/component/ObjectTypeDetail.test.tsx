import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ObjectTypeDetail } from "@/pages/ObjectTypeDetail";

const ISO = new Date().toISOString();
const TYPE_ID = "a1b2c3d4-e5f6-4789-8abc-def012345678";

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
          <Route path="/object-types/:id" element={<ObjectTypeDetail />} />
          <Route
            path="/object-types"
            element={<div data-testid="list-page">list</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const sample = {
  id: TYPE_ID,
  name: "Company",
  description: "a publicly traded company",
  traits: [] as string[],
  created_at: ISO,
  updated_at: ISO,
};

describe("ObjectTypeDetail", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders name, id, and description", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse(sample),
    );
    renderAt(`/object-types/${TYPE_ID}`);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Company" })).toBeInTheDocument(),
    );
    expect(screen.getByTestId("object-type-id")).toHaveTextContent(TYPE_ID);
    expect(screen.getByTestId("description-text")).toHaveTextContent(
      /publicly traded/,
    );
  });

  it("renders the 'no description' state when description is empty", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ ...sample, description: "" }),
    );
    renderAt(`/object-types/${TYPE_ID}`);
    await waitFor(() =>
      expect(screen.getByTestId("description-empty")).toBeInTheDocument(),
    );
  });

  it("edits name + description and PATCHes", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    let patched = false;
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-types/${TYPE_ID}`) && method === "GET") {
        return jsonResponse(
          patched
            ? { ...sample, name: "Issuer", description: "renamed" }
            : sample,
        );
      }
      if (url.endsWith(`/object-types/${TYPE_ID}`) && method === "PATCH") {
        patched = true;
        patchBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({ ...sample, name: "Issuer", description: "renamed" });
      }
      if (url.endsWith("/object-types") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-types/${TYPE_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));

    await user.click(screen.getByTestId("edit-button"));
    const nameInput = screen.getByTestId("edit-name-input");
    await user.clear(nameInput);
    await user.type(nameInput, "Issuer");
    const descInput = screen.getByTestId("edit-description-input");
    await user.clear(descInput);
    await user.type(descInput, "renamed");

    await user.click(screen.getByTestId("save-button"));

    await waitFor(() => expect(patched).toBe(true));
    expect(patchBody).toEqual({ name: "Issuer", description: "renamed" });

    // Editor closes; the new name shows in the header.
    await waitFor(() => expect(screen.queryByTestId("editor")).toBeNull());
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Issuer" })).toBeInTheDocument(),
    );
  });

  it("a no-op save (no fields changed) doesn't hit the server", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-types/${TYPE_ID}`) && method === "GET") {
        return jsonResponse(sample);
      }
      if (url.endsWith("/object-types") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-types/${TYPE_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("save-button"));

    // No PATCH was sent — editor still closes.
    await waitFor(() => expect(screen.queryByTestId("editor")).toBeNull());
    expect(
      fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PATCH"),
    ).toBeUndefined();
  });

  it("flags invalid name client-side without PATCH", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse(sample));

    renderAt(`/object-types/${TYPE_ID}`);
    await waitFor(() => screen.getByTestId("edit-button"));
    await user.click(screen.getByTestId("edit-button"));
    const nameInput = screen.getByTestId("edit-name-input");
    await user.clear(nameInput);
    await user.type(nameInput, "bad name!");
    await user.click(screen.getByTestId("save-button"));

    expect(screen.getByTestId("edit-error")).toHaveTextContent(/alphanumerics/);
    expect(
      fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PATCH"),
    ).toBeUndefined();
  });

  it("deletes after confirm and navigates back to the list", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith(`/object-types/${TYPE_ID}`) && method === "GET") {
        return jsonResponse(sample);
      }
      if (url.endsWith(`/object-types/${TYPE_ID}`) && method === "DELETE") {
        return jsonResponse({ deleted: TYPE_ID });
      }
      if (url.endsWith("/object-types") && method === "GET") {
        return jsonResponse([]);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderAt(`/object-types/${TYPE_ID}`);
    await waitFor(() => screen.getByTestId("delete-button"));

    await user.click(screen.getByTestId("delete-button"));
    expect(screen.getByTestId("delete-confirm")).toBeInTheDocument();
    await user.click(screen.getByTestId("delete-confirm-button"));

    await waitFor(() => expect(screen.getByTestId("list-page")).toBeInTheDocument());
  });

  it("404 renders the not-found state", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ error: "not_found", id: TYPE_ID }, 404),
    );
    renderAt(`/object-types/${TYPE_ID}`);
    await waitFor(() => expect(screen.getByTestId("not-found")).toBeInTheDocument());
  });
});
