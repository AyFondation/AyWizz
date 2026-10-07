// =============================================================================
// File: workbenchTypes.ts
// Version: 3
// Path: ay_platform_ui/lib/workbenchTypes.ts
// Description: TypeScript contracts for the traceability workbench —
//              500-SPEC R-500-015..021, mirroring the C5 Pydantic models of
//              310-SPEC §4.1 / §4.7 / §4.8 / §4.9.
//
//              v3 (2026-10-07) : five fields whose type was an INLINE
//              object literal are promoted to named interfaces mirroring
//              the C5 models they shadow — `BatchView`,
//              `RatificationView`, `StepEstimateView`, `ImpactSetView`,
//              `ImpactPathView` — plus `BatchKind`, which was `string`.
//              An inline literal has no name to compare against, so
//              `audit_ui_api_chain.py` skipped its members; naming them
//              brought the audit from 115 to 117 of 129 call sites, with
//              nothing left silently uncompared. They turned out to be
//              correct, which is the answer one wants from a check and
//              could not have had from a reading.
//
//              v2 (2026-10-07) : `AllocationCoverageView` is re-aligned
//              with C5 `AllocationCoverage`. It had declared `is_covered`
//              and `links` since it was written, and C5 served NEITHER —
//              `is_covered` was a Python `@property` (computed, never
//              serialised) and the links were discarded in `_classify`
//              after being reduced to id tuples. The workbench therefore
//              threw on `allocation.links.some(…)` and showed "not yet
//              answered" for everything. C5 now serves both (`links`
//              additively, `is_covered` as a `computed_field`), which is
//              what `R-500-017` and `R-500-019` require: the latter says
//              auto-accepted requirements SHALL be counted "from links
//              actually marked auto-accepted, never inferred from a
//              difference between totals", and no id tuple carries a
//              state. Surfaced by `ay_platform_core/scripts/checks/
//              audit_ui_api_chain.py`.
//
//              A SEPARATE MODULE, not an extension of `types.ts`. That file
//              is 1385 lines covering six components; adding a seventh
//              surface to it makes both harder to read, and the workbench
//              types are consumed by exactly one page tree. Flagged as an
//              adjacent choice per CLAUDE.md §1.3 rather than made silently.
//
//              THE REVIEW STATES ARE A UNION, NOT A STRING. `R-310-006`
//              fixes five, and `R-310-007` requires `auto-accepted` to stay
//              distinguishable from `accepted` everywhere a figure is shown
//              (R-500-019). A bare `string` would let a component treat
//              them as interchangeable, which is the exact failure the
//              requirement forbids.
//
//              `speculative` is DELIBERATELY ABSENT from that union:
//              `R-310-177` v2 makes it a provenance marking orthogonal to
//              the review state, so an object can be `accepted` AND
//              speculative. Folding it in would make that unrepresentable
//              here as surely as it would in the backend enum.
//
// @relation implements:R-500-015
// @relation implements:R-500-017
// @relation implements:R-500-019
// @relation implements:R-500-021
// =============================================================================

/** The five review states of `R-310-006`. Closed on purpose. */
export type ReviewState = "proposed" | "accepted" | "auto-accepted" | "stale" | "rejected";

/** Review states that mean a human (or a ratified cluster review) has
 *  signed off. `auto-accepted` counts as acceptance but is counted
 *  SEPARATELY wherever a figure is displayed (`R-500-019`). */
export const ACCEPTED_STATES: ReadonlySet<ReviewState> = new Set<ReviewState>([
  "accepted",
  "auto-accepted",
]);

/** True when the state means nobody has examined this yet. Used to decide
 *  whether an object renders as a proposal (`R-500-021`). */
export function isProposal(state: ReviewState): boolean {
  return state === "proposed";
}

export type ObjectType =
  | "heading"
  | "subheading"
  | "paragraph"
  | "figure"
  | "table"
  | "requirement";

