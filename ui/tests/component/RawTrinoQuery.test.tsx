import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { RawTrinoQuery } from "@/pages/RawTrinoQuery";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <RawTrinoQuery />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const happy = {
  columns: ["Catalog"],
  rows: [["system"], ["tpch"]],
  row_count: 2,
  truncated: false,
  elapsed_seconds: 0.012,
  query_id: "20260619_120000_00001_aaa",
  applied_limits: { timeout_seconds: 30, max_rows: 10_000 },
};

describe("Query page", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders empty hint initially", () => {
    renderPage();
    expect(screen.getByText(/run a query to see results/i)).toBeInTheDocument();
  });

  it("submits the SQL, renders the table and metadata", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(jsonResponse(happy));

    renderPage();
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => {
      expect(screen.getByTestId("results-table")).toBeInTheDocument();
    });
    expect(screen.getByText("system")).toBeInTheDocument();
    expect(screen.getByText("tpch")).toBeInTheDocument();
    // The metadata strip splits "2 rows" across nested elements; assert via
    // normalised textContent instead of strict getByText.
    expect(screen.getByTestId("result-panel").textContent).toMatch(/2 rows/i);
    expect(screen.getByTestId("result-panel").textContent).toMatch(/0\.01s elapsed/);
    expect(screen.getByText(happy.query_id)).toBeInTheDocument();
  });

  it("sends the configured timeout and max_rows in the request body", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(jsonResponse(happy));

    renderPage();
    await user.selectOptions(screen.getByTestId("timeout-select"), "10");
    await user.selectOptions(screen.getByTestId("max-rows-select"), "100");
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
    expect(body.timeout_seconds).toBe(10);
    expect(body.max_rows).toBe(100);
    expect(body.sql).toBe("SHOW CATALOGS");
  });

  it("renders the truncation banner when result.truncated is true", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...happy, truncated: true, row_count: 100, rows: Array.from({ length: 100 }, (_, i) => [`row${i}`]) }),
    );

    renderPage();
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => {
      expect(screen.getByTestId("truncation-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("truncation-banner")).toHaveTextContent(/truncated at/i);
  });

  it("renders 'no rows returned' when columns is empty", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...happy, columns: [], rows: [], row_count: 0 }),
    );

    renderPage();
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => {
      expect(screen.getByText(/no rows returned/i)).toBeInTheDocument();
    });
  });

  it("renders a Trino-error panel for a 400 trino_error response", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: "trino_error", details: "line 1:8: Column 'nope' cannot be resolved" }, 400),
    );

    renderPage();
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => {
      expect(screen.getByTestId("error-panel")).toBeInTheDocument();
    });
    expect(screen.getByTestId("error-panel")).toHaveTextContent(/Trino error/);
    expect(screen.getByTestId("error-panel")).toHaveTextContent(/Column 'nope' cannot be resolved/);
  });

  it("renders a timeout panel for a 504 timeout response", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: "timeout", details: "query exceeded 1.0s", timeout_seconds: 1 }, 504),
    );

    renderPage();
    await user.click(screen.getByTestId("run-button"));

    await waitFor(() => {
      expect(screen.getByTestId("error-panel")).toBeInTheDocument();
    });
    expect(screen.getByTestId("error-panel")).toHaveTextContent(/Query timed out/i);
  });

  it("disables Run when SQL is empty", async () => {
    const user = userEvent.setup();
    renderPage();
    const ta = screen.getByTestId("sql-input") as HTMLTextAreaElement;
    await user.clear(ta);
    expect(screen.getByTestId("run-button")).toBeDisabled();
  });

  it("Cmd+Enter inside the textarea submits the query", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(jsonResponse(happy));

    renderPage();
    const ta = screen.getByTestId("sql-input") as HTMLTextAreaElement;
    await user.click(ta);
    await user.keyboard("{Meta>}{Enter}{/Meta}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it("picking an example fills the textarea", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.selectOptions(screen.getByTestId("examples-select"), "List schemas in a catalog");
    expect((screen.getByTestId("sql-input") as HTMLTextAreaElement).value).toBe(
      "SHOW SCHEMAS FROM tpch",
    );
  });
});
