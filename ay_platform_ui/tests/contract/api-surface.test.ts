// =============================================================================
// File: api-surface.test.ts
// Version: 1
// Path: ay_platform_ui/tests/contract/api-surface.test.ts
// Description: UI ↔ backend HTTP-contract test. Drives EVERY public method
//              of `lib/apiClient.ts` through a capturing `fetch` spy, then
//              asserts that every (method, path) the client emits resolves
//              to a real backend route declared in the committed snapshot
//              `tests/contract/backend-routes.json` (generated from the
//              auth-matrix catalog by
//              `ay_platform_core/scripts/checks/export_route_catalog_json.py`,
//              itself pinned to the live FastAPI routers by
//              `tests/coherence/test_route_catalog.py`).
//
//              This is the regression guard for front/back CONTRACT DRIFT —
//              the exact failure class the upload-405 bug belonged to
//              (D-020 retired `POST /sources/upload`; the client kept
//              calling it; mocked HTTP tests could not see it because a mock
//              encodes the same wrong contract as the code). A spy over the
//              real client + a router-pinned snapshot closes that gap: an
//              endpoint the client calls but the backend does not expose
//              fails here.
//
//              Completeness ("toutes les fonctions") is enforced two ways:
//              (1) the test exercises a hand-maintained call for each method,
//              and (2) it asserts that the set of exercised methods equals
//              the full set of HTTP-bearing prototype methods — so a NEW
//              `apiClient` method that is not wired here fails the build.
// =============================================================================

// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "@/lib/apiClient";
import type { PlatformConfig } from "@/lib/types";
import backendRoutes from "./backend-routes.json";

// ---------------------------------------------------------------------------
// Non-FastAPI endpoints the client legitimately calls that are NOT in the
// backend route snapshot. Each entry needs a documented rationale — this is
// the ONLY sanctioned escape hatch from the contract, and adding one is a
// decision, not a convenience.
// ---------------------------------------------------------------------------
// R-100-081 v3: byte custody moved to C7 — the UI no longer calls the n8n
// webhook directly (uploadSource → C7 `POST /sources/upload`, a real FastAPI
// route in the snapshot). The allowlist is now empty.
const NON_BACKEND_ALLOWLIST: ReadonlyArray<{ method: string; path: string; why: string }> = [];

type BackendRoute = { method: string; path: string; component: string; auth: string };
const ROUTES: BackendRoute[] = (backendRoutes as { routes: BackendRoute[] }).routes;

// Prototype methods that do NOT emit an HTTP call to a backend route and are
// therefore exempt from the completeness check (private helpers + ctor).
const NON_HTTP_METHODS = new Set(["constructor", "url", "request"]);

/**
 * Match a FastAPI path template (with `{param}` and catch-all `{name:path}`
 * segments) against a concrete path emitted by the client. Catch-all
 * segments consume one-or-more concrete segments while leaving enough for
 * any trailing literal/param segments (e.g. `…/source/file/{path:path}/meta`).
 */
function pathMatches(template: string, concrete: string): boolean {
  const t = template.split("/").filter(Boolean);
  const c = concrete.split("/").filter(Boolean);
  let ti = 0;
  let ci = 0;
  while (ti < t.length) {
    const seg = t[ti];
    if (seg.startsWith("{") && seg.endsWith(":path}")) {
      const trailing = t.length - ti - 1; // template segments after the catch-all
      if (c.length - ci < 1 + trailing) return false; // need ≥1 consumed + trailing
      ci = c.length - trailing; // greedily consume up to the trailing segments
      ti += 1;
      continue;
    }
    if (ci >= c.length) return false;
    if (seg.startsWith("{")) {
      ti += 1;
      ci += 1;
      continue;
    }
    if (seg !== c[ci]) return false;
    ti += 1;
    ci += 1;
  }
  return ci === c.length;
}

