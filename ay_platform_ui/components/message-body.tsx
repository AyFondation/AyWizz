// =============================================================================
// File: message-body.tsx
// Version: 1
// Path: ay_platform_ui/components/message-body.tsx
// Description: Markdown renderer for chat message bodies. Resolves Q-500-003
//              (500-SPEC-UI-UX §7), which deferred "rich Markdown-to-HTML
//              rendering" out of v1 and left the library choice open.
//
//              WHY react-markdown AND NOT `marked`. The content rendered here
//              is LLM output, and LLM output is influenceable by whatever was
//              ingested into the RAG corpus — it is untrusted by construction.
//              `marked` emits an HTML STRING, which can only be mounted via
//              `dangerouslySetInnerHTML`; that opens an XSS path that then has
//              to be closed by never forgetting to sanitise. react-markdown
//              emits REACT ELEMENTS and never HTML, so raw HTML embedded in the
//              markdown is escaped and displayed as text. The hole is closed by
//              construction rather than by vigilance.
//
//              `rehype-raw` would re-open exactly that hole and SHALL NOT be
//              added here.
//
//              A Server Component / MDX path — the other option named in
//              Q-500-003 — was rejected: the assistant bubble is filled by a
//              client-side SSE stream, token by token, and a server render
//              cannot follow an in-flight stream. It would only ever cover
//              persisted messages, not the live row.
//
//              STREAMING. Partial markdown is expected on every in-flight
//              turn (an unclosed `**`, a half-written table). react-markdown
//              renders what currently parses and reflows as the rest arrives;
//              no guard is needed.
// =============================================================================

"use client";

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Constrained component map. Two jobs: keep block spacing tight enough for a
// chat bubble (the prose defaults are page-sized), and harden links.
const COMPONENTS = {
  // Links always leave the app. `noopener noreferrer` is mandatory on
  // `target="_blank"`: without `noopener` the opened page gets a live
  // `window.opener` handle back into this origin (reverse tabnabbing).
  a: (props: ComponentPropsWithoutRef<"a">) => (
    <a
      {...props}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-700 underline underline-offset-2 hover:text-blue-900"
    />
  ),
  p: (props: ComponentPropsWithoutRef<"p">) => (
    <p {...props} className="my-2 first:mt-0 last:mb-0" />
  ),
  ul: (props: ComponentPropsWithoutRef<"ul">) => (
    <ul {...props} className="my-2 list-disc pl-5 first:mt-0 last:mb-0" />
  ),
  ol: (props: ComponentPropsWithoutRef<"ol">) => (
    <ol {...props} className="my-2 list-decimal pl-5 first:mt-0 last:mb-0" />
  ),
  li: (props: ComponentPropsWithoutRef<"li">) => <li {...props} className="my-0.5" />,
  // Headings are scaled well down: a chat bubble is not a document, and an
  // untamed `#` would otherwise dwarf the conversation around it.
  h1: (props: ComponentPropsWithoutRef<"h1">) => (
    <h1 {...props} className="mt-3 mb-1.5 font-semibold text-base first:mt-0" />
  ),
  h2: (props: ComponentPropsWithoutRef<"h2">) => (
    <h2 {...props} className="mt-3 mb-1.5 font-semibold text-base first:mt-0" />
  ),
  h3: (props: ComponentPropsWithoutRef<"h3">) => (
    <h3 {...props} className="mt-3 mb-1.5 font-semibold text-sm first:mt-0" />
  ),
  h4: (props: ComponentPropsWithoutRef<"h4">) => (
    <h4 {...props} className="mt-3 mb-1.5 font-semibold text-sm first:mt-0" />
  ),
  h5: (props: ComponentPropsWithoutRef<"h5">) => (
    <h5 {...props} className="mt-3 mb-1.5 font-semibold text-sm first:mt-0" />
  ),
  h6: (props: ComponentPropsWithoutRef<"h6">) => (
    <h6 {...props} className="mt-3 mb-1.5 font-semibold text-sm first:mt-0" />
  ),
  code: (props: ComponentPropsWithoutRef<"code">) => (
    <code
      {...props}
      className="rounded bg-neutral-100 px-1 py-0.5 font-mono text-[0.85em] text-neutral-800"
    />
  ),
  // The bubble itself must never scroll horizontally, so a long code line
  // scrolls inside its own block.
  pre: (props: ComponentPropsWithoutRef<"pre">) => (
    <pre
      {...props}
      className="my-2 overflow-x-auto rounded border border-neutral-200 bg-neutral-50 p-2 first:mt-0 last:mb-0 [&>code]:bg-transparent [&>code]:p-0"
    />
  ),
  blockquote: (props: ComponentPropsWithoutRef<"blockquote">) => (
    <blockquote
      {...props}
      className="my-2 border-neutral-300 border-l-2 pl-3 text-neutral-600 first:mt-0 last:mb-0"
    />
  ),
  hr: (props: ComponentPropsWithoutRef<"hr">) => (
    <hr {...props} className="my-3 border-neutral-200" />
  ),
  // GFM tables (remark-gfm). Wrapped so a wide table scrolls itself instead
  // of widening the bubble.
  table: (props: ComponentPropsWithoutRef<"table">) => (
    <div className="my-2 overflow-x-auto first:mt-0 last:mb-0">
      <table {...props} className="w-full border-collapse text-left" />
    </div>
  ),
  th: (props: ComponentPropsWithoutRef<"th">) => (
    <th {...props} className="border border-neutral-200 bg-neutral-50 px-2 py-1 font-semibold" />
  ),
  td: (props: ComponentPropsWithoutRef<"td">) => (
    <td {...props} className="border border-neutral-200 px-2 py-1" />
  ),
};

/**
 * Render one chat message body as markdown.
 *
 * Used for BOTH roles: an assistant reply and a user message go through the
 * same renderer, so a table or a code block pasted into the composer reads
 * the same way it will when quoted back.
 */
export function MessageBody({ content }: { content: string }) {
  return (
    <div className="break-words" data-testid="message-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