/** One object of a container, as `GET /containers/{c}/objects` returns it.
 *
 *  `body` and `notation` are MUTUALLY EXCLUSIVE and both nullable, mirroring
 *  `DocObjectPublic` exactly: `R-310-008` makes prose and figures exclusive,
 *  so a paragraph carries `body` and a figure carries `notation`. An earlier
 *  draft of this interface declared a single `content: string`, which the
 *  backend never sends — the contract test pins PATHS, not payload shapes,
 *  so nothing but reading `DocObjectPublic` would have caught it. */
export interface DocObjectSummary {
  object_id: string;
  container: string;
  type: ObjectType;
  ordinal: number;
  version: number;
  review_state: ReviewState;
  body?: string | null;
  notation?: string | null;
  produced_by?: ProducedByView | null;
}

/** Which workflow produced an object, when an agent did (`R-310-011`). */
export interface ProducedByView {
  workflow: string;
  workflow_version: number;
}

/** The text to render for an object, whichever field carries it. */
export function objectText(obj: DocObjectSummary): string {
  return obj.notation ?? obj.body ?? "";
}

/** True when the object is a figure rather than prose (`R-310-008`). */
export function isFigure(obj: DocObjectSummary): boolean {
  return obj.notation != null;
}

export interface DocObjectList {
  objects: DocObjectSummary[];
}

/** A coverage link as the object panel expands it in place (`R-500-017`).
 *  Mirrors C5 `CoverageLink` (`extra="forbid"`). */
export interface CoverageLinkView {
  object_id: string;
  project_id: string;
  container: string;
  target_id: string;
  pinned_version: number;
  strength: "covered" | "weak";
  state: ReviewState;
  actor: string;
  at: string;
}

/** What one allocation of a requirement has delivered. Mirrors C5
 *  `AllocationCoverage`; `is_covered` is a `computed_field` there, so it
 *  is served rather than re-derived here. `links` and `is_covered` were
 *  declared on this side long before C5 served either — the workbench
 *  threw on `allocation.links.some(…)` and rendered "not yet answered"
 *  unconditionally. Both are now on the wire (2026-10-07). */
export interface AllocationCoverageView {
  container: string;
  allocation_state: ReviewState;
  covering_objects: string[];
  weak_objects: string[];
  stale_objects: string[];
  is_covered: boolean;
  links: CoverageLinkView[];
}

/** The aggregated coverage of one requirement. */
export interface RequirementCoverageView {
  requirement_id: string;
  allocations: AllocationCoverageView[];
  out_of_project: boolean;
  fragments: RequirementCoverageView[];
}

/** What a container owes and has delivered (`R-310-120`, `R-500-018`). */
export interface ContainerCoverageView {
  container: string;
  allocated: string[];
  uncovered: string[];
  weak: string[];
  stale: string[];
}

/** A coverage link whose target has moved past its pin (`R-310-145`). */
export interface SuspectLinkView extends CoverageLinkView {
  current_version: number;
}

export interface SuspectLinkList {
  links: SuspectLinkView[];
}

/** An object built on an upstream nobody has accepted (`R-310-177` v2).
 *  Derived server-side on every read, never a stored flag. */
export interface SpeculativeMarkingView {
  object_id: string;
  container: string;
  unaccepted_targets: string[];
  advanced_targets: string[];
}

export interface SpeculativeList {
  markings: SpeculativeMarkingView[];
  count: number;
  stale_count: number;
}

/** A container declared by the resolved cycle (`R-310-021`). */
export interface ContainerSpecView {
  slug: string;
  ordinal: number;
  scope_id: string;
  scope: string;
  coverage_obligatory: boolean;
}

export interface ResolvedCycleView {
  cycle_id: string;
  version: number;
  title: string;
  containers: ContainerSpecView[];
}

/** A change ticket, as the review region lists it (`R-310-149`): note the
 *  absence of a status field — it is derived from `closed_at`. */
export interface ChangeTicketView {
  ticket_id: string;
  project_id: string;
  drop_id: string;
  requirement_id: string;
  kind: "added" | "modified" | "removed";
  previous_text: string | null;
  current_text: string | null;
  opened_at: string;
  closed_at: string | null;
  closed_by: string | null;
  impact: ImpactSetView;
  dispositions: DispositionView[];
}