function hasBackendRoute(method: string, path: string): boolean {
  if (ROUTES.some((r) => r.method === method && pathMatches(r.path, path))) return true;
  return NON_BACKEND_ALLOWLIST.some((a) => a.method === method && a.path === path);
}

// ---------------------------------------------------------------------------
// Capturing fetch spy + a permissive fake Response that satisfies every
// response-handling shape the client uses (json / text / blob / SSE reader /
// 204). One fresh response per call so the SSE reader state never leaks.
// ---------------------------------------------------------------------------
type Captured = { method: string; path: string };

function makeResponse(): Response {
  const encoder = new TextEncoder();
  let read = false;
  return {
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => ({}),
    text: async () => "",
    blob: async () => new Blob([]),
    body: {
      getReader() {
        return {
          read: async () => {
            if (read) return { value: undefined, done: true };
            read = true;
            return { value: encoder.encode("data: [DONE]\n\n"), done: false };
          },
        };
      },
    },
  } as unknown as Response;
}

function installFetchSpy(): Captured[] {
  const calls: Captured[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input.toString();
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ method, path: new URL(raw, "http://contract.test").pathname });
      return makeResponse();
    }),
  );
  return calls;
}

const cfg = { runtime: { apiBaseUrl: "" } } as unknown as PlatformConfig;

// Dummy arguments — values are irrelevant to the contract (only the shape of
// the emitted path/method matters). Path-bearing args stay single-segment so
// catch-all routes match cleanly.
const P = "p1";
const RUN = "r1";
const SRC = "s1";
const CONV = "c1";
const SLUG = "sl";
const RID = "rid1";
const FILE_PATH = "doc.md";

/**
 * One invocation per HTTP-bearing client method. The key is the method name;
 * the completeness check asserts these keys cover every prototype method
 * not in NON_HTTP_METHODS. `sourceBlobUrl` returns a URL string (no fetch),
 * so it is exercised specially below and recorded as a synthetic GET.
 */
