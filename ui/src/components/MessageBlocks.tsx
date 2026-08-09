/**
 * Renders the Anthropic content-blocks array inline.
 *
 * Each block type has its own visual treatment:
 *   - text         → plain prose, markdown-ish (no real markdown parsing yet —
 *                    we just preserve whitespace)
 *   - thinking     → collapsed by default, italic when expanded
 *   - tool_use     → pill with the tool name + the input as collapsed JSON
 *   - tool_result  → the result, monospace, collapsed if long, error-coloured
 *                    when is_error=true
 *   - anything else → JSON dump (forward-compatible with new Anthropic block
 *                    types we haven't yet styled)
 */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ContentBlock, ToolResultBlock, ToolUseBlock } from "@/api/conversations";
import { cn } from "@/lib/utils";

export function MessageBlocks({ blocks }: { blocks: ContentBlock[] }) {
  return (
    <div className="space-y-2">
      {blocks.map((block, i) => (
        <BlockRenderer key={i} block={block} />
      ))}
    </div>
  );
}

function BlockRenderer({ block }: { block: ContentBlock }) {
  switch (block.type) {
    case "text":
      return <TextBlockView text={String((block as { text?: string }).text ?? "")} />;
    case "thinking":
      return <ThinkingBlockView text={String((block as { thinking?: string }).thinking ?? "")} />;
    case "tool_use":
      return <ToolUseBlockView block={block as unknown as ToolUseBlock} />;
    case "tool_result":
      return <ToolResultBlockView block={block as unknown as ToolResultBlock} />;
    default:
      return <UnknownBlockView block={block} />;
  }
}

