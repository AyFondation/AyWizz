// =============================================================================
// File: coverage-figure.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/coverage-figure.tsx
// Description: The ONLY component that renders a coverage number —
//              500-SPEC R-500-019, realising 310-SPEC R-310-225 / R-310-007.
//
//              WHY ONE COMPONENT. "Every coverage figure displays its
//              auto-accepted share alongside it" is easy to honour in the
//              first place a number appears and forget in the fourth. A
//              convention a reviewer must check on every new panel is a
//              convention that decays. So every figure in the workbench goes
//              through here, and `CoverageFigure` makes `autoAccepted`
//              non-optional — a caller with only a total does not typecheck.
//
//              WHAT `auto-accepted` MEANS, and why it cannot be merged.
//              R-310-007: acceptance granted by CLUSTER review, without
//              individual examination. A figure that sums it with
//              individually-reviewed coverage reads as a review result and
//              is not one. The share is therefore rendered even when it is
//              zero: "0 of 12 auto-accepted" is information, and its absence
//              would leave the reader unable to tell a fully-reviewed figure
//              from a component that forgot to show the breakdown.
//
// @relation implements:R-310-225
// @relation implements:R-500-019
// =============================================================================

import {
  autoAcceptedShare,
  type CoverageFigure as Figure,
  hasAutoAccepted,
} from "@/lib/workbenchTypes";

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/**
 * Render a coverage count with its auto-accepted breakdown.
 *
 * `label` names what is being counted; the figure carries the numbers. The
 * breakdown is always rendered — see the header for why zero is shown.
 */
export function CoverageFigureView({
  label,
  figure,
  testId,
}: {
  label: string;
  figure: Figure;
  testId?: string;
}) {
  const share = autoAcceptedShare(figure);
  const flagged = hasAutoAccepted(figure);
  return (
    <div className="flex flex-col gap-0.5" data-testid={testId}>
      <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      <span className="font-semibold text-neutral-900 tabular-nums">
        {figure.total}
        {figure.outOf !== undefined ? (
          <span className="font-normal text-neutral-500">
            {" / "}
            {figure.outOf}
          </span>
        ) : null}
      </span>
      {/* R-500-019: rendered unconditionally, including at zero. */}
      <span
        className={`text-xs tabular-nums ${flagged ? "text-amber-700" : "text-neutral-500"}`}
        data-testid={testId ? `${testId}-auto` : undefined}
      >
        {figure.autoAccepted} of {figure.total} auto-accepted
        {flagged ? ` (${pct(share)})` : ""}
      </span>
      {flagged ? (
        <span className="text-[11px] text-amber-700">
          Auto-accepted means granted by cluster review, without individual examination.
        </span>
      ) : null}
    </div>
  );
}

/**
 * A horizontal bar for the same figure, for use in a matrix row.
 *
 * The auto-accepted portion is drawn as a distinct band rather than as part
 * of the covered bar: a single bar would present the merged total
 * `R-500-019` prohibits, in the form a reader trusts most.
 */
export function CoverageFigureBar({ figure, testId }: { figure: Figure; testId?: string }) {
  const denominator = figure.outOf ?? figure.total;
  const safe = denominator > 0 ? denominator : 1;
  const reviewed = figure.total - figure.autoAccepted;
  const reviewedPct = (reviewed / safe) * 100;
  const autoPct = (figure.autoAccepted / safe) * 100;
  return (
    <div className="flex items-center gap-2" data-testid={testId}>
      <div
        className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200"
        role="img"
        aria-label={`${reviewed} reviewed, ${figure.autoAccepted} auto-accepted, out of ${denominator}`}
      >
        <div className="flex h-full">
          <div
            className="h-full bg-emerald-500"
            style={{ width: `${reviewedPct}%` }}
            data-testid={testId ? `${testId}-reviewed` : undefined}
          />
          <div
            className="h-full bg-amber-400"
            style={{ width: `${autoPct}%` }}
            data-testid={testId ? `${testId}-auto` : undefined}
          />
        </div>
      </div>
      <span className="text-xs tabular-nums text-neutral-600">
        {reviewed}
        {figure.autoAccepted > 0 ? `+${figure.autoAccepted}` : ""}
        {" / "}
        {denominator}
      </span>
    </div>
  );
}
