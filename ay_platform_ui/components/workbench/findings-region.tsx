// =============================================================================
// File: findings-region.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/findings-region.tsx
// Description: The permanently mounted findings region — 500-SPEC R-500-018,
//              realising 310-SPEC R-310-223 / R-310-224.
//
//              "PERMANENTLY MOUNTED", NOT "AVAILABLE". A findings panel
//              behind a tab is a panel nobody opens, and the gap list is the
//              one thing a reviewer must not be able to work without seeing.
//              So this region has no collapsed state and no tab: it renders
//              in the layout unconditionally, and its empty state is an
//              explicit "nothing outstanding" rather than an absence.
//
//              EVERY FINDING CITES ITS CRITERION AND ITS LOCATION, because
//              R-310-223 says so and because a finding a reviewer cannot
//              locate is a notification. `Finding` makes both fields
//              required, so an un-locatable finding does not typecheck.
//
//              CLICKING A FINDING SELECTS ITS SUBJECT. The whole point of
//              the shared selection (`R-500-016`) is that acting on a
//              finding does not mean re-finding its subject by hand.
//
// @relation implements:R-310-223
// @relation implements:R-310-224
// @relation implements:R-500-018
// =============================================================================

"use client";

import {
  type ContainerCoverageView,
  FINDING_META,
  type Finding,
  type SpeculativeList,
  type SuspectLinkList,
} from "@/lib/workbenchTypes";
import { useSelection } from "./selection-context";

/**
 * Assemble the findings list from the backend views.
 *
 * Exported and pure so the derivation is testable without rendering: the
 * mapping from "what the backend reports" to "what a reviewer must act on"
 * is the substance of R-500-018, and asserting it through the DOM would
 * test React instead.
 */
export function buildFindings({
  coverage,
  suspect,
  speculative,
}: {
  coverage: ContainerCoverageView | null;
  suspect: SuspectLinkList | null;
  speculative: SpeculativeList | null;
}): Finding[] {
  const findings: Finding[] = [];

  if (coverage) {
    // R-310-224: while a container is open, what it owes and has not
    // delivered. Listed as its own kind rather than merged into
    // "coverage-gap" because the remedy differs — this one has an owner.
    for (const requirementId of coverage.uncovered) {
      findings.push({
        kind: "uncovered-allocation",
        criterion: FINDING_META["uncovered-allocation"].criterion,
        location: coverage.container,
        detail: `allocated to ${coverage.container} and not yet answered`,
        subject: requirementId,
      });
    }
    for (const requirementId of coverage.weak) {
      findings.push({
        kind: "weak-coverage",
        criterion: FINDING_META["weak-coverage"].criterion,
        location: coverage.container,
        detail: "cited without being answered",
        subject: requirementId,
      });
    }
    for (const requirementId of coverage.stale) {
      findings.push({
        kind: "coverage-gap",
        criterion: FINDING_META["coverage-gap"].criterion,
        location: coverage.container,
        detail: "its coverage link pins a superseded version",
        subject: requirementId,
      });
    }
  }

  if (suspect) {
    for (const link of suspect.links) {
      findings.push({
        kind: "suspect-link",
        criterion: FINDING_META["suspect-link"].criterion,
        location: `${link.container}/${link.object_id}`,
        detail: `pins v${link.pinned_version} of ${link.target_id}, now at v${link.current_version}`,
        subject: link.object_id,
        container: link.container,
      });
    }
  }

  if (speculative) {
    for (const marking of speculative.markings) {
      const advanced = marking.advanced_targets.length > 0;
      findings.push({
        kind: "speculative",
        criterion: FINDING_META.speculative.criterion,
        location: `${marking.container}/${marking.object_id}`,
        detail: advanced
          ? `built on ${marking.unaccepted_targets.join(", ")}, which changed before acceptance`
          : `built on ${marking.unaccepted_targets.join(", ")}, not yet accepted`,
        subject: marking.object_id,
        container: marking.container,
      });
    }
  }

  return findings;
}

const TONE: Record<Finding["kind"], string> = {
  "coverage-gap": "border-l-rose-500",
  "suspect-link": "border-l-amber-500",
  "weak-coverage": "border-l-amber-500",
  speculative: "border-l-sky-500",
  "uncovered-allocation": "border-l-rose-500",
};

export function FindingsRegion({
  coverage,
  suspect,
  speculative,
}: {
  coverage: ContainerCoverageView | null;
  suspect: SuspectLinkList | null;
  speculative: SpeculativeList | null;
}) {
  const { selectFinding, objectId, requirementId } = useSelection();
  const findings = buildFindings({ coverage, suspect, speculative });

  return (
    <section
      className="flex h-full flex-col overflow-hidden border-t border-neutral-200 bg-white"
      aria-label="Findings"
      data-testid="findings-region"
    >
      <header className="flex items-baseline justify-between border-b border-neutral-200 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-700">Findings</h2>
        <span className="text-xs tabular-nums text-neutral-500" data-testid="findings-count">
          {findings.length}
        </span>
      </header>

      {findings.length === 0 ? (
        /* An explicit statement, not an absence: a blank panel reads as
           "not loaded" and this one must read as "nothing outstanding". */
        <p className="px-3 py-4 text-xs text-neutral-500" data-testid="findings-empty">
          Nothing outstanding for the current selection.
        </p>
      ) : (
        <ul className="flex-1 divide-y divide-neutral-100 overflow-y-auto">
          {findings.map((finding) => {
            const selected = finding.subject === objectId || finding.subject === requirementId;
            return (
              <li key={`${finding.kind}:${finding.subject}:${finding.location}`}>
                <button
                  type="button"
                  onClick={() => selectFinding(finding.subject, finding.container)}
                  className={`w-full border-l-4 px-3 py-2 text-left hover:bg-neutral-50 ${
                    TONE[finding.kind]
                  } ${selected ? "bg-neutral-100" : ""}`}
                  data-testid={`finding-${finding.subject}`}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="text-xs font-medium text-neutral-900">
                      {FINDING_META[finding.kind].label}
                    </span>
                    <code className="text-[11px] text-neutral-500">{finding.criterion}</code>
                  </span>
                  <span className="mt-0.5 block text-xs text-neutral-700">
                    <strong className="font-medium">{finding.subject}</strong>
                    {" — "}
                    {finding.detail}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-neutral-500">
                    {finding.location}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