function TextBlockView({ text }: { text: string }) {
  if (!text.trim()) return null;
  // The agent tends to produce markdown (lists, code fences, tables). Render
  // it through react-markdown + remark-gfm. The `prose` classes (Tailwind
  // typography plugin) handle the per-element styling; `prose-pre:` overrides
  // tighten up code blocks for our compact chat surface.
  return (
    <div
      className={cn(
        // min-w-0 + break-words keep long unbroken strings (URLs, ids, big
        // numbers) from pushing the bubble wide in the narrow side panel;
        // they wrap instead.
        "prose prose-sm min-w-0 max-w-none break-words text-zinc-800",
        // tighten Tailwind typography defaults for an in-bubble chat layout
        "prose-p:my-2 prose-p:leading-relaxed",
        "prose-headings:mt-4 prose-headings:mb-2 prose-headings:font-semibold",
        "prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5",
        // Code fences don't wrap — scroll them horizontally within the bubble.
        "prose-pre:my-2 prose-pre:overflow-x-auto prose-pre:bg-zinc-900 prose-pre:text-zinc-100 prose-pre:text-[12px] prose-pre:p-3 prose-pre:rounded",
        "prose-code:before:content-none prose-code:after:content-none",
        "prose-code:bg-zinc-100 prose-code:text-zinc-800 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[12px] prose-code:font-mono",
        "prose-pre:prose-code:bg-transparent prose-pre:prose-code:text-zinc-100 prose-pre:prose-code:p-0",
        "prose-table:my-2 prose-table:text-xs",
        "prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline",
        "prose-blockquote:not-italic prose-blockquote:border-zinc-300 prose-blockquote:text-zinc-600",
      )}
      data-testid="markdown-content"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // Open external links in a new tab; safe-by-default — react-markdown
        // strips <script>/dangerous URLs unless we explicitly opt in.
        components={{
          a: (props) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          // Wide tables get their own horizontal scroller so they don't blow
          // out the bubble width.
          table: ({ node: _node, ...props }) => (
            <div className="overflow-x-auto">
              <table {...props} />
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function ThinkingBlockView({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text.trim()) return null;
  return (
    <details
      className="rounded border border-zinc-200 bg-zinc-50 text-xs"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer select-none px-3 py-1.5 font-medium uppercase tracking-wide text-zinc-500">
        Thinking
      </summary>
      <p className="whitespace-pre-wrap break-words px-3 pb-2 italic text-zinc-600">
        {text}
      </p>
    </details>
  );
}

function ToolUseBlockView({ block }: { block: ToolUseBlock }) {
  const [open, setOpen] = useState(false);
  const inputJson = JSON.stringify(block.input ?? {}, null, 2);
  const isEmpty = inputJson === "{}";
  return (
    <details
      className="rounded border border-blue-200 bg-blue-50 text-xs"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      data-testid={`tool-use-${block.name}`}
    >
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-1.5">
        <span className="rounded bg-blue-200 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-blue-900">
          tool
        </span>
        <code className="font-mono text-blue-900">{block.name}</code>
        {!isEmpty && (
          <span className="ml-auto text-[10px] text-blue-700">
            {open ? "hide input" : "show input"}
          </span>
        )}
      </summary>
      {!isEmpty && (
        <pre className="overflow-x-auto border-t border-blue-200 px-3 py-2 font-mono text-[11px] text-blue-900">
          {inputJson}
        </pre>
      )}
    </details>
  );
}

function ToolResultBlockView({ block }: { block: ToolResultBlock }) {
  const text = renderToolResultContent(block.content);
  const [open, setOpen] = useState(text.length < 800);
  const isError = Boolean(block.is_error);
  return (
    <details
      className={cn(
        "rounded border text-xs",
        isError
          ? "border-red-200 bg-red-50"
          : "border-emerald-200 bg-emerald-50",
      )}
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      data-testid={isError ? "tool-result-error" : "tool-result-ok"}
    >
      <summary
        className={cn(
          "flex cursor-pointer select-none items-center gap-2 px-3 py-1.5",
        )}
      >
        <span
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
            isError ? "bg-red-200 text-red-900" : "bg-emerald-200 text-emerald-900",
          )}
        >
          {isError ? "error" : "result"}
        </span>
        <span
          className={cn(
            "truncate text-[11px]",
            isError ? "text-red-900" : "text-emerald-900",
          )}
        >
          {firstLine(text, 80)}
        </span>
        <span
          className={cn(
            "ml-auto whitespace-nowrap text-[10px]",
            isError ? "text-red-700" : "text-emerald-700",
          )}
        >
          {open ? "hide" : "show"}
        </span>
      </summary>
      <pre
        className={cn(
          "overflow-x-auto border-t px-3 py-2 font-mono text-[11px]",
          isError
            ? "border-red-200 text-red-900"
            : "border-emerald-200 text-emerald-900",
        )}
      >
        {text}
      </pre>
    </details>
  );
}

function UnknownBlockView({ block }: { block: ContentBlock }) {
  return (
    <details className="rounded border border-zinc-200 bg-zinc-50 text-xs">
      <summary className="cursor-pointer select-none px-3 py-1.5 font-mono text-zinc-600">
        {block.type}
      </summary>
      <pre className="overflow-x-auto px-3 py-2 font-mono text-[11px] text-zinc-700">
        {JSON.stringify(block, null, 2)}
      </pre>
    </details>
  );
}

function renderToolResultContent(content: ToolResultBlock["content"]): string {
  if (typeof content === "string") return content;
  // Some tool results are themselves arrays of content blocks. Concatenate
  // text blocks; for anything else, JSON-dump.
  const parts: string[] = [];
  for (const block of content) {
    const maybeText = (block as { text?: unknown }).text;
    if (block.type === "text" && typeof maybeText === "string") {
      parts.push(maybeText);
    } else {
      parts.push(JSON.stringify(block, null, 2));
    }
  }
  return parts.join("\n");
}

function firstLine(text: string, max: number): string {
  const line = text.split("\n", 1)[0] ?? "";
  return line.length > max ? line.slice(0, max - 1) + "…" : line;
}
