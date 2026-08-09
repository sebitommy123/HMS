/**
 * Monaco-backed code editor for flex modules.
 *
 * Defaults to Python with line numbers and a fixed height — the
 * surrounding page gives it whatever vertical space makes sense.
 * Controlled component: parent owns the value.
 */

import Editor from "@monaco-editor/react";

export function CodeEditor({
  value,
  onChange,
  height = "400px",
  readOnly = false,
  language = "python",
  testId,
}: {
  value: string;
  onChange?: (next: string) => void;
  height?: string | number;
  readOnly?: boolean;
  language?: string;
  testId?: string;
}) {
  return (
    <div
      className="overflow-hidden rounded border border-zinc-300"
      data-testid={testId}
    >
      <Editor
        height={height}
        language={language}
        value={value}
        onChange={(next) => onChange?.(next ?? "")}
        theme="vs-light"
        options={{
          // Read-only mode is for "view this module" surfaces in the UI.
          readOnly,
          // Fewer chrome elements so the editor doesn't dominate the page.
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          // Sensible Python defaults.
          tabSize: 4,
          insertSpaces: true,
          fontSize: 13,
          fontFamily:
            'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
          wordWrap: "off",
          renderLineHighlight: "line",
          automaticLayout: true,
        }}
      />
    </div>
  );
}
