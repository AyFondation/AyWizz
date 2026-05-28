// =============================================================================
// File: types.ts
// Version: 11
// Path: ay_platform_ui/lib/types.ts
// Description: Wire-format type definitions for the platform's public
//              bootstrap surface — `/runtime-config.json` (static, served
//              from this app's origin) and `/ux/config` (dynamic, served
//              by C2). snake_case fields match the Python wire format
//              verbatim so there's no mapping layer to keep in sync.
//
//              v11 (2026-05-19) : UNIFIED inline-event model.
//              `StageEvent` + `ToolCallEvent` collapse into one
//              `InlineEvent` (discriminated by `kind`) and
//              `Message.stages` becomes `Message.events` — mirrors
//              the C3 v10 single `event: inline` channel + persisted
//              `MessagePublic.events` audit ledger. Rendered through
//              one `<InlineLog>` formatter registry.
//
//              v7 : adds `user_color` to `UserPreferences*` and
//              `StageEvent` for the C3 SSE pipeline-progress
//              channel (named `stage` events emitted by `_rag_stream`).
//
//              v6 : `Project` carries the effective per-project
//              `system_prompt` + `system_prompt_is_default` flag.
//              New `UserPreferences*` types mirror C2's
//              `/api/v1/users/me/preferences`. The UX fetches user
//              prefs at login and the active project at navigation,
//              forwards both prompts on every chat message.
//
//              v5 (2026-05-11) : adds C3 (conversations + messages),
//              C5 (requirements documents + entities), C6
//              (validation runs + findings) wire types.
//
//              v4 (2026-05-11) : adds `Source`, `SourceList`,
//              `ParseStatus`, `SUPPORTED_MIME_TYPES` (C7 sources).
//
//              v3 (2026-05-11) : adds `Project` + `ProjectList` types
//              (wire shape of C2's `GET /api/v1/projects`).
//
//              v2 (2026-04-29) : adds `DevCredential` + optional
//              `dev_credentials` field on `UxConfig`. Populated only
//              when C2's `C2_UX_DEV_MODE_ENABLED=true` AND
//              `C2_AUTH_MODE=local` ; null/absent in production.
// =============================================================================

/** Static deployment-time config served from the UI's `/public/` dir.
 *  Mountable as a K8s ConfigMap so the API URL can change without
 *  rebuilding the bundle. */
export interface RuntimeConfig {
  /** Base URL of the platform's public Traefik gateway. Empty = relative
   *  URLs (same-origin: Traefik in prod, Next.js dev rewrites in dev). */
  apiBaseUrl: string;
  /** Public-facing URL of the UI itself, used for OAuth redirects /
   *  webhook callbacks once those land. Optional in v1. */
  publicBaseUrl: string;
}

/** Brand identity served by C2 `/ux/config`. Skinnable per deployment
 *  via `C2_UX_BRAND_*` env vars. */
export interface BrandConfig {
  name: string;
  short_name: string;
  accent_color_hex: string;
}

/** Capability toggles served by C2 `/ux/config`. UX checks these
 *  before showing the corresponding affordances. */
export interface FeatureFlags {
  chat_enabled: boolean;
  kg_enabled: boolean;
  cross_tenant_enabled: boolean;
  file_download_enabled: boolean;
}

/** A single demo-seed credential surfaced for auto-fill on the login
 *  page. Returned by `/ux/config` only when C2's
 *  `C2_UX_DEV_MODE_ENABLED=true` AND `C2_AUTH_MODE=local` ; otherwise
 *  the parent field is null/absent. The plaintext password is
 *  intentional (well-known dev accounts) — production deployments
 *  never set the flag, so this never reaches a prod browser. */
export interface DevCredential {
  username: string;
  password: string;
  role_label: string;
  note: string | null;
}

/** Response body of `GET /ux/config`. */
export interface UxConfig {
  api_version: string;
  /** API tier image build stamp — baked at docker build time and
   *  surfaced by C2 so the UX footer can show which image the API
   *  is running. Free-form string (ISO timestamp / git sha / semver).
   *  Optional in the type so a deployment running an older C2 that
   *  doesn't emit the field still parses without crashing. */
  build_version?: string;
  auth_mode: "none" | "local" | "sso";
  brand: BrandConfig;
  features: FeatureFlags;
  /** Demo credentials for auto-fill in dev mode. null/undefined in
   *  production. */
  dev_credentials?: DevCredential[] | null;
}

/** Combined config available to every Client Component via
 *  `useReadyConfig()`. */
