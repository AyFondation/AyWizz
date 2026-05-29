// =============================================================================
// File: apiClient.test.ts
// Version: 4
// Path: ay_platform_ui/tests/unit/lib/apiClient.test.ts
// Description: Unit tests for the HTTP client wrapper. Covers :
//                - URL composition (relative vs absolute apiBaseUrl)
//                - Authorization header injection from localStorage
//                - login() returns the token without persisting
//                - ApiError thrown on non-2xx with status + body
//                - localStorage helpers (read/write/clear)
//                - 204 No-Content path + session-revoked 401 funnel
//                - the full method surface (projects, preferences,
//                  sources, conversations + SSE stream, requirements,
//                  validation, artifacts, live-docs/source structural
//                  ops, orchestrator runs) — URL, method, body, query
//                  and return-shape behaviour for each.
//
//              v4 (2026-05-29): broaden coverage from the login/error/
//              version-history slice to the whole ApiClient surface +
//              the request() 204 / 401-revoked branches + the
//              sendMessageStream SSE parser (token / inline / [DONE]).
//
//              v3 (2026-05-28): live-docs file-manager methods
//              (R-500-010 v2) — `getDocumentText` (latest, no ref),
//              `createDocument` (POST, blank-file allowed),
//              `updateDocument` (PUT, percent-escapes nested paths).
//
//              v2 (2026-05-21): version-history methods (R-200-147) —
//              `listProjectCommits` forwards an optional `path` filter
//              and `getDocumentTextAtRef` builds the `?ref=<sha>` read
//              URL + returns the decoded text + content type.
// =============================================================================

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  clearStoredToken,
  readStoredToken,
  setSessionRevokedHandler,
  writeStoredToken,
} from "@/lib/apiClient";
import type { InlineEvent, PlatformConfig } from "@/lib/types";

const SAME_ORIGIN_CFG: PlatformConfig = {
  runtime: { apiBaseUrl: "", publicBaseUrl: "" },
  ux: {
    api_version: "v1",
    auth_mode: "local",
    brand: {
      name: "AyWizz",
      short_name: "AY",
      accent_color_hex: "#000",
    },
    features: {
      chat_enabled: true,
      kg_enabled: true,
      cross_tenant_enabled: false,
      file_download_enabled: true,
    },
  },
};

const CROSS_ORIGIN_CFG: PlatformConfig = {
  ...SAME_ORIGIN_CFG,
  runtime: {
    apiBaseUrl: "https://api.example.com",
    publicBaseUrl: "https://app.example.com",
  },
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

describe("token storage helpers", () => {
  it("write+read round-trips a token", () => {
    writeStoredToken("token-123");
    expect(readStoredToken()).toBe("token-123");
  });

  it("read returns null when no token has been written", () => {
    expect(readStoredToken()).toBeNull();
  });

  it("clear removes the stored token", () => {
    writeStoredToken("token-456");
    clearStoredToken();
    expect(readStoredToken()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// URL composition
// ---------------------------------------------------------------------------

describe("ApiClient URL composition", () => {
  it("uses a relative URL when apiBaseUrl is empty", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          access_token: "t",
          token_type: "bearer",
          expires_in: 3600,
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.login("alice", "pw");

    expect(fetchMock).toHaveBeenCalledWith(
      "/auth/login",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("prepends apiBaseUrl when set (cross-origin deployment)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          access_token: "t",
          token_type: "bearer",
          expires_in: 3600,
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(CROSS_ORIGIN_CFG);
    await client.login("alice", "pw");

    expect(fetchMock).toHaveBeenCalledWith("https://api.example.com/auth/login", expect.anything());
  });
});

// ---------------------------------------------------------------------------
// login() — returns token, does NOT persist
// ---------------------------------------------------------------------------

describe("ApiClient.login", () => {
  it("returns the access_token from the LoginResponse body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            access_token: "jwt-abc",
            token_type: "bearer",
            expires_in: 3600,
          }),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    const token = await client.login("alice", "pw");

    expect(token).toBe("jwt-abc");
  });

  it("does NOT write to localStorage — AuthProvider owns persistence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            access_token: "jwt-abc",
            token_type: "bearer",
            expires_in: 3600,
          }),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.login("alice", "pw");

    expect(readStoredToken()).toBeNull();
  });

  it("posts a JSON body with username + password", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          access_token: "t",
          token_type: "bearer",
          expires_in: 3600,
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.login("alice", "pw-123");

    const callArgs = fetchMock.mock.calls[0];
    const init = callArgs[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ username: "alice", password: "pw-123" }));
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});