function invocations(client: ApiClient): Record<string, () => Promise<unknown> | unknown> {
  const file = new File(["x"], "a.md", { type: "text/markdown" });
  return {
    login: () => client.login("u", "p"),
    listProjects: () => client.listProjects(),
    getProject: () => client.getProject(P),
    updateProject: () => client.updateProject(P, { name: "n" }),
    getUserPreferences: () => client.getUserPreferences(),
    updateUserPreferences: () => client.updateUserPreferences({}),
    listSources: () => client.listSources(P),
    getSource: () => client.getSource(P, SRC),
    getSourceDiagnostics: () => client.getSourceDiagnostics(P, SRC),
    uploadSource: () => client.uploadSource(P, file, SRC, "text/markdown"),
    deleteSource: () => client.deleteSource(P, SRC),
    sourceBlobUrl: () => client.sourceBlobUrl(P, SRC),
    downloadSourceBlob: () => client.downloadSourceBlob(P, SRC),
    getEnrichmentConfig: () => client.getEnrichmentConfig(P),
    updateEnrichmentConfig: () =>
      client.updateEnrichmentConfig(P, { quality_tier: "minimal" } as never),
    listLlmRegistry: () => client.listLlmRegistry(),
    createLlmRegistryModel: () =>
      client.createLlmRegistryModel({ alias: "haiku", provider_id: "p1" } as never),
    updateLlmRegistryModel: () =>
      client.updateLlmRegistryModel("m1", { alias: "haiku", provider_id: "p1" } as never),
    deleteLlmRegistryModel: () => client.deleteLlmRegistryModel("m1"),
    listLlmProviders: () => client.listLlmProviders(),
    createLlmProvider: () =>
      client.createLlmProvider({ name: "P", base_url: "https://x", wire_format: "openai" }),
    updateLlmProvider: () =>
      client.updateLlmProvider("p1", { name: "P", base_url: "https://x", wire_format: "openai" }),
    putLlmProviderApiKey: () => client.putLlmProviderApiKey("p1", "sk-x"),
    deleteLlmProvider: () => client.deleteLlmProvider("p1"),
    listUserProjectAccess: () => client.listUserProjectAccess("u1"),
    listProjectConsumption: () => client.listProjectConsumption(),
    listTenantConsumption: () => client.listTenantConsumption(),
    listUserConsumption: () => client.listUserConsumption(),
    listProjectStorage: () => client.listProjectStorage(),
    listTenantStorage: () => client.listTenantStorage(),
    getProjectStorageSeries: () => client.getProjectStorageSeries("p1", "t1", "month"),
    triggerStorageSnapshot: () => client.triggerStorageSnapshot(),
    listLlmCatalog: () => client.listLlmCatalog(),
    listAvailableLlmCatalogModels: () => client.listAvailableLlmCatalogModels(),
    putLlmCatalogModel: () => client.putLlmCatalogModel("m1", { enabled: true }),
    deleteLlmCatalogModel: () => client.deleteLlmCatalogModel("m1"),
    getProjectModels: () => client.getProjectModels(P),
    setProjectModels: () => client.setProjectModels(P, ["m1"]),
    listTenants: () => client.listTenants(),
    createTenant: () => client.createTenant("t1", "T1"),
    deleteTenant: () => client.deleteTenant("t1"),
    deactivateTenant: () => client.deactivateTenant("t1"),
    reactivateTenant: () => client.reactivateTenant("t1"),
    listUsersAdmin: () => client.listUsersAdmin(),
    deactivateUser: () => client.deactivateUser("u1"),
    reactivateUser: () => client.reactivateUser("u1"),
    listAllProjects: () => client.listAllProjects(),
    activateProject: () => client.activateProject(P),
    deactivateProject: () => client.deactivateProject(P),
    archiveProject: () => client.archiveProject(P),
    getProjectMembers: () => client.getProjectMembers(P),
    grantProjectAccess: () => client.grantProjectAccess(P, "u1", "project_editor"),
    revokeProjectAccess: () => client.revokeProjectAccess(P, "u1"),
    getQuotaPolicy: () => client.getQuotaPolicy(),
    putQuotaPolicy: () => client.putQuotaPolicy([]),
    getQuotaStatus: () => client.getQuotaStatus("t1"),
    getConsumption: () => client.getConsumption(),
    getMyQuota: () => client.getMyQuota(),
    listSourceRuns: () => client.listSourceRuns(P, SRC),
    listRunArtifacts: () => client.listRunArtifacts(P, SRC, RUN),
    getRunArtifact: () => client.getRunArtifact(P, SRC, RUN, FILE_PATH),
    downloadRunArtifactsZip: () => client.downloadRunArtifactsZip(P, SRC, RUN),
    downloadChunksZip: () => client.downloadChunksZip(P, SRC),
    getChunkContent: () => client.getChunkContent(P, SRC, "ch1"),
    listConversations: () => client.listConversations(),
    createConversation: () => client.createConversation({ title: "t" }),
    getConversation: () => client.getConversation(CONV),
    deleteConversation: () => client.deleteConversation(CONV),
    updateConversation: () => client.updateConversation(CONV, { title: "t" }),
    listMessages: () => client.listMessages(CONV),
    sendMessageStream: () => client.sendMessageStream(CONV, "hi", () => {}),
    listRequirementDocuments: () => client.listRequirementDocuments(P),
    getRequirementDocument: () => client.getRequirementDocument(P, SLUG),
    listRequirementEntities: () => client.listRequirementEntities(P),
    listValidationPlugins: () => client.listValidationPlugins(),
    triggerValidationRun: () => client.triggerValidationRun({ project_id: P, domain: "code" }),
    getValidationRun: () => client.getValidationRun(RUN),
    listValidationFindings: () => client.listValidationFindings(RUN),
    getValidationFinding: () => client.getValidationFinding("f1"),
    listArtifactRuns: () => client.listArtifactRuns(P),
    getArtifactTree: () => client.getArtifactTree(P, RUN),
    getArtifactBlobText: () => client.getArtifactBlobText(P, RUN, FILE_PATH),
    downloadArtifactBlob: () => client.downloadArtifactBlob(P, RUN, FILE_PATH),
    listProjectCommits: () => client.listProjectCommits(P),
    getDocumentText: () => client.getDocumentText(P, FILE_PATH),
    getDocumentTextAtRef: () => client.getDocumentTextAtRef(P, FILE_PATH, "sha1"),
    mkdirDocument: () => client.mkdirDocument(P, "dir"),
    renameDocument: () => client.renameDocument(P, FILE_PATH, "new.md"),
    moveDocument: () => client.moveDocument(P, FILE_PATH, "dir"),
    deleteDocument: () => client.deleteDocument(P, FILE_PATH),
    createDocument: () => client.createDocument(P, FILE_PATH, ""),
    updateDocument: () => client.updateDocument(P, FILE_PATH, "body"),
    getSourceTree: () => client.getSourceTree(P, RUN),
    mkdirSource: () => client.mkdirSource(P, RUN, "dir"),
    renameSource: () => client.renameSource(P, RUN, FILE_PATH, "new.py"),
    moveSource: () => client.moveSource(P, RUN, FILE_PATH, "dir"),
    getSourceFileMeta: () => client.getSourceFileMeta(P, RUN, FILE_PATH),
    deleteSourceFile: () => client.deleteSourceFile(P, RUN, FILE_PATH),
    createOrchestratorRun: () =>
      client.createOrchestratorRun({ project_id: P, goal: "g" } as never),
    getOrchestratorRun: () => client.getOrchestratorRun(RID),
    submitOrchestratorFeedback: () =>
      client.submitOrchestratorFeedback(RID, { phase: "plan", approved: true } as never),
    resumeOrchestratorRun: () => client.resumeOrchestratorRun(RID, "retry" as never),
    readOrchestratorTrace: () => client.readOrchestratorTrace(RID),
    steerOrchestratorRun: () => client.steerOrchestratorRun(RID, { hint: "x" } as never),
  };
}