export interface PlatformConfig {
  runtime: RuntimeConfig;
  ux: UxConfig;
}

/** A single project record served by C2's `GET /api/v1/projects` and
 *  `GET /api/v1/projects/{pid}`. `profile` selects the production-
 *  domain pipeline (currently only `code` ; resolved via
 *  lib/profiles/registry). `system_prompt` is the EFFECTIVE addendum
 *  prepended to chat messages on this project (override OR C2
 *  default) ; `system_prompt_is_default` flags whether an override
 *  has been stored — the settings page uses it to render a 'Using
 *  default' badge + 'Reset to default' button only when meaningful. */
export interface Project {
  project_id: string;
  tenant_id: string;
  name: string;
  profile: string;
  created_at: string;
  created_by: string;
  system_prompt: string;
  system_prompt_is_default: boolean;
  /** HTTPS clone URL of the project's Gitea repo (R-200-142).
   *  Null on legacy projects created before the Gitea pass landed. */
  git_repo_url?: string | null;
}

/** Response body of `GET /api/v1/projects`. */
export interface ProjectList {
  items: Project[];
}

/** Body of `PATCH /api/v1/projects/{pid}`. Per-field semantics :
 *  - omitted / `undefined`     → no change.
 *  - `null`                    → equivalent to omitted (server treats
 *                                missing & null identically).
 *  - empty string `""`         → clear the override (revert to C2
 *                                default).
 *  - non-empty string          → set the override. */
export interface ProjectUpdate {
  name?: string | null;
  system_prompt?: string | null;
}

// ===========================================================================
// C2 — Self-service user preferences
// ===========================================================================

/** Response of `GET /api/v1/users/me/preferences` — effective values
 *  the chat client should forward to C3, plus the `is_default` flag
 *  the preferences page uses to render a 'Reset to default' button.
 *
 *  `trigram` / `user_color` are the user's stored overrides or null
 *  when they rely on the UI defaults. `user_prompt` is ALWAYS
 *  populated — falling back to `C2_DEFAULT_USER_PROMPT` when no
 *  override is set. */
export interface UserPreferencesResponse {
  trigram: string | null;
  user_prompt: string;
  user_prompt_is_default: boolean;
  user_color: string | null;
}

/** Body of `PUT /api/v1/users/me/preferences`. Per-field semantics
 *  mirror `ProjectUpdate` — null / undefined leaves the value
 *  untouched ; empty string clears the override ; non-empty sets it. */
export interface UserPreferencesUpdate {
  trigram?: string | null;
  user_prompt?: string | null;
  user_color?: string | null;
}

// ===========================================================================
// C3 — Chat SSE pipeline progress events
// ===========================================================================

/** One `event: inline` payload — the UNIFIED inline-activity event
 *  (C3 v10, 2026-05-19). Every kind of in-turn activity travels this
 *  single channel, discriminated by `kind` :
 *    - `kind:"stage"`    pipeline progress (retrieve/generate/done) ;
 *                        carries `name`, `duration_ms`, `stats`.
 *    - `kind:"tool_call"` DocGen tool (D-015) ; carries `name` (tool),
 *                        `ok`, `round`, `summary`, `path`.
 *    - future kinds      add a formatter in `<InlineLog>`, nothing
 *                        else changes.
 *  `running` events stream live ; the matching `done` event is the
 *  one persisted on `Message.events` (the audit ledger) so the log
 *  re-renders identically on navigation / reload. The UX renders
 *  every kind through ONE formatter registry (`components/
 *  inline-log.tsx`) — adding a kind never touches plumbing. */
export interface InlineEvent {
  kind: string;
  label: string;
  status: "running" | "done";
  /** Machine id : stage name (`retrieve`…) or tool name. */
  name?: string | null;
  /** Outcome flag for `tool_call` events ; null for stages. */
  ok?: boolean | null;
  /** Tool-loop round index for `tool_call` events. */
  round?: number | null;
  /** Elapsed time for `stage` events. */
  duration_ms?: number | null;
  /** Free-form metrics for `stage` events. */
  stats?: Record<string, unknown> | null;
  /** Result summary for `tool_call` events. */
  summary?: string | null;
  /** Affected document path for mutating DocGen tools
   *  (create / update / delete_document). Drives the inline log's
   *  "Open in Working area" deep-link (Phase 2.C.3). */
  path?: string | null;
  /** Size-capped call arguments for `tool_call` events. Lets the
   *  inline log expand each tool call into its chain-of-thought detail
   *  (#4). Large string values (e.g. document `content`) are truncated
   *  server-side to a preview. Absent on stages / legacy events. */
  arguments?: Record<string, unknown> | null;
  /** Resulting per-file version after a create/update_document tool
   *  call (R-200-147). Drives the versioned "Open in working area (vN)"
   *  link rendered below the response (#5). Absent otherwise. */
  version?: number | null;
}

