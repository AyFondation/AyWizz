// =============================================================================
// File: apiClient.ts
// Version: 7
// Path: ay_platform_ui/lib/apiClient.ts
// Description: Thin wrapper over `fetch` that prepends the runtime-config
//              `apiBaseUrl` to every call and (optionally) attaches the
//              user's JWT bearer token. Components use this rather than
//              calling `fetch` directly so the API base URL is honoured
//              uniformly.
//
//              v7 (2026-05-19) : unified inline channel.
//              `sendMessageStream` parses ONE `event: inline` SSE
//              type (replacing `event: stage` + `event: tool_call`)
//              and dispatches every payload to `onInlineEvent`
//              regardless of `kind` — the caller feeds `<InlineLog>`.
//
//              v6 : `sendMessageStream` now parses NAMED SSE events.
//              Default `message` events carry assistant tokens (legacy
//              behaviour). New `event: stage` events carry JSON
//              describing pipeline progress (retrieve / generate /
//              done) and are dispatched to the optional `onStage`
//              callback ; older clients without `onStage` ignore them.
//
//              v5 : user preferences (`getUserPreferences` /
//              `updateUserPreferences`), per-project read + patch
//              (`getProject` / `updateProject`), and `sendMessageStream`
//              now accepts optional `user_prompt` + `project_prompt`
//              forwarded to C3 for LLM prompt assembly.
//
//              v4 (2026-05-11) : C3 conversations (CRUD + SSE chat
//              stream), C5 requirements docs + entities, C6 validation
//              runs trigger + findings.
//
//              v3 (2026-05-11) : C7 source surface — `listSources`,
//              `uploadSource` (multipart), `getSource`, `deleteSource`,
//              `sourceBlobUrl` (URL for `<a download>` links).
//
//              v2 (2026-05-11) : adds `listProjects()` (GET
//              /api/v1/projects, tenant-scoped).
// =============================================================================

import type {
  ArtifactCommitList,
  ArtifactRunList,
  ArtifactTree,
  Conversation,
  ConversationList,
  ConversationResponse,
  DocumentRef,
  DocumentStructuralOpResult,
  Finding,
  FindingPage,
  InlineEvent,
  MessageList,
  OrchestratorRun,
  OrchestratorRunCreate,
  OrchestratorRunFeedback,
  OrchestratorRunResumeStrategy,
  OrchestratorRunSteer,
  PlatformConfig,
  Project,
  ProjectList,
  ProjectUpdate,
  PromptReference,
  RequirementDocumentDetail,
  RequirementDocumentList,
  RequirementEntityList,
  Source,
  SourceFileMeta,
  SourceList,
  SourceStructuralOpResult,
  SourceTreeResponse,
  TraceEvent,
  UserPreferencesResponse,
  UserPreferencesUpdate,
  ValidationPlugin,
  ValidationRun,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly url: string,
    public readonly body: string,
  ) {
    super(`API ${status} ${url}: ${body || "(empty body)"}`);
    this.name = "ApiError";
  }
}

/** Token storage key — same shape as auth-matrix tests use, but in
 *  localStorage rather than HTTP-only cookie. v1 trade-off : XSS
 *  exposure vs CSRF resilience ; HTTP-only cookies move that to the
 *  next iteration with proper backend `/auth/login` cookie support. */
const TOKEN_KEY = "aywizz.token";

export function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function writeStoredToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
}

// ---------------------------------------------------------------------------
// Session-revocation hook
//
// When C2 rejects a request with 401 (token expired, session revoked
// server-side after a restart, etc.) every protected page that catches
// the ApiError would otherwise render its own "API 401 …" string —
// useless to the operator. We funnel those 401s through a single
// module-level callback the `AuthProvider` registers at mount : the
// hook clears the token, the auth state flips to "anonymous", and the
// `(protected)` gate's useEffect redirects to `/login`.
// ---------------------------------------------------------------------------

type SessionRevokedHandler = () => void;
let _sessionRevokedHandler: SessionRevokedHandler | null = null;