/** Mirror of C5 `ImpactSet` — everything one change reaches. The seed is
 *  deliberately not a member of `nodes`. */
export interface ImpactSetView {
  seed_id: string;
  nodes: ImpactNodeView[];
}

/** Mirror of C5 `ImpactPath` — one route from the change to an impacted
 *  node, seed first. */
export interface ImpactPathView {
  nodes: string[];
}

export interface ImpactNodeView {
  node_id: string;
  container: string;
  paths: ImpactPathView[];
  paths_truncated: boolean;
}

export interface DispositionView {
  node_id: string;
  kind: "modified-and-accepted" | "confirmed-unchanged";
  actor: string;
  at: string;
  justification: string;
  object_version: number | null;
}

export interface ChangeTicketList {
  tickets: ChangeTicketView[];
  count: number;
}

/** A treatment plan awaiting ratification or running (`R-310-170`). */
export interface TreatmentPlanView {
  plan_id: string;
  project_id: string;
  version: number;
  batch: BatchView;
  steps: PlanStepView[];
  proposed_by: string;
  proposed_at: string;
  ratification: RatificationView | null;
}

/** C5 `BatchKind` — what a plan's batch is made of. Was `string`, which
 *  meant the audit could not compare the members. */
export type BatchKind = "change_set" | "supplied_requirements" | "container_authoring";

/** Mirror of C5 `Batch` — what one plan concerns (`R-310-171`). */
export interface BatchView {
  kind: BatchKind;
  source: string;
  count: number;
}

/** Mirror of C5 `Ratification` — the decision that execution may begin
 *  (`R-310-175`), carrying the plan version it approved. */
export interface RatificationView {
  plan_version: number;
  actor: string;
  at: string;
}

export interface PlanStepView {
  step_id: string;
  scope: string[];
  effort: "batchable" | "arbitration-required" | "reflection-required";
  mode: "end-to-end" | "step-by-step";
  estimate: StepEstimateView;
  state: "pending" | "running" | "suspended" | "completed" | "failed";
  suspended_reason: string | null;
}

/** Mirror of C5 `StepEstimate` — the four figures a step declares before
 *  it may be ratified. `review_items` has no default on the Python side:
 *  reviewer capacity binds before budget does, so a zero would read as
 *  "generates no review work", the one claim a plan must never make
 *  silently. */
export interface StepEstimateView {
  tokens: number;
  cost_eur: number;
  duration_min: number;
  review_items: number;
}

export interface TreatmentPlanList {
  plans: TreatmentPlanView[];
  count: number;
}

// ---------------------------------------------------------------------------
// Coverage figures — R-500-019
// ---------------------------------------------------------------------------

/**
 * A coverage figure and the share of it granted without individual
 * examination.
 *
 * Both fields are REQUIRED. `R-500-019` forbids presenting a merged total
 * without its auto-accepted breakdown, and an optional field is a field
 * callers omit: the fourth component to render a figure is the one that
 * forgets. Making the share non-optional moves the requirement from a
 * convention a reviewer must check into a type error.
 */
export interface CoverageFigure {
  /** Objects or requirements counted as covered, auto-accepted included. */
  total: number;
  /** How many of `total` came from an `auto-accepted` link (`R-310-007`). */
  autoAccepted: number;
  /** The denominator the figure is out of, when there is one. */
  outOf?: number;
}

/** The share of a figure granted without individual examination, 0..1.
 *  Returns 0 for an empty figure rather than dividing. */
export function autoAcceptedShare(figure: CoverageFigure): number {
  if (figure.total <= 0) return 0;
  return figure.autoAccepted / figure.total;
}

/** True when any part of the figure was granted by cluster review. A
 *  reviewer reading a green number needs to know this at a glance, not
 *  after arithmetic. */
export function hasAutoAccepted(figure: CoverageFigure): boolean {
  return figure.autoAccepted > 0;
}

