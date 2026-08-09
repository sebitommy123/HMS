import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { MessageBlocks } from "@/components/MessageBlocks";

describe("MessageBlocks", () => {
  it("renders a plain text block", () => {
    render(<MessageBlocks blocks={[{ type: "text", text: "hello world" }]} />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("renders a tool_use block with the tool name visible", () => {
    render(
      <MessageBlocks
        blocks={[
          {
            type: "tool_use",
            id: "toolu_1",
            name: "list_catalogs",
            input: {},
          },
        ]}
      />,
    );
    expect(screen.getByTestId("tool-use-list_catalogs")).toBeInTheDocument();
    expect(screen.getByText("list_catalogs")).toBeInTheDocument();
  });

  it("renders a tool_result success block with first line as summary", () => {
    render(
      <MessageBlocks
        blocks={[
          {
            type: "tool_result",
            tool_use_id: "toolu_1",
            content: "first line\nsecond line",
            is_error: false,
          },
        ]}
      />,
    );
    expect(screen.getByTestId("tool-result-ok")).toBeInTheDocument();
    // "first line" appears in both the summary (truncated) and the <pre> body.
    expect(screen.getAllByText(/first line/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/second line/)).toBeInTheDocument();
  });

  it("renders a tool_result error block with the error tone", () => {
    render(
      <MessageBlocks
        blocks={[
          {
            type: "tool_result",
            tool_use_id: "toolu_1",
            content: "Trino rejected the query",
            is_error: true,
          },
        ]}
      />,
    );
    expect(screen.getByTestId("tool-result-error")).toBeInTheDocument();
  });

  it("renders a thinking block (collapsed by default)", () => {
    render(
      <MessageBlocks
        blocks={[{ type: "thinking", thinking: "let me think about this" }]}
      />,
    );
    expect(screen.getByText(/Thinking/i)).toBeInTheDocument();
  });

  it("falls back gracefully for unknown block types", () => {
    render(
      <MessageBlocks
        blocks={[
          { type: "future_block_type", surprise: { nested: "data" } } as never,
        ]}
      />,
    );
    expect(screen.getByText("future_block_type")).toBeInTheDocument();
  });

  it("renders markdown headings, lists, and code in a text block", () => {
    const md = [
      "# Title",
      "",
      "Some prose.",
      "",
      "- one",
      "- two",
      "",
      "`inline code` and a fenced block:",
      "",
      "```sql",
      "SELECT 1",
      "```",
    ].join("\n");
    render(<MessageBlocks blocks={[{ type: "text", text: md }]} />);
    // Structural elements come through, not raw markdown syntax.
    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("two")).toBeInTheDocument();
    expect(screen.getByText("inline code")).toBeInTheDocument();
    expect(screen.getByText(/SELECT 1/)).toBeInTheDocument();
    // No raw '#' or '```' should leak into the rendered output.
    expect(screen.queryByText(/^# Title$/)).not.toBeInTheDocument();
  });

  it("renders GFM tables", () => {
    const md = [
      "| col | val |",
      "| --- | --- |",
      "| foo | 1 |",
      "| bar | 2 |",
    ].join("\n");
    render(<MessageBlocks blocks={[{ type: "text", text: md }]} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "col" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "foo" })).toBeInTheDocument();
  });

  it("opens links in a new tab", () => {
    render(
      <MessageBlocks
        blocks={[{ type: "text", text: "see [docs](https://example.com)" }]}
      />,
    );
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders an array of mixed blocks in order", () => {
    render(
      <MessageBlocks
        blocks={[
          { type: "text", text: "Let me check." },
          {
            type: "tool_use",
            id: "toolu_2",
            name: "run_sql",
            input: { sql: "SHOW CATALOGS" },
          },
        ]}
      />,
    );
    expect(screen.getByText("Let me check.")).toBeInTheDocument();
    expect(screen.getByTestId("tool-use-run_sql")).toBeInTheDocument();
  });
});