// ---------------------------------------------------------------------------
// Authorization header injection
// ---------------------------------------------------------------------------

describe("ApiClient authorization header", () => {
  it("attaches Authorization: Bearer when a token is in localStorage", async () => {
    writeStoredToken("stored-token");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          access_token: "fresh",
          token_type: "bearer",
          expires_in: 3600,
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.login("alice", "pw");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer stored-token");
  });

  it("omits Authorization when no token is stored", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          access_token: "t",
          token_type: "bearer",
          expires_in: 3600,
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.login("alice", "pw");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ApiError on non-2xx
// ---------------------------------------------------------------------------

describe("ApiClient error handling", () => {
  it("throws ApiError with status + url + body on 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        text: () => Promise.resolve('{"detail":"invalid credentials"}'),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await expect(client.login("alice", "wrong")).rejects.toBeInstanceOf(ApiError);
    try {
      await client.login("alice", "wrong");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(401);
      expect(apiErr.url).toBe("/auth/login");
      expect(apiErr.body).toContain("invalid credentials");
    }
  });

  it("includes status + body in the error message for log clarity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        text: () => Promise.resolve("service unavailable"),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await expect(client.login("alice", "pw")).rejects.toThrow(/503.*service unavailable/);
  });

  it("handles a 4xx with empty body gracefully", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        text: () => Promise.resolve(""),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await expect(client.login("a", "b")).rejects.toThrow(/empty body/);
  });
});

// ---------------------------------------------------------------------------
// Version history (R-200-147) — per-file commits + read-at-ref
// ---------------------------------------------------------------------------