/** Register a handler fired the first time `ApiClient` sees a 401 on
 *  an authenticated request. Pass `null` to unregister (e.g. on
 *  AuthProvider unmount). The handler is invoked at most once per
 *  flight — concurrent 401s collapse to a single notification so the
 *  AuthProvider doesn't thrash. */
export function setSessionRevokedHandler(handler: SessionRevokedHandler | null): void {
  _sessionRevokedHandler = handler;
}

function _notifySessionRevoked(): void {
  // Pop the handler before firing so re-entrant 401s (multiple
  // requests in flight at the moment the session is revoked) don't
  // trigger duplicate redirects ; the AuthProvider re-registers on
  // its next mount.
  const handler = _sessionRevokedHandler;
  _sessionRevokedHandler = null;
  handler?.();
}

export class ApiClient {
  constructor(private readonly cfg: PlatformConfig) {}

  private url(path: string): string {
    const base = this.cfg.runtime.apiBaseUrl;
    return base ? `${base}${path}` : path;
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const url = this.url(path);
    const headers = new Headers(init.headers);
    // For FormData bodies, let the browser set Content-Type with the
    // multipart boundary — forcing application/json would break the
    // upload. For everything else, default to JSON.
    if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const token = readStoredToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const resp = await fetch(url, { ...init, headers, cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      // A 401 on a token-bearing request means the JWT was rejected
      // by C2 (expired, session revoked after a backend restart, key
      // rotation, …). Funnel through the registered handler so the
      // AuthProvider flips to anonymous and the protected gate
      // redirects — beats a per-page "Failed to load: API 401 …".
      // 401s on UNAUTHENTICATED requests (no token in storage —
      // typically the login page hitting `/auth/login` with bad
      // credentials) flow through as-is : the caller wants to
      // display a "wrong password" message rather than redirect.
      if (resp.status === 401 && token) {
        _notifySessionRevoked();
      }
      throw new ApiError(resp.status, url, body);
    }
    if (resp.status === 204) return undefined as T;
    return (await resp.json()) as T;
  }

  /** POST /auth/login — returns the access token. The caller (login
   *  page) forwards it to `auth.setToken(token)` so the AuthProvider
   *  owns persistence + decoded-claims state ; this method
   *  intentionally does NOT write to localStorage directly. */
  async login(username: string, password: string): Promise<string> {
    type LoginResponse = {
      access_token: string;
      token_type: string;
      expires_in: number;
    };
    const body = await this.request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    return body.access_token;
  }

  /** GET /api/v1/projects — list projects in the caller's tenant.
   *  `tenant_manager` callers are rejected server-side (content-blind
   *  per E-100-002 v2) ; every other authenticated user receives the
   *  full list. The UI applies per-user filtering via
   *  `JWTClaims.project_scopes` when needed. */
  async listProjects(): Promise<ProjectList> {
    return this.request<ProjectList>("/api/v1/projects", { method: "GET" });
  }

  /** GET /api/v1/projects/{pid} — single project (any tenant member). */
  async getProject(projectId: string): Promise<Project> {
    return this.request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
      method: "GET",
    });
  }

  /** PATCH /api/v1/projects/{pid} — partial update (name + system_prompt).
   *  Restricted to admin / tenant_admin / project_owner. */
  async updateProject(projectId: string, payload: ProjectUpdate): Promise<Project> {
    return this.request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  // -------------------------------------------------------------------------
  // C2 — Self-service user preferences
  // -------------------------------------------------------------------------

  /** GET /api/v1/users/me/preferences — read the caller's effective
   *  preferences (trigram override + LLM user prompt). */
  async getUserPreferences(): Promise<UserPreferencesResponse> {
    return this.request<UserPreferencesResponse>("/api/v1/users/me/preferences", { method: "GET" });
  }

  /** PUT /api/v1/users/me/preferences — upsert. Empty-string field
   *  values clear the corresponding override (revert to default). */
  async updateUserPreferences(payload: UserPreferencesUpdate): Promise<UserPreferencesResponse> {
    return this.request<UserPreferencesResponse>("/api/v1/users/me/preferences", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  }

  // -------------------------------------------------------------------------
  // C7 — Sources
  // -------------------------------------------------------------------------

  /** GET /api/v1/memory/projects/{pid}/sources — list project sources. */
  async listSources(projectId: string): Promise<SourceList> {
    return this.request<SourceList>(
      `/api/v1/memory/projects/${encodeURIComponent(projectId)}/sources`,
      { method: "GET" },
    );
  }

  /** GET /api/v1/memory/projects/{pid}/sources/{sid} — single source metadata. */
  async getSource(projectId: string, sourceId: string): Promise<Source> {
    return this.request<Source>(
      `/api/v1/memory/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "GET" },
    );
  }

  /** POST /api/v1/memory/projects/{pid}/sources/upload — multipart upload.
   *  Required form fields : `file`, `source_id`, `mime_type`. C7 stores
   *  the raw bytes in MinIO then runs parse → chunk → embed → index. */
  async uploadSource(
    projectId: string,
    file: File,
    sourceId: string,
    mimeType: string,
  ): Promise<Source> {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("source_id", sourceId);
    form.append("mime_type", mimeType);
    return this.request<Source>(
      `/api/v1/memory/projects/${encodeURIComponent(projectId)}/sources/upload`,
      { method: "POST", body: form },
    );
  }

  /** DELETE /api/v1/memory/projects/{pid}/sources/{sid}. Requires
   *  `project_owner` or `admin`. */
  async deleteSource(projectId: string, sourceId: string): Promise<void> {
    await this.request<void>(
      `/api/v1/memory/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    );
  }

  /** Public URL for downloading the raw blob of a source (used as
   *  `<a href={...} download>`). Not a fetch — the browser navigates,
   *  C7 streams. The Bearer token is sent on the navigation via the
   *  same-origin Traefik route only when the user is signed in via
   *  the AuthProvider's cookie — but in v1 we keep JWT in
   *  localStorage, so this URL is meaningful only for in-app fetches.
   *  For now, callers fetch + create an object URL when they need
   *  the download to be auth-aware. */
  sourceBlobUrl(projectId: string, sourceId: string): string {
    return this.url(
      `/api/v1/memory/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/blob`,
    );
  }

  /** Auth-aware blob download : fetch with Bearer, return a Blob the
   *  caller can pipe into a programmatic download. Returns the
   *  filename suggested by the Content-Disposition header (if any). */
  async downloadSourceBlob(
    projectId: string,
    sourceId: string,
  ): Promise<{ blob: Blob; filename: string | null }> {
    const url = this.sourceBlobUrl(projectId, sourceId);
    const token = readStoredToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const resp = await fetch(url, { method: "GET", headers, cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      throw new ApiError(resp.status, url, body);
    }
    const blob = await resp.blob();
    const cd = resp.headers.get("Content-Disposition") ?? "";
    const match = cd.match(/filename="?([^"]+)"?/i);
    return { blob, filename: match ? match[1] : null };
  }

  // -------------------------------------------------------------------------
  // C3 — Conversations + chat SSE
  // -------------------------------------------------------------------------

  /** GET /api/v1/conversations — list the caller's conversations. */
  async listConversations(): Promise<ConversationList> {
    return this.request<ConversationList>("/api/v1/conversations", { method: "GET" });
  }

  /** POST /api/v1/conversations — start a new conversation. */
  async createConversation(payload: {
    title: string;
    project_id?: string | null;
  }): Promise<Conversation> {
    const body = await this.request<ConversationResponse>("/api/v1/conversations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return body.conversation;
  }

  /** GET /api/v1/conversations/{cid}. */
  async getConversation(conversationId: string): Promise<Conversation> {
    const body = await this.request<ConversationResponse>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
      { method: "GET" },
    );
    return body.conversation;
  }

  /** DELETE /api/v1/conversations/{cid}. */
  async deleteConversation(conversationId: string): Promise<void> {
    await this.request<void>(`/api/v1/conversations/${encodeURIComponent(conversationId)}`, {
      method: "DELETE",
    });
  }

  /** PATCH /api/v1/conversations/{cid} — partial update. Used by the
   *  chat page to auto-rename a freshly-created conversation from the
   *  first user message (replacing the placeholder "New conversation"
   *  title with a meaningful summary). */
  async updateConversation(
    conversationId: string,
    payload: { title?: string; project_id?: string | null },
  ): Promise<Conversation> {
    const body = await this.request<ConversationResponse>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
      { method: "PATCH", body: JSON.stringify(payload) },
    );
    return body.conversation;
  }

  /** GET /api/v1/conversations/{cid}/messages — full history. */
  async listMessages(conversationId: string): Promise<MessageList> {
    return this.request<MessageList>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      { method: "GET" },
    );
  }

  /** POST /api/v1/conversations/{cid}/messages — send + stream reply.
   *
   *  Server emits Server-Sent Events as plain `data: <chunk>\n\n`
   *  lines terminated by `data: [DONE]\n\n`. We don't use the
   *  EventSource API (no Bearer header support) and stream the body
   *  manually. `onChunk` fires for each non-DONE chunk ;
   *  the returned Promise resolves on `[DONE]` or on stream end. */
  async sendMessageStream(
    conversationId: string,
    content: string,
    onChunk: (chunk: string) => void,
    options: {
      userPrompt?: string | null;
      projectPrompt?: string | null;
      /** Unified inline-activity callback. Fires for every
       *  `event: inline` SSE payload regardless of `kind` (stage /
       *  tool_call / future). The caller accumulates them and feeds
       *  `<InlineLog>` — one entry point. */
      onInlineEvent?: (evt: InlineEvent) => void;
      /** Prompt-attached references (R-200-180). Up to 10 entries ;
       *  server enforces a 32K-token cap on combined resolved
       *  content (returns 413 on overflow). */
      references?: PromptReference[];
    } = {},
  ): Promise<void> {
    const url = this.url(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`);
    const headers = new Headers();
    headers.set("Content-Type", "application/json");
    headers.set("Accept", "text/event-stream");
    const token = readStoredToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const body: Record<string, unknown> = { content };
    if (options.userPrompt != null && options.userPrompt !== "") {
      body.user_prompt = options.userPrompt;
    }
    if (options.projectPrompt != null && options.projectPrompt !== "") {
      body.project_prompt = options.projectPrompt;
    }
    if (options.references && options.references.length > 0) {
      body.references = options.references;
    }
    const resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      // Same session-revoked funnel as `request()` — a 401 on a
      // streaming message send means the JWT was rejected ; redirect
      // to login rather than render the raw 401 in the composer.
      if (resp.status === 401 && token) {
        _notifySessionRevoked();
      }
      throw new ApiError(resp.status, url, text);
    }
    if (!resp.body) throw new Error("response has no body — cannot stream");

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    // SSE spec : events are separated by blank lines. Each event is
    // zero or more `event:` / `data:` lines. We dispatch by event
    // type: default `message` → assistant token (`onChunk`) ; named
    // `inline` → unified inline-activity JSON (`onInlineEvent`).
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      // Split on event boundary (blank line = two newlines).
      // biome-ignore lint/suspicious/noAssignInExpressions: idiomatic loop pattern
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let eventType = "message";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) {
            // RFC : strip a single optional leading space after the colon.
            const rest = line.slice(6);
            eventType = (rest.startsWith(" ") ? rest.slice(1) : rest).trim();
          } else if (line.startsWith("data:")) {
            // SSE spec : after `data:` strip EXACTLY ONE optional space
            // (RFC). `trimStart()` was wrong — it ate leading spaces
            // belonging to the token (e.g. " L'Italie" becomes
            // "L'Italie", losing the word boundary).
            const rest = line.slice(5);
            dataLines.push(rest.startsWith(" ") ? rest.slice(1) : rest);
          }
        }
        if (dataLines.length === 0) continue;
        const data = dataLines.join("\n");
        if (eventType === "inline") {
          if (options.onInlineEvent) {
            try {
              options.onInlineEvent(JSON.parse(data) as InlineEvent);
            } catch {
              // Malformed inline payload — silently ignore so a
              // server hiccup never breaks the token stream.
            }
          }
          continue;
        }
        if (data === "[DONE]") return;
        onChunk(data);
      }
    }
  }

  // -------------------------------------------------------------------------
  // C5 — Requirements (read-only surface for the UX)
  // -------------------------------------------------------------------------

  /** GET /api/v1/projects/{pid}/requirements/documents. */
  async listRequirementDocuments(projectId: string): Promise<RequirementDocumentList> {
    return this.request<RequirementDocumentList>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/requirements/documents`,
      { method: "GET" },
    );
  }

  /** GET /api/v1/projects/{pid}/requirements/documents/{slug}. */
  async getRequirementDocument(
    projectId: string,
    slug: string,
  ): Promise<RequirementDocumentDetail> {
    return this.request<RequirementDocumentDetail>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/requirements/documents/${encodeURIComponent(slug)}`,
      { method: "GET" },
    );
  }

  /** GET /api/v1/projects/{pid}/requirements/entities. */
  async listRequirementEntities(projectId: string): Promise<RequirementEntityList> {
    return this.request<RequirementEntityList>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/requirements/entities`,
      { method: "GET" },
    );
  }

  // -------------------------------------------------------------------------
  // C6 — Validation runs
  // -------------------------------------------------------------------------

  /** GET /api/v1/validation/plugins — list installed validation plugins. */
  async listValidationPlugins(): Promise<ValidationPlugin[]> {
    return this.request<ValidationPlugin[]>("/api/v1/validation/plugins", {
      method: "GET",
    });
  }

  /** POST /api/v1/validation/runs — trigger a new run. Project-scoped
   *  via the `project_id` payload. Returns the queued run id. */
  async triggerValidationRun(payload: {
    project_id: string;
    domain: string;
    requirements?: unknown[];
    artifacts?: unknown[];
  }): Promise<{ run_id: string }> {
    return this.request<{ run_id: string }>("/api/v1/validation/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  /** GET /api/v1/validation/runs/{run_id}. */
  async getValidationRun(runId: string): Promise<ValidationRun> {
    return this.request<ValidationRun>(`/api/v1/validation/runs/${encodeURIComponent(runId)}`, {
      method: "GET",
    });
  }

  /** GET /api/v1/validation/runs/{run_id}/findings. */
  async listValidationFindings(runId: string, limit = 100, offset = 0): Promise<FindingPage> {
    const qs = `?limit=${limit}&offset=${offset}`;
    return this.request<FindingPage>(
      `/api/v1/validation/runs/${encodeURIComponent(runId)}/findings${qs}`,
      { method: "GET" },
    );
  }

  // -------------------------------------------------------------------------
  // C4 — Project artifacts (Code source / DocGen). Transparent MinIO
  // surface ; UX never sees the storage backend (R-200-133).
  // -------------------------------------------------------------------------

  /** GET /api/v1/projects/{pid}/artifacts/runs — list runs. */
  async listArtifactRuns(projectId: string): Promise<ArtifactRunList> {
    return this.request<ArtifactRunList>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/runs`,
      { method: "GET" },
    );
  }

  /** GET /api/v1/projects/{pid}/artifacts/runs/{rid}/tree — flat
   *  node list ; UX rebuilds the hierarchy by splitting `path`. */
  async getArtifactTree(projectId: string, runId: string): Promise<ArtifactTree> {
    return this.request<ArtifactTree>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/runs/${encodeURIComponent(runId)}/tree`,
      { method: "GET" },
    );
  }

  /** GET /api/v1/projects/{pid}/artifacts/runs/{rid}/blob?path=...
   *  Returns the raw bytes as text (decoded UTF-8) so the Monaco /
   *  pre viewer can render directly. For binary files (PDF, images)
   *  the caller switches to `artifactBlobUrl()` + auth-aware download. */
  async getArtifactBlobText(
    projectId: string,
    runId: string,
    path: string,
  ): Promise<{ text: string; contentType: string }> {
    const url = this.url(
      `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/runs/${encodeURIComponent(runId)}/blob?path=${encodeURIComponent(path)}`,
    );
    const headers = new Headers();
    const token = readStoredToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const resp = await fetch(url, { method: "GET", headers, cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      if (resp.status === 401 && token) {
        _notifySessionRevoked();
      }
      throw new ApiError(resp.status, url, body);
    }
    const text = await resp.text();
    const contentType = resp.headers.get("Content-Type") ?? "text/plain";
    return { text, contentType };
  }

  /** GET /api/v1/projects/{pid}/git/commits — paginated commit list
   *  proxied from the project's Gitea repo (R-200-147). Returns
   *  empty when Gitea is not wired or the repo has no commits yet.
   *  `path` (optional) restricts the list to one file's revision
   *  history — the source for the "view a previous version" picker. */
  async listProjectCommits(
    projectId: string,
    page = 1,
    path?: string,
  ): Promise<ArtifactCommitList> {
    const pathQ = path ? `&path=${encodeURIComponent(path)}` : "";
    return this.request<ArtifactCommitList>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/git/commits?page=${page}${pathQ}`,
      { method: "GET" },
    );
  }

  /** GET /api/v1/projects/{pid}/documents/{path} — read the CURRENT
   *  (latest) text content of a live-docs file. Returns the decoded
   *  text + content type. Use `getDocumentTextAtRef` for historical SHAs. */
  async getDocumentText(
    projectId: string,
    path: string,
  ): Promise<{ text: string; contentType: string }> {
    const url = this.url(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    );
    const headers = new Headers();
    const token = readStoredToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const resp = await fetch(url, { method: "GET", headers, cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      if (resp.status === 401 && token) {
        _notifySessionRevoked();
      }
      throw new ApiError(resp.status, url, body);
    }
    const text = await resp.text();
    const contentType = resp.headers.get("Content-Type") ?? "text/plain";
    return { text, contentType };
  }

  /** GET /api/v1/projects/{pid}/documents/{path}?ref=<sha> — read a
   *  live-docs document as it existed at a specific commit (R-200-147
   *  history viewer). Returns the decoded text + content type, same
   *  shape as `getArtifactBlobText`. */
  async getDocumentTextAtRef(
    projectId: string,
    path: string,
    ref: string,
  ): Promise<{ text: string; contentType: string }> {
    const url = this.url(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}?ref=${encodeURIComponent(ref)}`,
    );
    const headers = new Headers();
    const token = readStoredToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const resp = await fetch(url, { method: "GET", headers, cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      if (resp.status === 401 && token) {
        _notifySessionRevoked();
      }
      throw new ApiError(resp.status, url, body);
    }
    const text = await resp.text();
    const contentType = resp.headers.get("Content-Type") ?? "text/plain";
    return { text, contentType };
  }

  /** Auth-aware download : fetch the blob with `download=1` so the
   *  server emits `Content-Disposition: attachment`, return a Blob
   *  the caller can pipe into a programmatic download. */
  async downloadArtifactBlob(
    projectId: string,
    runId: string,
    path: string,
  ): Promise<{ blob: Blob; filename: string }> {
    const url = this.url(
      `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/runs/${encodeURIComponent(runId)}/blob?path=${encodeURIComponent(path)}&download=1`,
    );
    const headers = new Headers();
    const token = readStoredToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const resp = await fetch(url, { method: "GET", headers, cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      if (resp.status === 401 && token) {
        _notifySessionRevoked();
      }
      throw new ApiError(resp.status, url, body);
    }
    const blob = await resp.blob();
    const fallback = path.split("/").pop() ?? "artifact";
    const cd = resp.headers.get("Content-Disposition") ?? "";
    const match = cd.match(/filename="?([^"]+)"?/i);
    return { blob, filename: match ? match[1] : fallback };
  }

  /** GET /api/v1/validation/findings/{finding_id}. */
  async getValidationFinding(findingId: string): Promise<Finding> {
    return this.request<Finding>(`/api/v1/validation/findings/${encodeURIComponent(findingId)}`, {
      method: "GET",
    });
  }

  // -------------------------------------------------------------------------
  // C4 — Live-docs operator-driven structural ops (Tranche B §5.17).
  // R-200-160..164. NOT LLM tools — driven by the tree right-click menu.
  // -------------------------------------------------------------------------

  /** POST /api/v1/projects/{pid}/documents/mkdir — creates an empty
   *  directory by writing a `.keep` marker (R-200-161). 409 if the
   *  path already exists. */
  async mkdirDocument(projectId: string, path: string): Promise<DocumentStructuralOpResult> {
    return this.request<DocumentStructuralOpResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/mkdir`,
      { method: "POST", body: JSON.stringify({ path }) },
    );
  }

  /** POST /api/v1/projects/{pid}/documents/rename — pure path change.
   *  Works on files and directories (recursive). 404 / 409 / 400. */
  async renameDocument(
    projectId: string,
    fromPath: string,
    toPath: string,
  ): Promise<DocumentStructuralOpResult> {
    return this.request<DocumentStructuralOpResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/rename`,
      {
        method: "POST",
        body: JSON.stringify({ from_path: fromPath, to_path: toPath }),
      },
    );
  }

  /** POST /api/v1/projects/{pid}/documents/move — relocate under a
   *  different directory. Target = `<to_dir>/<basename(from_path)>`. */
  async moveDocument(
    projectId: string,
    fromPath: string,
    toDir: string,
  ): Promise<DocumentStructuralOpResult> {
    return this.request<DocumentStructuralOpResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/move`,
      {
        method: "POST",
        body: JSON.stringify({ from_path: fromPath, to_dir: toDir }),
      },
    );
  }

  /** DELETE /api/v1/projects/{pid}/documents/{path} — remove a
   *  document from MinIO (Gitea history retained per R-200-155). */
  async deleteDocument(projectId: string, path: string): Promise<void> {
    await this.request<void>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      { method: "DELETE" },
    );
  }

  /** POST /api/v1/projects/{pid}/documents — create (or overwrite) a
   *  live-docs file. Pass `content=""` for a blank file. Returns the
   *  DocumentRef with its new per-file version. */
  async createDocument(projectId: string, path: string, content = ""): Promise<DocumentRef> {
    return this.request<DocumentRef>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents`,
      { method: "POST", body: JSON.stringify({ path, content }) },
    );
  }

  /** PUT /api/v1/projects/{pid}/documents/{path} — replace a live-docs
   *  file's content (manual edit). Returns the updated DocumentRef. */
  async updateDocument(projectId: string, path: string, content: string): Promise<DocumentRef> {
    return this.request<DocumentRef>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/documents/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      { method: "PUT", body: JSON.stringify({ content }) },
    );
  }

  // -------------------------------------------------------------------------
  // C4 — Source-files surface (Tranche B §5.18). Tree projection +
  // operator structural ops + metadata. Scoped to one run_id at a
  // time (Q-200-017). Editor+ RBAC on mutating endpoints.
  // -------------------------------------------------------------------------

  /** GET /api/v1/projects/{pid}/source/tree?run_id=... — recursive
   *  source-files projection (R-200-170). */
  async getSourceTree(projectId: string, runId: string): Promise<SourceTreeResponse> {
    return this.request<SourceTreeResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/source/tree?run_id=${encodeURIComponent(runId)}`,
      { method: "GET" },
    );
  }

  async mkdirSource(
    projectId: string,
    runId: string,
    path: string,
  ): Promise<SourceStructuralOpResult> {
    return this.request<SourceStructuralOpResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/source/mkdir?run_id=${encodeURIComponent(runId)}`,
      { method: "POST", body: JSON.stringify({ path }) },
    );
  }

  async renameSource(
    projectId: string,
    runId: string,
    fromPath: string,
    toPath: string,
  ): Promise<SourceStructuralOpResult> {
    return this.request<SourceStructuralOpResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/source/rename?run_id=${encodeURIComponent(runId)}`,
      { method: "POST", body: JSON.stringify({ from_path: fromPath, to_path: toPath }) },
    );
  }

  async moveSource(
    projectId: string,
    runId: string,
    fromPath: string,
    toDir: string,
  ): Promise<SourceStructuralOpResult> {
    return this.request<SourceStructuralOpResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/source/move?run_id=${encodeURIComponent(runId)}`,
      { method: "POST", body: JSON.stringify({ from_path: fromPath, to_dir: toDir }) },
    );
  }

  async getSourceFileMeta(projectId: string, runId: string, path: string): Promise<SourceFileMeta> {
    return this.request<SourceFileMeta>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/source/file/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}/meta?run_id=${encodeURIComponent(runId)}`,
      { method: "GET" },
    );
  }

  /** DELETE /api/v1/projects/{pid}/source/file/{path}?run_id=... — remove
   *  one source file (R-200-175). Editor+ RBAC enforced server-side. */
  async deleteSourceFile(projectId: string, runId: string, path: string): Promise<void> {
    await this.request<void>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/source/file/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}?run_id=${encodeURIComponent(runId)}`,
      { method: "DELETE" },
    );
  }

  // -------------------------------------------------------------------------
  // C4 — Orchestrator pipeline runs (trigger + plan approval).
  // The Pipeline page POSTs a goal, polls the run, then surfaces the
  // generated files in the Code-source section. Same run_id is reused
  // as the artifact-run id (R-200-151).
  // -------------------------------------------------------------------------

  /** POST /api/v1/orchestrator/runs — start a pipeline run. The
   *  brainstorm phase fires inline ; spec + plan auto-advance ;
   *  the run pauses at PLAN waiting for the operator's Gate A. */
  async createOrchestratorRun(payload: OrchestratorRunCreate): Promise<OrchestratorRun> {
    return this.request<OrchestratorRun>("/api/v1/orchestrator/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  /** GET /api/v1/orchestrator/runs/{run_id} — poll the run state.
   *  The Pipeline page polls this every 2 s while RUNNING ; stops on
   *  COMPLETED / BLOCKED. */
  async getOrchestratorRun(runId: string): Promise<OrchestratorRun> {
    return this.request<OrchestratorRun>(`/api/v1/orchestrator/runs/${encodeURIComponent(runId)}`, {
      method: "GET",
    });
  }

  /** POST /api/v1/orchestrator/runs/{run_id}/feedback — pass Gate A
   *  with `{phase: "plan", approved: true}` ; or append user feedback
   *  to re-run the current phase. */
  async submitOrchestratorFeedback(
    runId: string,
    payload: OrchestratorRunFeedback,
  ): Promise<OrchestratorRun> {
    return this.request<OrchestratorRun>(
      `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/feedback`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

  /** POST /api/v1/orchestrator/runs/{run_id}/resume — admin override
   *  after a BLOCKED halt. `retry` re-attempts the failing phase ;
   *  `abort` terminates the run. (`skip-phase` is backend-deferred,
   *  Q-200-009, and not surfaced in the UI yet.) */
  async resumeOrchestratorRun(
    runId: string,
    strategy: OrchestratorRunResumeStrategy,
  ): Promise<OrchestratorRun> {
    return this.request<OrchestratorRun>(
      `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/resume`,
      { method: "POST", body: JSON.stringify({ strategy }) },
    );
  }

  /** GET /api/v1/orchestrator/runs/{run_id}/trace — paginated back-in-time
   *  read of the TraceEvent ledger (R-200-201). `before` is an ISO-8601
   *  timestamp ; omit to fetch the most-recent slice (mostly redundant with
   *  `OrchestratorRun.trace`, but useful for explicit refreshes). Newest-first,
   *  capped at `limit` (server enforces ≤ 200). */
  async readOrchestratorTrace(
    runId: string,
    options: { before?: string; limit?: number } = {},
  ): Promise<TraceEvent[]> {
    const params = new URLSearchParams();
    if (options.before) params.set("before", options.before);
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    const qs = params.toString();
    return this.request<TraceEvent[]>(
      `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/trace${qs ? `?${qs}` : ""}`,
      { method: "GET" },
    );
  }

  /** POST /api/v1/orchestrator/runs/{run_id}/steer — queue an operator
   *  hint (R-200-202). The hint is consumed at the next phase / sub-agent
   *  boundary (no mid-LLM-call interruption per R-200-203). Returns 409
   *  if the run is not RUNNING. */
  async steerOrchestratorRun(
    runId: string,
    payload: OrchestratorRunSteer,
  ): Promise<OrchestratorRun> {
    return this.request<OrchestratorRun>(
      `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/steer`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }
}
