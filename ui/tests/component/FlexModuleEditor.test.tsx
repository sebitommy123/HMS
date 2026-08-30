/**
 * FlexModuleEditor — view / edit / preview / save for a flex catalog's
 * Python source.
 *
 * Monaco is heavy and not test-friendly out of the box, so we mock
 * @monaco-editor/react with a plain <textarea>. The interaction
 * surface (typing → onChange, displayed value) is what these tests
 * actually care about.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FlexModuleEditor } from "@/components/FlexModuleEditor";

vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
    options,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
    options?: { readOnly?: boolean };
  }) => (
    <textarea
      data-testid="monaco-mock"
      readOnly={options?.readOnly}
      value={value ?? ""}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FlexModuleEditor catalogName="my_flex" />
    </QueryClientProvider>,
  );
}

const NOW = new Date().toISOString();

const moduleRow = {
  id: "11111111-1111-4111-8111-111111111111",
  catalog_name: "my_flex",
  source_text: "def get_tables(): return []\n",
  created_at: NOW,
  updated_at: NOW,
};

describe("FlexModuleEditor", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("is collapsed on first paint, then expands read-only", async () => {
    const user = userEvent.setup();
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse(moduleRow),
    );
    renderEditor();

    // Collapsed by default: a placeholder, no editor, no Edit button.
    await waitFor(() =>
      expect(screen.getByTestId("flex-source-collapsed")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("monaco-mock")).not.toBeInTheDocument();
    expect(screen.queryByTestId("flex-edit-button")).not.toBeInTheDocument();

    // Expand → the source shows read-only, and the Edit affordance appears.
    await user.click(screen.getByTestId("flex-toggle-button"));
    const editor = screen.getByTestId("monaco-mock") as HTMLTextAreaElement;
    expect(editor).toHaveValue(moduleRow.source_text);
    expect(editor).toHaveAttribute("readonly");
    expect(screen.getByTestId("flex-edit-button")).toBeInTheDocument();
  });

  it("404 from /flex-modules hides the editor (non-Phase-B flex catalog)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ error: "not_found", catalog_name: "my_flex" }, 404),
    );
    const { container } = renderEditor();
    await waitFor(() => expect(container.textContent).not.toMatch(/Loading/));
    // Nothing visible (the component returns null).
    expect(container.firstChild).toBeNull();
  });

  it("Edit unlocks the editor and saves via PUT", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    let putBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/flex-modules/my_flex") && method === "GET") {
        return jsonResponse(moduleRow);
      }
      if (url.endsWith("/flex-modules/my_flex") && method === "PUT") {
        putBody = init?.body ? JSON.parse(init.body as string) : null;
        return jsonResponse({ ...moduleRow, source_text: "edited" });
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderEditor();
    await waitFor(() => screen.getByTestId("flex-toggle-button"));
    await user.click(screen.getByTestId("flex-toggle-button")); // expand source

    await user.click(screen.getByTestId("flex-edit-button"));
    const editor = screen.getByTestId("monaco-mock") as HTMLTextAreaElement;
    expect(editor).not.toHaveAttribute("readonly");

    // Save is disabled while unchanged.
    expect(screen.getByTestId("flex-save-button")).toBeDisabled();

    await user.clear(editor);
    await user.type(editor, "edited");
    expect(screen.getByTestId("flex-save-button")).toBeEnabled();

    await user.click(screen.getByTestId("flex-save-button"));
    await waitFor(() => expect(putBody).toEqual({ source: "edited" }));

    // After successful save, the edit affordance comes back (we're
    // out of edit mode) and the editor is read-only again.
    await waitFor(() =>
      expect(screen.getByTestId("flex-edit-button")).toBeInTheDocument(),
    );
  });

  it("Preview hits POST /flex-modules/preview and renders the result panel", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/flex-modules/my_flex") && method === "GET") {
        return jsonResponse(moduleRow);
      }
      if (url.endsWith("/flex-modules/preview") && method === "POST") {
        return jsonResponse({
          tables: [
            {
              schema: "default",
              name: "items",
              columns: [{ name: "id", type: "bigint" }],
              sample_columns: ["id"],
              sample_rows: [[1], [2], [3]],
            },
          ],
        });
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderEditor();
    await waitFor(() => screen.getByTestId("flex-toggle-button"));
    await user.click(screen.getByTestId("flex-toggle-button")); // expand source
    await user.click(screen.getByTestId("flex-edit-button"));
    await user.click(screen.getByTestId("flex-preview-button"));

    await waitFor(() =>
      expect(
        screen.getByTestId("preview-table-default-items"),
      ).toBeInTheDocument(),
    );
  });

  it("surfaces a 400 invalid_python from PUT in the inline error banner", async () => {
    const user = userEvent.setup();
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/flex-modules/my_flex") && method === "GET") {
        return jsonResponse(moduleRow);
      }
      if (url.endsWith("/flex-modules/my_flex") && method === "PUT") {
        return jsonResponse(
          { error: "invalid_python", details: "line 1: invalid syntax" },
          400,
        );
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });

    renderEditor();
    await waitFor(() => screen.getByTestId("flex-toggle-button"));
    await user.click(screen.getByTestId("flex-toggle-button")); // expand source
    await user.click(screen.getByTestId("flex-edit-button"));
    const editor = screen.getByTestId("monaco-mock");
    await user.type(editor, "junk");
    await user.click(screen.getByTestId("flex-save-button"));

    await waitFor(() =>
      expect(screen.getByTestId("flex-editor-error")).toHaveTextContent(
        /invalid_python/,
      ),
    );
  });
});