/** Build the coverage figure for one container from its coverage view.
 *
 * `autoAccepted` is counted from the links actually marked
 * `auto-accepted`, never inferred from a difference between totals: a
 * difference silently absorbs any other reason a count might not match.
 */
export function containerFigure(
  view: ContainerCoverageView,
  autoAcceptedIds: ReadonlySet<string>,
): CoverageFigure {
  const covered = view.allocated.filter((id) => !view.uncovered.includes(id));
  return {
    total: covered.length,
    autoAccepted: covered.filter((id) => autoAcceptedIds.has(id)).length,
    outOf: view.allocated.length,
  };
}

// ---------------------------------------------------------------------------
// Findings — R-500-018
// ---------------------------------------------------------------------------

export type FindingKind =
  | "coverage-gap"
  | "suspect-link"
  | "weak-coverage"
  | "speculative"
  | "uncovered-allocation";

/**
 * One item of the permanently mounted findings region.
 *
 * `criterion` and `location` are both required because `R-310-223`
 * requires each finding to cite them: a finding a reviewer cannot locate
 * is a notification, not a finding.
 */
export interface Finding {
  kind: FindingKind;
  criterion: string;
  location: string;
  detail: string;
  /** The object or requirement the finding is about, so selecting the
   *  finding selects the subject (`R-500-016`). */
  subject: string;
  container?: string;
}

/** Human-readable label per finding kind, and the criterion identifier
 *  each one cites. Co-located so a new kind cannot be added without
 *  deciding both. */
export const FINDING_META: Record<FindingKind, { label: string; criterion: string }> = {
  "coverage-gap": { label: "Coverage gap", criterion: "CRIT-COV-001" },
  "suspect-link": { label: "Suspect link", criterion: "CRIT-COV-002" },
  "weak-coverage": { label: "Weak coverage", criterion: "CRIT-COV-003" },
  speculative: { label: "Speculative", criterion: "CRIT-COV-004" },
  "uncovered-allocation": { label: "Allocated, unanswered", criterion: "CRIT-COV-005" },
};

// ---------------------------------------------------------------------------
// Baselines — 310-SPEC §4.11
// ---------------------------------------------------------------------------

export type BaselineBlocker =
  | "open-change-ticket"
  | "critical-coverage-gap"
  | "stale-coverage-link";

export interface BaselineRefusalView {
  blocker: BaselineBlocker;
  subject: string;
  detail: string;
}

/** The R-310-201 gate's verdict. `refusals` carries EVERY outstanding
 *  precondition, so the panel can list them all rather than the first. */
export interface BaselineReadinessView {
  project_id: string;
  refusals: BaselineRefusalView[];
}

/** A baseline as the panel lists it. Note the absence of entries: the
 *  listing is a chooser, and a manifest of 30 000 objects has no business
 *  in it (R-310-300). */
export interface BaselineSummaryView {
  tag: string;
  project_id: string;
  cycle_id: string;
  cycle_version: number;
  created_by: string;
  created_at: string;
  object_count: number;
  link_count: number;
  note: string;
}

export interface BaselineList {
  baselines: BaselineSummaryView[];
  count: number;
}

/** One object version named by a baseline. Carries a content HASH and no
 *  content: `R-310-200` forbids duplicating it, because a second copy is a
 *  second answer to "what did this baseline contain?". */
export interface ManifestObjectView {
  object_id: string;
  container: string;
  version: number;
  ordinal: number;
  review_state: ReviewState;
  content_hash: string;
}

/** One coverage pin frozen into a baseline. */
export interface ManifestLinkView {
  object_id: string;
  container: string;
  target_id: string;
  pinned_version: number;
  strength: "covered" | "weak";
  state: ReviewState;
}

/** A named, immutable photograph of the corpus (`R-310-200`). */
export interface BaselineManifestView {
  tag: string;
  project_id: string;
  cycle_id: string;
  cycle_version: number;
  created_by: string;
  created_at: string;
  objects: ManifestObjectView[];
  links: ManifestLinkView[];
  note: string;
}

export type RenderFormatName = "docx" | "pdf";