describe("ApiClient version history", () => {
  it("listProjectCommits forwards the optional path filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ commits: [], page: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.listProjectCommits("proj-d", 1, "docs/intro.md");

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/v1/projects/proj-d/git/commits?page=1");
    expect(url).toContain("&path=docs%2Fintro.md");
  });

  it("listProjectCommits omits the path query when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ commits: [], page: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.listProjectCommits("proj-d");

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).not.toContain("&path=");
  });

  it("getDocumentTextAtRef builds the ?ref read URL and returns text + type", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve("# v1\n"),
      headers: new Headers({ "Content-Type": "text/markdown" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    const out = await client.getDocumentTextAtRef("proj-d", "docs/intro.md", "sha-1");

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toBe("/api/v1/projects/proj-d/documents/docs/intro.md?ref=sha-1");
    expect(out).toEqual({ text: "# v1\n", contentType: "text/markdown" });
  });

  it("getDocumentTextAtRef throws ApiError on a 404 (unknown ref)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        text: () => Promise.resolve('{"detail":"not found at ref"}'),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await expect(
      client.getDocumentTextAtRef("proj-d", "docs/intro.md", "bad"),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// Live-docs file manager (R-500-010 v2) — read latest + create + update
// ---------------------------------------------------------------------------

describe("ApiClient live-docs file manager", () => {
  it("getDocumentText reads the latest content without ?ref", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve("hello\n"),
      headers: new Headers({ "Content-Type": "text/markdown" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    const out = await client.getDocumentText("proj-d", "docs/intro.md");

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toBe("/api/v1/projects/proj-d/documents/docs/intro.md");
    expect(url).not.toContain("?ref=");
    expect(out).toEqual({ text: "hello\n", contentType: "text/markdown" });
  });

  it("getDocumentText falls back to text/plain when the server omits the type", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(""),
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    const out = await client.getDocumentText("proj-d", "blank.md");
    expect(out.contentType).toBe("text/plain");
  });

  it("createDocument POSTs the {path, content} body to /documents", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ path: "notes/a.md", size_bytes: 0, version: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    const ref = await client.createDocument("proj-d", "notes/a.md", "");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/proj-d/documents",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "notes/a.md", content: "" }),
      }),
    );
    expect(ref).toEqual({ path: "notes/a.md", size_bytes: 0, version: 1 });
  });

  it("createDocument defaults to an empty content body when omitted", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ path: "x.md", size_bytes: 0, version: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await client.createDocument("proj-d", "x.md");

    const body = (fetchMock.mock.calls[0][1] as { body: string }).body;
    expect(JSON.parse(body)).toEqual({ path: "x.md", content: "" });
  });

  it("updateDocument PUTs the new content and percent-encodes nested path segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ path: "a/b c.md", size_bytes: 5, version: 4 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient(SAME_ORIGIN_CFG);
    const ref = await client.updateDocument("proj-d", "a/b c.md", "edit\n");

    const url = fetchMock.mock.calls[0][0] as string;
    // segments are percent-encoded individually, the "/" separator survives
    expect(url).toBe("/api/v1/projects/proj-d/documents/a/b%20c.md");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({ content: "edit\n" }),
    });
    expect(ref).toEqual({ path: "a/b c.md", size_bytes: 5, version: 4 });
  });

  it("updateDocument surfaces a 409 as an ApiError (conflict on stale write)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        text: () => Promise.resolve('{"detail":"conflict"}'),
      }),
    );

    const client = new ApiClient(SAME_ORIGIN_CFG);
    await expect(client.updateDocument("proj-d", "x.md", "y")).rejects.toBeInstanceOf(ApiError);
  });
});

// ===========================================================================
// Full method surface — URL / method / body / query / return shape.
// ===========================================================================

/** A fetch mock resolving a JSON body (json() + text() + empty headers). */
function fetchJson(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(typeof body === "string" ? body : JSON.stringify(body)),
    headers: new Headers(),
  });
}

/** A fetch mock resolving raw text + an optional Content-Type. */
function fetchText(text: string, contentType?: string, status = 200) {
  const headers = new Headers();
  if (contentType) headers.set("Content-Type", contentType);
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    text: () => Promise.resolve(text),
    blob: () => Promise.resolve(new Blob([text])),
    headers,
  });
}

/** A fetch mock resolving a Blob + an optional Content-Disposition. */
function fetchBlob(text: string, disposition?: string, status = 200) {
  const headers = new Headers();
  if (disposition) headers.set("Content-Disposition", disposition);
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    blob: () => Promise.resolve(new Blob([text])),
    text: () => Promise.resolve(text),
    headers,
  });
}

function errResp(status: number, body = "err") {
  return vi.fn().mockResolvedValue({
    ok: false,
    status,
    text: () => Promise.resolve(body),
  });
}

const lastUrl = (m: ReturnType<typeof vi.fn>) => m.mock.calls[0][0] as string;
const lastInit = (m: ReturnType<typeof vi.fn>) => m.mock.calls[0][1] as RequestInit;
const client = () => new ApiClient(SAME_ORIGIN_CFG);

// ---------------------------------------------------------------------------
// request() shared branches — 204 + session-revoked 401 funnel
// ---------------------------------------------------------------------------

