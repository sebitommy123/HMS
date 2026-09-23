import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChangesetPanel } from "@/components/ChangesetPanel";
import type { StagedAction } from "@/api/changeset";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const ACTIONS: StagedAction[] = [
  { id: "a1", op: "create_catalog", handle: "sales_cat", name: "sales", connector: "postgresql" },
  { id: "a2", op: "create_object_type", handle: "cust", name: "customer", description: "" },
  { id: "a3", op: "add_trait", trait: "identity", target: { source: "action", handle: "cust" } },
  {
    id: "a4",
    op: "create_object_factory",
    handle: "f1",
    data_source: { catalog: { source: "action", handle: "sales_cat" }, schema_name: "public", table: "customers" },
    object_type: { source: "action", handle: "cust" },
  },
  { id: "a5", op: "delete_object_factory", target: { source: "prod", kind: "object_factory", id: "old-fac" } },
];

function renderPanel(props?: Partial<React.ComponentProps<typeof ChangesetPanel>>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChangesetPanel
        conversationId="conv-1"
        changeset={ACTIONS}
        tests={[]}
        coreDown={false}
        {...props}
      />
    </QueryClientProvider>,
  );
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("ChangesetPanel", () => {
  it("renders the staged actions with count, family badges and readable summaries", () => {
    renderPanel();
    expect(screen.getByTestId("changeset-panel")).toBeInTheDocument();
    expect(screen.getByTestId("changeset-count")).toHaveTextContent("5");
    // one row per action
    expect(screen.getByTestId("staged-action-create_catalog")).toBeInTheDocument();
    expect(screen.getByTestId("staged-action-create_object_factory")).toBeInTheDocument();
    // readable summary text
    expect(screen.getByText(/Create catalog/)).toBeInTheDocument();
    expect(screen.getByText("sales")).toBeInTheDocument();
    expect(screen.getByText(/Add trait/)).toBeInTheDocument();
    // factory summary shows the structural data-source path + arrow
    expect(screen.getByText(/public\.customers/)).toBeInTheDocument();
  });

  it("renders nothing when the changeset is empty", () => {
    renderPanel({ changeset: [] });
    expect(screen.queryByTestId("changeset-panel")).not.toBeInTheDocument();
  });

  it("expands an action into a materialized field form (not JSON), starting collapsed", async () => {
    renderPanel();
    // detail hidden until expanded
    expect(screen.queryByTestId("action-detail")).not.toBeInTheDocument();
    // expand the first action (create_catalog)
    await userEvent.click(screen.getAllByTestId("toggle-action")[0]);
    const detail = await screen.findByTestId("action-detail");
    // every field rendered with a human label + value — no raw JSON
    expect(detail).toHaveTextContent("Handle");
    expect(detail).toHaveTextContent("sales_cat");
    expect(detail).toHaveTextContent("Name");
    expect(detail).toHaveTextContent("sales");
    expect(detail).toHaveTextContent("Connector");
    expect(detail).toHaveTextContent("postgresql");
    expect(detail).not.toHaveTextContent(/[{}]/); // no JSON braces
    // the collapsed one-line summary is hidden while expanded (not duplicated)
    expect(screen.queryByText(/Create catalog/)).not.toBeInTheDocument();
  });

  it("shows the agent's note (human description) when the action has one", async () => {
    const withNote: StagedAction[] = [
      {
        id: "n1",
        op: "create_object_type",
        handle: "log",
        name: "log",
        note: "Adds a Log object type so events can be modeled.",
      },
    ];
    renderPanel({ changeset: withNote });
    await userEvent.click(screen.getByTestId("toggle-action"));
    const detail = await screen.findByTestId("action-detail");
    expect(detail).toHaveTextContent("Adds a Log object type so events can be modeled.");
    // the note is prose, not one of the labeled field rows
    expect(detail).not.toHaveTextContent(/^Note/);
  });

  it("masks secret-looking property values in the detail form", async () => {
    const withSecret: StagedAction[] = [
      {
        id: "s1",
        op: "create_catalog",
        handle: "pg",
        name: "pg",
        connector: "postgresql",
        properties: { "connection-url": "jdbc:postgresql://h/db", "connection-password": "hunter2" },
      },
    ];
    renderPanel({ changeset: withSecret });
    await userEvent.click(screen.getByTestId("toggle-action"));
    const detail = await screen.findByTestId("action-detail");
    expect(detail).toHaveTextContent("connection-url");
    expect(detail).not.toHaveTextContent("hunter2"); // password masked
  });

  it("is not itself collapsible (no whole-panel toggle)", () => {
    renderPanel();
    // the action list is always visible — no top-level collapse button
    expect(screen.getByTestId("changeset-header")).toBeInTheDocument();
    expect(screen.getByTestId("staged-action-create_catalog")).toBeVisible();
  });

  it("applies to production and shows the success banner", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ applied: true, result: { catalogs_promoted: 1, object_types_promoted: 1, factories_promoted: 1 } }),
    );
    renderPanel();
    await userEvent.click(screen.getByTestId("apply-changeset"));
    const banner = await screen.findByTestId("apply-success");
    expect(banner).toHaveTextContent(/Applied to production/);
    expect(banner).toHaveTextContent(/1 catalogs promoted/);
    // hit the apply endpoint
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/conversations/conv-1/changeset/apply"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("surfaces a promote conflict (409) as a drift banner", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ applied: false, conflicts: [{ kind: "collision", entity: "object_type", name: "customer" }] }, 409),
    );
    renderPanel();
    await userEvent.click(screen.getByTestId("apply-changeset"));
    const banner = await screen.findByTestId("apply-conflict");
    expect(banner).toHaveTextContent(/Production drifted/);
    expect(banner).toHaveTextContent(/collision/);
    expect(banner).toHaveTextContent(/customer/);
  });

  it("runs saved tests and renders per-test results", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ tests: [{ name: "has customers", passed: true, objects: 4 }], all_passed: true }),
    );
    renderPanel({ tests: [{ name: "has customers", from: "customer" }] });
    await userEvent.click(screen.getByTestId("test-stage"));
    const results = await screen.findByTestId("test-results");
    expect(results).toHaveTextContent(/All tests passed/);
    expect(results).toHaveTextContent(/has customers/);
    expect(results).toHaveTextContent(/4 objects/);
  });

  it("removes an action via the DELETE endpoint", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ actions: ACTIONS.slice(1) }));
    renderPanel();
    const firstRemove = screen.getAllByTestId("remove-action")[0];
    await userEvent.click(firstRemove);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/conversations/conv-1/changeset/actions/a1"),
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("disables test + apply when Core is down", () => {
    renderPanel({ coreDown: true });
    expect(screen.getByTestId("apply-changeset")).toBeDisabled();
    expect(screen.getByTestId("test-stage")).toBeDisabled();
  });
});
