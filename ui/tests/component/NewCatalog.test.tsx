import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { NewCatalog } from "@/pages/NewCatalog";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderForm() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/catalogs/new"]}>
        <Routes>
          <Route path="/catalogs/new" element={<NewCatalog />} />
          <Route
            path="/catalogs/:name"
            element={<div data-testid="detail-page" />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NewCatalog form", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("validates required fields on submit", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("error-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("error-connector")).toBeInTheDocument();
    // fetch should not have been called.
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("rejects invalid characters in name and connector", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/Name/i), "bad name!");
    await user.type(screen.getByLabelText(/Connector/i), "weird-connector");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("error-name")).toHaveTextContent(/letters/i);
    });
    expect(screen.getByTestId("error-connector")).toHaveTextContent(/letters/i);
  });

  it("submits a valid form, sends properties as an object, and navigates to the detail page", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          catalog: {
            name: "prod_orders",
            connector: "postgresql",
            properties: { "connection-url": "jdbc:postgresql://h/db" },
            status: "enabled",
            last_error: null,
            version: 1,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          reconcile: { all_ok: true, actions: [] },
        },
        201,
      ),
    );

    renderForm();

    await user.type(screen.getByLabelText(/Name/i), "prod_orders");
    await user.type(screen.getByLabelText(/Connector/i), "postgresql");
    await user.type(screen.getByTestId("property-key-0"), "connection-url");
    await user.type(screen.getByTestId("property-value-0"), "jdbc:postgresql://h/db");

    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("detail-page")).toBeInTheDocument();
    });

    // Confirm the POST body was an object, not an array.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0];
    const body = JSON.parse(call[1]!.body as string);
    expect(body).toEqual({
      name: "prod_orders",
      connector: "postgresql",
      properties: { "connection-url": "jdbc:postgresql://h/db" },
    });
  });

  it("maps a 409 server response to a name-field error", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: "already_exists", name: "tpch_demo" }, 409),
    );

    renderForm();

    await user.type(screen.getByLabelText(/Name/i), "tpch_demo");
    await user.type(screen.getByLabelText(/Connector/i), "tpch");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("error-name")).toHaveTextContent(/already exists/i);
    });
    // We didn't navigate away.
    expect(screen.queryByTestId("detail-page")).not.toBeInTheDocument();
  });

  it("still navigates to detail page on 502 (catalog was persisted as broken)", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          catalog: {
            name: "oops",
            connector: "nonexistent_plugin",
            properties: {},
            status: "broken",
            last_error: "Trino said no",
            version: 1,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          reconcile: { all_ok: false, actions: [{ kind: "create", name: "oops", ok: false, error: "..." }] },
        },
        502,
      ),
    );

    renderForm();

    await user.type(screen.getByLabelText(/Name/i), "oops");
    await user.type(screen.getByLabelText(/Connector/i), "nonexistent_plugin");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("detail-page")).toBeInTheDocument();
    });
  });

  it("flags duplicate property keys", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/Name/i), "x");
    await user.type(screen.getByLabelText(/Connector/i), "memory");
    await user.click(screen.getByTestId("add-property"));

    await user.type(screen.getByTestId("property-key-0"), "k");
    await user.type(screen.getByTestId("property-value-0"), "1");
    await user.type(screen.getByTestId("property-key-1"), "k");
    await user.type(screen.getByTestId("property-value-1"), "2");

    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByText(/duplicate key/i)).toBeInTheDocument();
    });
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("shows property hints when a known connector is picked, and click-fills them", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/Connector/i), "postgresql");

    const hints = await screen.findByTestId("property-hints");
    expect(hints).toHaveTextContent(/connection-url/);

    await user.click(screen.getByRole("button", { name: "connection-url" }));
    expect((screen.getByTestId("property-key-0") as HTMLInputElement).value).toBe(
      "connection-url",
    );
  });
});
