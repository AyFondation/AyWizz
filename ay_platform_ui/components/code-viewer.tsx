// =============================================================================
// File: code-viewer.tsx
// Version: 1
// Path: ay_platform_ui/components/code-viewer.tsx
// Description: Read-only source-code viewer for the project artifacts
//              page. v1 ships a basic `<pre>` rendering — the swap to
//              Monaco (`@monaco-editor/react`) is one component
//              substitution away once the dep is installed (the user
//              owns `npm install` outside of Claude's allowlist).
//
//              The wrapper exists so the artifacts page imports a
//              single, stable interface ; Monaco upgrade is mechanical
//              and won't touch the consumer.
// =============================================================================

"use client";

interface Props {
  text: string;
  path: string;
}

/** Map a file path's extension to a Monaco language id (for the
 *  forthcoming upgrade) AND drive a tiny set of badge classes for
 *  the v1 plain renderer. Add an entry as new languages show up in
 *  generated artifacts. */
function languageForPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "py":
      return "python";
    case "js":
    case "mjs":
    case "cjs":
      return "javascript";
    case "ts":
    case "tsx":
      return "typescript";
    case "md":
    case "markdown":
      return "markdown";
    case "json":
      return "json";
    case "yml":
    case "yaml":
      return "yaml";
    case "toml":
      return "toml";
    case "html":
    case "htm":
      return "html";
    case "css":
      return "css";
    case "sh":
    case "bash":
      return "shell";
    case "txt":
    case "log":
      return "plaintext";
    default:
      return "plaintext";
  }
}

export function CodeViewer({ text, path }: Props) {
  const language = languageForPath(path);
  // TODO Pass 1.5 — replace this <pre> with @monaco-editor/react :
  //   const Monaco = dynamic(() => import("@monaco-editor/react"), { ssr: false });
  //   <Monaco height="60vh" language={language} value={text} options={{ readOnly: true }} />
  // Plain renderer for now : keeps line numbers + monospace, no syntax highlighting.
  const lines = text.split("\n");
  const lineNumberWidth = Math.max(2, String(lines.length).length);
  return (
    <div
      className="max-h-[70vh] overflow-auto bg-neutral-50 text-xs"
      data-testid="code-viewer"
      data-language={language}
    >
      <table className="w-full border-collapse font-mono">
        <tbody>
          {lines.map((line, idx) => (
            <tr
              // biome-ignore lint/suspicious/noArrayIndexKey: stable line index
              key={idx}
              className="align-top"
            >
              <td
                className="select-none whitespace-nowrap border-r border-neutral-200 px-2 py-0.5 text-right text-neutral-400"
                style={{ width: `${lineNumberWidth + 2}ch` }}
              >
                {idx + 1}
              </td>
              <td className="whitespace-pre px-3 py-0.5 text-neutral-900">{line || " "}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