describe("API contract: every client call maps to a backend route", () => {
  it("exercises every HTTP-bearing apiClient method (completeness guard)", () => {
    const client = new ApiClient(cfg);
    const wired = new Set(Object.keys(invocations(client)));
    const proto = Object.getOwnPropertyNames(ApiClient.prototype).filter(
      (n) => !NON_HTTP_METHODS.has(n),
    );
    const missing = proto.filter((n) => !wired.has(n));
    // A new apiClient method that isn't driven here would silently escape the
    // contract guard — fail loudly so it gets wired in.
    expect(missing, `apiClient methods not exercised by the contract test: ${missing}`).toEqual([]);
  });

  it("emits no path/method the backend does not expose", async () => {
    const calls = installFetchSpy();
    const client = new ApiClient(cfg);
    const inv = invocations(client);

    for (const [name, run] of Object.entries(inv)) {
      if (name === "sourceBlobUrl") {
        // No fetch — it returns a URL string. Record it as a synthetic GET so
        // the blob route is still contract-checked.
        const url = client.sourceBlobUrl(P, SRC);
        calls.push({ method: "GET", path: new URL(url, "http://contract.test").pathname });
        continue;
      }
      // Some methods reject on the fake response shape; the contract only
      // cares about what hit fetch, so swallow post-fetch errors.
      await Promise.resolve(run()).catch(() => undefined);
    }

    vi.unstubAllGlobals();

    expect(calls.length).toBeGreaterThan(0);
    const violations = calls
      .filter((c) => !hasBackendRoute(c.method, c.path))
      .map((c) => `${c.method} ${c.path}`);
    const unique = [...new Set(violations)];
    expect(
      unique,
      `client calls with no matching backend route (drift — regenerate ` +
        `backend-routes.json or fix the client):\n${unique.join("\n")}`,
    ).toEqual([]);
  });
});
