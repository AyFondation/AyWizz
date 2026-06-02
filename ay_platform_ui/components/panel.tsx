// =============================================================================
// File: panel.tsx
// Version: 1
// Path: ay_platform_ui/components/panel.tsx
// Description: Shared presentational primitives for page visual structure.
//              Establishes a clear contrast ladder so page zones read at a
//              glance: a tinted full-bleed `PageShell` background, white
//              `Panel` cards with a defined border + soft shadow that pop
//              against it, and a `PanelHeader` carrying an accent left-bar
//              + a readable title. Introduced for the Sources flow; meant to
//              be reused when the visual refresh rolls out app-wide.
// =============================================================================

import type { ReactNode } from "react";

/** Tinted, full-bleed page background. Wrap a page's `<main>` so the muted
 *  backdrop stretches behind the centered content column and white panels
 *  stand out against it. */
export function PageShell({ children }: { children: ReactNode }) {
  return <div className="min-h-screen bg-neutral-50">{children}</div>;
}

/** A content card: white surface, defined border + soft shadow so each
 *  zone is visually separated from the tinted page background. Renders no
 *  inner padding — pair with `PanelHeader` (flush) and `PanelBody`. */
export function Panel({
  children,
  className = "",
  testId,
}: {
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      className={`overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-sm ${className}`}
      data-testid={testId}
    >
      {children}
    </section>
  );
}

/** Panel header: an accent left-bar + a readable title, with optional
 *  right-aligned actions/status. The accent bar + tinted strip make every
 *  section title scannable. */
export function PanelHeader({ title, actions }: { title: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-neutral-200 bg-neutral-50 px-5 py-3">
      <h3 className="flex items-center gap-2.5 text-sm font-semibold tracking-tight text-neutral-800">
        <span className="h-4 w-1 shrink-0 rounded-full bg-indigo-600" aria-hidden="true" />
        {title}
      </h3>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Consistent body padding for a `Panel`. */
export function PanelBody({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`px-5 py-4 ${className}`}>{children}</div>;
}

/** A secondary heading inside a `PanelBody` (sub-zone label). Stronger than
 *  the previous faint uppercase labels: a short accent tick + readable text. */
export function SubHeading({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
        <span className="h-3 w-0.5 shrink-0 rounded-full bg-indigo-400" aria-hidden="true" />
        {children}
      </h4>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