describe("request() shared behaviour", () => {
  afterEach(() => setSessionRevokedHandler(null));

  it("returns undefined (not a JSON parse) on 204 No-Content", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: () => Promise.reject(new Error("must not parse a 204 body")),
      text: () => Promise.resolve(""),
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(client().deleteConversation("c1")).resolves.toBeUndefined();
  });

  it("fires the session-revoked handler once on a 401 with a stored token", async () => {
    writeStoredToken("tok");
    const handler = vi.fn();
    setSessionRevokedHandler(handler);
    vi.stubGlobal("fetch", errResp(401, '{"detail":"expired"}'));
    await expect(client().listProjects()).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire the handler on a 401 without a token (login failure path)", async () => {
    const handler = vi.fn();
    setSessionRevokedHandler(handler);
    vi.stubGlobal("fetch", errResp(401));
    await expect(client().login("a", "b")).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Projects + preferences
// ---------------------------------------------------------------------------

describe("ApiClient projects + preferences", () => {
  it("listProjects GETs /api/v1/projects", async () => {
    const m = fetchJson({ projects: [] });
    vi.stubGlobal("fetch", m);
    await client().listProjects();
    expect(lastUrl(m)).toBe("/api/v1/projects");
    expect(lastInit(m).method).toBe("GET");
  });

  it("getProject encodes the project id", async () => {
    const m = fetchJson({ project_id: "p/1" });
    vi.stubGlobal("fetch", m);
    await client().getProject("p/1");
    expect(lastUrl(m)).toBe("/api/v1/projects/p%2F1");
  });

  it("updateProject PATCHes the payload", async () => {
    const m = fetchJson({ project_id: "p1", name: "New" });
    vi.stubGlobal("fetch", m);
    await client().updateProject("p1", { name: "New" });
    expect(lastInit(m).method).toBe("PATCH");
    expect(lastInit(m).body).toBe(JSON.stringify({ name: "New" }));
  });

  it("getUserPreferences GETs the self endpoint", async () => {
    const m = fetchJson({ trigram: null });
    vi.stubGlobal("fetch", m);
    await client().getUserPreferences();
    expect(lastUrl(m)).toBe("/api/v1/users/me/preferences");
  });

  it("updateUserPreferences PUTs the payload", async () => {
    const m = fetchJson({ trigram: "abc" });
    vi.stubGlobal("fetch", m);
    await client().updateUserPreferences({ trigram: "abc" });
    expect(lastInit(m).method).toBe("PUT");
    expect(lastInit(m).body).toBe(JSON.stringify({ trigram: "abc" }));
  });
});

// ---------------------------------------------------------------------------
// C7 — Sources
// ---------------------------------------------------------------------------

describe("ApiClient sources", () => {
  it("listSources / getSource build the project-scoped URLs", async () => {
    const m = fetchJson({ sources: [] });
    vi.stubGlobal("fetch", m);
    await client().listSources("p1");
    expect(lastUrl(m)).toBe("/api/v1/memory/projects/p1/sources");

    const m2 = fetchJson({ source_id: "s1" });
    vi.stubGlobal("fetch", m2);
    await client().getSource("p1", "s1");
    expect(lastUrl(m2)).toBe("/api/v1/memory/projects/p1/sources/s1");
  });

  it("uploadSource POSTs a FormData body (no forced JSON Content-Type)", async () => {
    const m = fetchJson({ source_id: "s1" });
    vi.stubGlobal("fetch", m);
    const file = new File(["hello"], "doc.txt", { type: "text/plain" });
    await client().uploadSource("p1", file, "s1", "text/plain");
    const init = lastInit(m);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect(new Headers(init.headers).get("Content-Type")).toBeNull();
    expect(lastUrl(m)).toBe("/api/v1/memory/projects/p1/sources/upload");
  });

  it("deleteSource issues a DELETE", async () => {
    const m = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      text: () => Promise.resolve(""),
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", m);
    await client().deleteSource("p1", "s1");
    expect(lastInit(m).method).toBe("DELETE");
  });

  it("sourceBlobUrl composes a URL without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const url = client().sourceBlobUrl("p1", "s1");
    expect(url).toBe("/api/v1/memory/projects/p1/sources/s1/blob");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("downloadSourceBlob returns the blob + parses the Content-Disposition filename", async () => {
    const m = fetchBlob("bytes", 'attachment; filename="report.pdf"');
    vi.stubGlobal("fetch", m);
    const out = await client().downloadSourceBlob("p1", "s1");
    expect(out.filename).toBe("report.pdf");
    expect(out.blob).toBeInstanceOf(Blob);
  });

  it("downloadSourceBlob returns a null filename when no header is present", async () => {
    vi.stubGlobal("fetch", fetchBlob("bytes"));
    const out = await client().downloadSourceBlob("p1", "s1");
    expect(out.filename).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// C3 — Conversations + SSE stream
// ---------------------------------------------------------------------------

describe("ApiClient conversations", () => {
  it("listConversations / listMessages GET the right URLs", async () => {
    const m = fetchJson({ conversations: [] });
    vi.stubGlobal("fetch", m);
    await client().listConversations();
    expect(lastUrl(m)).toBe("/api/v1/conversations");

    const m2 = fetchJson({ messages: [] });
    vi.stubGlobal("fetch", m2);
    await client().listMessages("c1");
    expect(lastUrl(m2)).toBe("/api/v1/conversations/c1/messages");
  });

  it("createConversation unwraps the {conversation} envelope", async () => {
    const m = fetchJson({ conversation: { conversation_id: "c1", title: "T" } });
    vi.stubGlobal("fetch", m);
    const conv = await client().createConversation({ title: "T" });
    expect(lastInit(m).method).toBe("POST");
    expect(conv).toEqual({ conversation_id: "c1", title: "T" });
  });

  it("getConversation / updateConversation unwrap the envelope", async () => {
    const m = fetchJson({ conversation: { conversation_id: "c1" } });
    vi.stubGlobal("fetch", m);
    await client().getConversation("c1");
    expect(lastUrl(m)).toBe("/api/v1/conversations/c1");

    const m2 = fetchJson({ conversation: { conversation_id: "c1", title: "renamed" } });
    vi.stubGlobal("fetch", m2);
    const conv = await client().updateConversation("c1", { title: "renamed" });
    expect(lastInit(m2).method).toBe("PATCH");
    expect(conv.title).toBe("renamed");
  });

  it("deleteConversation issues a DELETE", async () => {
    const m = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      text: () => Promise.resolve(""),
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", m);
    await client().deleteConversation("c1");
    expect(lastInit(m).method).toBe("DELETE");
  });
});

describe("ApiClient.sendMessageStream (SSE parser)", () => {
  /** Build a fetch mock whose body streams the given SSE text blocks. */
  function sseFetch(blocks: string[], status = 200) {
    const encoder = new TextEncoder();
    const queue = blocks.map((b) => encoder.encode(b));
    let i = 0;
    const reader = {
      read: () =>
        i < queue.length
          ? Promise.resolve({ value: queue[i++], done: false })
          : Promise.resolve({ value: undefined, done: true }),
    };
    return vi.fn().mockResolvedValue({
      ok: status < 400,
      status,
      text: () => Promise.resolve(""),
      body: { getReader: () => reader },
    });
  }

  it("dispatches token chunks to onChunk and stops on [DONE]", async () => {
    vi.stubGlobal("fetch", sseFetch(["data: Hello\n\n", "data: world\n\n", "data: [DONE]\n\n"]));
    const chunks: string[] = [];
    await client().sendMessageStream("c1", "hi", (c) => chunks.push(c));
    expect(chunks).toEqual(["Hello", "world"]);
  });

  it("routes `event: inline` payloads to onInlineEvent, not onChunk", async () => {
    vi.stubGlobal(
      "fetch",
      sseFetch([
        'event: inline\ndata: {"kind":"stage","label":"retrieve"}\n\n',
        "data: token\n\n",
        "data: [DONE]\n\n",
      ]),
    );
    const chunks: string[] = [];
    const inline: InlineEvent[] = [];
    await client().sendMessageStream("c1", "hi", (c) => chunks.push(c), {
      onInlineEvent: (e) => inline.push(e),
    });
    expect(chunks).toEqual(["token"]);
    expect(inline).toEqual([{ kind: "stage", label: "retrieve" }]);
  });

  it("forwards optional prompts + references in the request body", async () => {
    const m = sseFetch(["data: [DONE]\n\n"]);
    vi.stubGlobal("fetch", m);
    await client().sendMessageStream("c1", "hi", () => {}, {
      userPrompt: "be terse",
      projectPrompt: "",
      references: [{ kind: "source", id: "s1" } as unknown as never],
    });
    const body = JSON.parse((lastInit(m) as { body: string }).body);
    expect(body.content).toBe("hi");
    expect(body.user_prompt).toBe("be terse");
    expect(body).not.toHaveProperty("project_prompt"); // empty string is dropped
    expect(body.references).toHaveLength(1);
  });

  it("throws ApiError + funnels a 401-with-token through the revoked handler", async () => {
    writeStoredToken("tok");
    const handler = vi.fn();
    setSessionRevokedHandler(handler);
    vi.stubGlobal("fetch", errResp(401));
    await expect(client().sendMessageStream("c1", "hi", () => {})).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    setSessionRevokedHandler(null);
  });
});

// ---------------------------------------------------------------------------
// C5 — Requirements (read-only)
// ---------------------------------------------------------------------------

describe("ApiClient requirements", () => {
  it("listRequirementDocuments / getRequirementDocument / listRequirementEntities", async () => {
    const m = fetchJson({ documents: [] });
    vi.stubGlobal("fetch", m);
    await client().listRequirementDocuments("p1");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/requirements/documents");

    const m2 = fetchJson({ slug: "100-SPEC" });
    vi.stubGlobal("fetch", m2);
    await client().getRequirementDocument("p1", "100-SPEC");
    expect(lastUrl(m2)).toBe("/api/v1/projects/p1/requirements/documents/100-SPEC");

    const m3 = fetchJson({ entities: [] });
    vi.stubGlobal("fetch", m3);
    await client().listRequirementEntities("p1");
    expect(lastUrl(m3)).toBe("/api/v1/projects/p1/requirements/entities");
  });
});

// ---------------------------------------------------------------------------
// C6 — Validation
// ---------------------------------------------------------------------------

describe("ApiClient validation", () => {
  it("listValidationPlugins GETs the plugins list", async () => {
    const m = fetchJson([]);
    vi.stubGlobal("fetch", m);
    await client().listValidationPlugins();
    expect(lastUrl(m)).toBe("/api/v1/validation/plugins");
  });

  it("triggerValidationRun POSTs the payload", async () => {
    const m = fetchJson({ run_id: "r1" });
    vi.stubGlobal("fetch", m);
    const out = await client().triggerValidationRun({ project_id: "p1", domain: "code" });
    expect(lastInit(m).method).toBe("POST");
    expect(out.run_id).toBe("r1");
  });

  it("getValidationRun / getValidationFinding build the right URLs", async () => {
    const m = fetchJson({ run_id: "r1" });
    vi.stubGlobal("fetch", m);
    await client().getValidationRun("r1");
    expect(lastUrl(m)).toBe("/api/v1/validation/runs/r1");

    const m2 = fetchJson({ finding_id: "f1" });
    vi.stubGlobal("fetch", m2);
    await client().getValidationFinding("f1");
    expect(lastUrl(m2)).toBe("/api/v1/validation/findings/f1");
  });

  it("listValidationFindings forwards limit + offset query params", async () => {
    const m = fetchJson({ findings: [], total: 0 });
    vi.stubGlobal("fetch", m);
    await client().listValidationFindings("r1", 25, 50);
    expect(lastUrl(m)).toBe("/api/v1/validation/runs/r1/findings?limit=25&offset=50");
  });
});

// ---------------------------------------------------------------------------
// C4 — Artifacts (runs / tree / blob text + download)
// ---------------------------------------------------------------------------

describe("ApiClient artifacts", () => {
  it("listArtifactRuns / getArtifactTree build run-scoped URLs", async () => {
    const m = fetchJson({ runs: [] });
    vi.stubGlobal("fetch", m);
    await client().listArtifactRuns("p1");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/artifacts/runs");

    const m2 = fetchJson({ nodes: [] });
    vi.stubGlobal("fetch", m2);
    await client().getArtifactTree("p1", "r1");
    expect(lastUrl(m2)).toBe("/api/v1/projects/p1/artifacts/runs/r1/tree");
  });

  it("getArtifactBlobText passes the path query and returns text + contentType", async () => {
    const m = fetchText("print('x')\n", "text/x-python");
    vi.stubGlobal("fetch", m);
    const out = await client().getArtifactBlobText("p1", "r1", "src/a.py");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/artifacts/runs/r1/blob?path=src%2Fa.py");
    expect(out).toEqual({ text: "print('x')\n", contentType: "text/x-python" });
  });

  it("downloadArtifactBlob adds &download=1 and falls back to the basename filename", async () => {
    const m = fetchBlob("bytes"); // no Content-Disposition → fallback
    vi.stubGlobal("fetch", m);
    const out = await client().downloadArtifactBlob("p1", "r1", "dir/out.bin");
    expect(lastUrl(m)).toContain("&download=1");
    expect(out.filename).toBe("out.bin");
  });
});

// ---------------------------------------------------------------------------
// C4 — Live-docs structural ops (mkdir / rename / move / delete)
// ---------------------------------------------------------------------------

describe("ApiClient live-docs structural ops", () => {
  it("mkdirDocument / renameDocument / moveDocument POST their bodies", async () => {
    const m = fetchJson({ ok: true });
    vi.stubGlobal("fetch", m);
    await client().mkdirDocument("p1", "docs/new");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/documents/mkdir");
    expect(JSON.parse((lastInit(m) as { body: string }).body)).toEqual({ path: "docs/new" });

    const m2 = fetchJson({ ok: true });
    vi.stubGlobal("fetch", m2);
    await client().renameDocument("p1", "a.md", "b.md");
    expect(JSON.parse((lastInit(m2) as { body: string }).body)).toEqual({
      from_path: "a.md",
      to_path: "b.md",
    });

    const m3 = fetchJson({ ok: true });
    vi.stubGlobal("fetch", m3);
    await client().moveDocument("p1", "a.md", "sub");
    expect(JSON.parse((lastInit(m3) as { body: string }).body)).toEqual({
      from_path: "a.md",
      to_dir: "sub",
    });
  });

  it("deleteDocument percent-encodes each path segment", async () => {
    const m = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      text: () => Promise.resolve(""),
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", m);
    await client().deleteDocument("p1", "a/b c.md");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/documents/a/b%20c.md");
    expect(lastInit(m).method).toBe("DELETE");
  });
});

// ---------------------------------------------------------------------------
// C4 — Source-files surface (tree + structural ops + meta, run-scoped)
// ---------------------------------------------------------------------------

describe("ApiClient source-files surface", () => {
  it("getSourceTree passes the run_id query", async () => {
    const m = fetchJson({ nodes: [] });
    vi.stubGlobal("fetch", m);
    await client().getSourceTree("p1", "r1");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/source/tree?run_id=r1");
  });

  it("mkdirSource / renameSource / moveSource POST run-scoped with bodies", async () => {
    const m = fetchJson({ ok: true });
    vi.stubGlobal("fetch", m);
    await client().mkdirSource("p1", "r1", "pkg");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/source/mkdir?run_id=r1");

    const m2 = fetchJson({ ok: true });
    vi.stubGlobal("fetch", m2);
    await client().renameSource("p1", "r1", "a.py", "b.py");
    expect(JSON.parse((lastInit(m2) as { body: string }).body)).toEqual({
      from_path: "a.py",
      to_path: "b.py",
    });

    const m3 = fetchJson({ ok: true });
    vi.stubGlobal("fetch", m3);
    await client().moveSource("p1", "r1", "a.py", "pkg");
    expect(JSON.parse((lastInit(m3) as { body: string }).body)).toEqual({
      from_path: "a.py",
      to_dir: "pkg",
    });
  });

  it("getSourceFileMeta encodes the path + adds run_id", async () => {
    const m = fetchJson({ path: "src/a.py" });
    vi.stubGlobal("fetch", m);
    await client().getSourceFileMeta("p1", "r1", "src/a.py");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/source/file/src/a.py/meta?run_id=r1");
  });

  it("deleteSourceFile DELETEs the run-scoped, encoded path", async () => {
    const m = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      text: () => Promise.resolve(""),
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", m);
    await client().deleteSourceFile("p1", "r1", "src/a.py");
    expect(lastUrl(m)).toBe("/api/v1/projects/p1/source/file/src/a.py?run_id=r1");
    expect(lastInit(m).method).toBe("DELETE");
  });
});

// ---------------------------------------------------------------------------
// C4 — Orchestrator runs
// ---------------------------------------------------------------------------

describe("ApiClient orchestrator runs", () => {
  it("createOrchestratorRun / getOrchestratorRun", async () => {
    const m = fetchJson({ run_id: "r1", state: "RUNNING" });
    vi.stubGlobal("fetch", m);
    await client().createOrchestratorRun({ project_id: "p1", goal: "g" } as never);
    expect(lastUrl(m)).toBe("/api/v1/orchestrator/runs");
    expect(lastInit(m).method).toBe("POST");

    const m2 = fetchJson({ run_id: "r1", state: "RUNNING" });
    vi.stubGlobal("fetch", m2);
    await client().getOrchestratorRun("r1");
    expect(lastUrl(m2)).toBe("/api/v1/orchestrator/runs/r1");
  });

  it("submitOrchestratorFeedback / resumeOrchestratorRun / steerOrchestratorRun POST bodies", async () => {
    const m = fetchJson({ run_id: "r1" });
    vi.stubGlobal("fetch", m);
    await client().submitOrchestratorFeedback("r1", { phase: "plan", approved: true } as never);
    expect(lastUrl(m)).toBe("/api/v1/orchestrator/runs/r1/feedback");

    const m2 = fetchJson({ run_id: "r1" });
    vi.stubGlobal("fetch", m2);
    await client().resumeOrchestratorRun("r1", "retry" as never);
    expect(JSON.parse((lastInit(m2) as { body: string }).body)).toEqual({ strategy: "retry" });

    const m3 = fetchJson({ run_id: "r1" });
    vi.stubGlobal("fetch", m3);
    await client().steerOrchestratorRun("r1", { hint: "go faster" } as never);
    expect(lastUrl(m3)).toBe("/api/v1/orchestrator/runs/r1/steer");
  });

  it("readOrchestratorTrace forwards before + limit query params", async () => {
    const m = fetchJson([]);
    vi.stubGlobal("fetch", m);
    await client().readOrchestratorTrace("r1", { before: "2026-01-01T00:00:00Z", limit: 50 });
    const url = lastUrl(m);
    expect(url).toContain("/api/v1/orchestrator/runs/r1/trace?");
    expect(url).toContain("before=2026-01-01T00%3A00%3A00Z");
    expect(url).toContain("limit=50");
  });

  it("readOrchestratorTrace omits the query string when no options are given", async () => {
    const m = fetchJson([]);
    vi.stubGlobal("fetch", m);
    await client().readOrchestratorTrace("r1");
    expect(lastUrl(m)).toBe("/api/v1/orchestrator/runs/r1/trace");
  });
});