/** Parse status of a C7 source — mirrors `ParseStatus` server-side
 *  (`c7_memory.models.ParseStatus`). The UX shows a colored badge
 *  per status ; `failed` surfaces `parse_error` in a callout.
 *  Pipeline order : pending → parsed → indexed (or failed at any
 *  step). `indexed` is the terminal happy state — chunks embedded
 *  + searchable. */
export type ParseStatus = "pending" | "parsed" | "indexed" | "failed";

/** A single source record (parsed file or pasted document) served by
 *  C7's `GET /api/v1/memory/projects/{pid}/sources`. */
export interface Source {
  source_id: string;
  project_id: string;
  mime_type: string;
  size_bytes: number;
  uploaded_by: string;
  uploaded_at: string;
  parse_status: ParseStatus;
  parse_error: string | null;
  chunk_count: number;
  model_id: string | null;
}

/** Response body of `GET /api/v1/memory/projects/{pid}/sources`.
 *  Server returns `{ "sources": [...] }` (not `items`). */
export interface SourceList {
  sources: Source[];
}

// ===========================================================================
// C3 — Conversations
// ===========================================================================

export type MessageRole = "user" | "assistant";

/** A single conversation as exposed by C3 (`ConversationPublic`). */
export interface Conversation {
  id: string;
  owner_id: string;
  project_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationList {
  conversations: Conversation[];
}

export interface ConversationResponse {
  conversation: Conversation;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  /** Unified inline-activity ledger persisted with the message
   *  (assistant messages only ; null on user messages). Pipeline
   *  stages + DocGen tool calls + future kinds, one audit list.
   *  Legacy messages saved with the pre-unification `stages` field
   *  are projected server-side into this list (kind="stage"), so the
   *  client only ever sees `events`. Rendered through `<InlineLog>`
   *  identically live and on reload. */
  events?: InlineEvent[] | null;
}

export interface MessageList {
  messages: Message[];
}

// ===========================================================================
// C5 — Requirements (documents + entities)
// ===========================================================================

/** Document slug record (e.g. "300-SPEC-REQUIREMENTS-MGMT"). The list
 *  endpoint returns metadata only ; pull individual docs by slug to get
 *  the rendered Markdown content. */
export interface RequirementDocument {
  slug: string;
  version: number;
  status: string;
  language: string;
  updated_at: string;
  size_bytes?: number;
}

export interface RequirementDocumentList {
  documents: RequirementDocument[];
}

/** Full document body with rendered Markdown content. */
export interface RequirementDocumentDetail {
  slug: string;
  version: number;
  status: string;
  content: string;
  language: string;
  updated_at: string;
}

/** A typed requirement entity (R-/E-/D-/T-/Q- prefix). */
export interface RequirementEntity {
  entity_id: string;
  type: string;
  version: number;
  status: string;
  category?: string;
  source_slug?: string;
  // Free-form payload from the YAML block — keys vary by entity type.
  payload: Record<string, unknown>;
}

export interface RequirementEntityList {
  entities: RequirementEntity[];
}

// ===========================================================================
// C6 — Validation
// ===========================================================================

export type ValidationRunStatus = "queued" | "running" | "completed" | "failed";
export type FindingSeverity = "info" | "warning" | "error" | "critical";

export interface ValidationRun {
  run_id: string;
  project_id: string;
  domain: string;
  status: ValidationRunStatus;
  started_at: string;
  completed_at: string | null;
  total_findings: number;
}

export interface Finding {
  finding_id: string;
  run_id: string;
  check_id: string;
  severity: FindingSeverity;
  title: string;
  message: string;
  location: string | null;
}

export interface FindingPage {
  findings: Finding[];
  total: number;
  limit: number;
  offset: number;
}

/** Plugin descriptor returned by `GET /api/v1/validation/plugins`. */
export interface ValidationPlugin {
  plugin_id: string;
  domain: string;
  version: string;
  description: string;
}

// ===========================================================================
// C4 — Project artifacts surface (R-200-131)
// ===========================================================================

export type ArtifactRunStatus = "pending" | "running" | "completed" | "failed";

/** One row from `GET /api/v1/projects/{pid}/artifacts/runs`. */
export interface ArtifactRun {
  run_id: string;
  project_id: string;
  tenant_id: string;
  started_at: string;
  completed_at: string | null;
  status: ArtifactRunStatus;
  file_count: number;
  total_bytes: number;
  label: string | null;
}

export interface ArtifactRunList {
  runs: ArtifactRun[];
}

/** One entry of the per-run flat tree (`kind` = file always in v1 ;
 *  the UX synthesises directory rows from path segments). */
export interface ArtifactNode {
  path: string;
  kind: "file" | "dir";
  size_bytes: number;
  mime_type: string | null;
  // Per-file revision count for live-docs, batched per AI response
  // (one bump per assistant turn that touched the file). Null/absent
  // for non-live-docs runs and when the Gitea history is unavailable ;
  // the tree renders `name (vN)` only when present.
  version?: number | null;
}

export interface ArtifactTree {
  run_id: string;
  nodes: ArtifactNode[];
}

/** Response of the live-docs write endpoints (POST create / PUT update,
 *  R-200-147). `version` is the per-file revision count after the write. */
export interface DocumentRef {
  path: string;
  size_bytes: number;
  version: number | null;
}

/** One commit returned by `GET /api/v1/projects/{pid}/git/commits`
 *  (R-200-147). Transparent backend : the UX renders these without
 *  knowing they came from Gitea ; the storage backend can be swapped
 *  later without an UX change. */
export interface ArtifactCommit {
  sha: string;
  message: string;
  author_name: string;
  author_email: string;
  committed_at: string;
}

export interface ArtifactCommitList {
  commits: ArtifactCommit[];
  page: number;
}

// ===========================================================================
// C4 — Orchestrator runs (pipeline trigger + plan approval)
// ===========================================================================

/** Five-phase pipeline. Mirrors `c4_orchestrator.models.Phase` verbatim. */
export type OrchestratorPhase = "brainstorm" | "spec" | "plan" | "generate" | "review";

export type OrchestratorRunStatus = "running" | "completed" | "blocked";

/** One non-blocking concern surfaced by an agent — surfaced in the
 *  pipeline panel as a coloured badge. */
export interface OrchestratorConcern {
  severity: string;
  message: string;
}

/** Run state exposed by `GET /api/v1/orchestrator/runs/{run_id}` and the
 *  POST/feedback responses. snake_case mirrors the Python wire format. */
export interface OrchestratorRun {
  run_id: string;
  project_id: string;
  session_id: string;
  tenant_id: string;
  user_id: string;
  domain: string;
  current_phase: OrchestratorPhase;
  status: OrchestratorRunStatus;
  started_at: string;
  completed_at: string | null;
  concerns: OrchestratorConcern[];
  minio_root: string;
  /** Operator-readable explanation when `status === "blocked"`. Set by
   *  C4 (`OrchestratorService._block_run`) and surfaced in the
   *  Pipeline page so the operator sees why automatic retries gave up.
   *  Null for running/completed runs. */
  block_reason?: string | null;
  /** Sliding window of the 200 most recent TraceEvents on the run,
   *  newest-first (R-200-201). Older events are loaded lazily via
   *  `GET /runs/{run_id}/trace?before=<ts>`. Empty on legacy runs. */
  trace: TraceEvent[];
}

/** Discriminator for `TraceEvent.kind` — mirrors
 *  `c4_orchestrator.models.TraceEventKind`. Adding a kind requires a
 *  matching <RunTrace> formatter on the UI side. */
export type TraceEventKind =
  | "agent-dispatch"
  | "gate-eval"
  | "fix-attempt"
  | "phase-boundary"
  | "steer-applied";

/** One entry of a run's append-only trace ledger (E-200-006, R-200-200).
 *  `ts` is ISO-8601 UTC. `phase` is the phase at which the event fired. */
export interface TraceEvent {
  kind: TraceEventKind;
  ts: string;
  phase: OrchestratorPhase;
  label: string;
  duration_ms?: number | null;
  ok?: boolean | null;
  payload?: Record<string, unknown> | null;
}

/** Body of `POST /api/v1/orchestrator/runs/{run_id}/steer` (E-200-007,
 *  R-200-202). A single operator hint, queued FIFO and consumed at the
 *  next phase / sub-agent boundary (no mid-LLM-call interruption). */
export interface OrchestratorRunSteer {
  message: string;
}

/** Response shape of the live-docs (and source-files) structural ops :
 *  mkdir / rename / move. Each op fills a different subset of fields ;
 *  the UX uses `from_path` + `to_path` (or `to_dir`) for the toast. */
export interface DocumentStructuralOpResult {
  from_path?: string | null;
  to_path?: string | null;
  to_dir?: string | null;
  path?: string | null;
  moved?: number | null;
}

/** Recursive node returned by `GET /source/tree` (R-200-170). Mirrors
 *  the backend `SourceTreeNode` Pydantic model. Folders carry
 *  `children` ; files carry `size_bytes`. */
export interface SourceTreeNode {
  name: string;
  kind: "file" | "dir";
  path: string;
  size_bytes?: number | null;
  children?: SourceTreeNode[] | null;
}

export interface SourceTreeResponse {
  run_id: string;
  truncated: boolean;
  nodes: SourceTreeNode[];
}

/** Same shape as `DocumentStructuralOpResult` plus the `run_id` of the
 *  artifact run that was mutated (source ops are run-scoped per
 *  Q-200-017). */
export interface SourceStructuralOpResult extends DocumentStructuralOpResult {
  run_id: string;
}

/** Response of `GET /source/file/{path}/meta` (R-200-173). Best-effort
 *  Gitea commit fields may be null when Gitea is unreachable. */
export interface SourceFileMeta {
  path: string;
  size: number;
  mime_type: string;
  modified_at?: string | null;
  last_commit_sha?: string | null;
  last_commit_message?: string | null;
  last_commit_author?: string | null;
  kg_indexed?: boolean | null;
}

/** Inclusive 1-indexed line range for an `excerpt`-kind reference
 *  (E-200-008). */
export interface PromptReferenceRange {
  start_line: number;
  end_line: number;
}

/** Operator-attached prompt reference (E-200-008 / R-200-180..184).
 *  `source=source` is deferred to v2 per Q-200-019 — v1 UI only emits
 *  `source=live-docs` references. */
export interface PromptReference {
  kind: "file" | "excerpt";
  source: "live-docs" | "source";
  path: string;
  range?: PromptReferenceRange | null;
}

/** Body of `POST /api/v1/orchestrator/runs`. `domain` defaults to
 *  `code` ; the UX pipeline panel uses the project's profile id. */
export interface OrchestratorRunCreate {
  project_id: string;
  session_id: string;
  initial_prompt: string;
  domain?: string;
}

/** Body of `POST /api/v1/orchestrator/runs/{run_id}/feedback`. The
 *  pipeline panel sends `{phase: "plan", approved: true}` to clear
 *  Gate A and resume into generate. */
export interface OrchestratorRunFeedback {
  phase: OrchestratorPhase;
  approved?: boolean | null;
  user_feedback?: string | null;
}

/** Body of `POST /api/v1/orchestrator/runs/{run_id}/resume`. Admin
 *  override after a BLOCKED halt — `retry` re-attempts the failing
 *  phase, `abort` marks the run terminally aborted. `skip-phase` is
 *  spec'd but deferred to C4 v2 (Q-200-009) ; the UI does not offer
 *  it until the backend implements it. */
export type OrchestratorRunResumeStrategy = "retry" | "skip-phase" | "abort";

export interface OrchestratorRunResume {
  strategy: OrchestratorRunResumeStrategy;
}

/** MIME types accepted by C7's upload endpoint (R-400-024). Anything
 *  else yields 415. Source : `c7_memory.ingestion.parser` registry. */
export const SUPPORTED_MIME_TYPES = {
  "text/plain": { ext: [".txt"], label: "Plain text" },
  "text/markdown": { ext: [".md", ".markdown"], label: "Markdown" },
  "text/html": { ext: [".html", ".htm"], label: "HTML" },
  "application/pdf": { ext: [".pdf"], label: "PDF" },
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
    ext: [".docx"],
    label: "Word (DOCX)",
  },
} as const;

export type SupportedMimeType = keyof typeof SUPPORTED_MIME_TYPES;

/** Map a filename to its expected MIME type via extension. Returns
 *  null for unsupported extensions — caller surfaces a UX error
 *  instead of trying to upload. */
export function mimeTypeFromFilename(filename: string): SupportedMimeType | null {
  const lower = filename.toLowerCase();
  for (const [mime, info] of Object.entries(SUPPORTED_MIME_TYPES)) {
    if (info.ext.some((ext) => lower.endsWith(ext))) {
      return mime as SupportedMimeType;
    }
  }
  return null;
}
