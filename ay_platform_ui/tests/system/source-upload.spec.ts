// =============================================================================
// File: source-upload.spec.ts
// Version: 3
// Path: ay_platform_ui/tests/system/source-upload.spec.ts
//
// v3 (2026-06-01, R-100-081 v3): the upload is now a multipart POST to C7
// `/sources/upload` (C7 owns byte custody), asserting 202 + a pending
// SourcePublic — NOT the old C12 webhook with base64-in-JSON.
// Description: System-tier E2E — exercises the D-020 source-upload ingestion
//              pipeline end-to-end against a REAL running stack
//              (Traefik → C12 n8n webhook → C13 AyExtractor → C7). NOT mocked.
//
//              This is the system-tier counterpart of the contract test
//              (tests/contract/api-surface.test.ts) for the bug that started
//              this work : the Sources upload returned HTTP 405 because the
//              client POSTed to a C7 endpoint D-020 had removed. Here we POST
//              the ingestion webhook EXACTLY as `lib/apiClient.uploadSource`
//              does (JSON body, file as base64) and assert the route is wired.
//
//              Pre-requisite : the operator has brought the stack up with the
//              `test` profile so mock_llm + c13-extractor run, e.g.
//                  ay_platform_core/scripts/e2e_stack.sh full
//              (v9 passes `--profile test`; `cmd_seed` provisions `alice`).
//
//              ASSERTION SCOPE (deliberate, honest about what a mock-LLM
//              stack can prove) :
//                (1) the upload webhook is ROUTED and accepts POST — never
//                    404 (route gone) / 405 (wrong method, the original
//                    symptom).
//                (2) the n8n workflow does NOT CRASH — status < 500. A 5xx
//                    (`{"message":"Error in workflow"}`) means a node threw
//                    (unseeded MinIO credential, duplicate webhook, C13
//                    unreachable, …) — the pipeline is broken end-to-end.
//                    v1 missed this : it only checked 404/405 and SKIPPED
//                    assertions on non-2xx, so a 500 passed. v2 makes < 500
//                    a hard assertion (route-works-but-pipeline-crashes is
//                    the exact class the operator asked an e2e test to catch).
//                (3) the response is a terminal {status}/{accepted} envelope
//                    (the workflow ran to its Respond node). We do NOT assert
//                    `completed` : the mock LLM exposes no `/v1/embeddings`,
//                    so a clean run ends `failed` (still HTTP 200 via the
//                    Respond node). Real extraction needs the `litellm`
//                    profile + a real key (operator decision 2026-05-29).
// =============================================================================

import { expect, test } from "@playwright/test";

// Credentials + project provisioned by `e2e_stack.sh` → `seed_e2e.py`
// (ADMIN_USER=alice / seed-password, global `admin`; DEMO_PROJECT=demo).
const ADMIN_USER = "alice";
const ADMIN_PASSWORD = "seed-password";
const DEMO_PROJECT = "demo";

test.describe("Source upload pipeline (real stack, D-020)", () => {
  test("upload webhook is routed and accepts POST (regression: the 405 bug)", async ({
    request,
  }) => {
    // 1. Real login against C2 (no MSW). seed_e2e provisions `alice`.
    const loginResp = await request.post("/auth/login", {
      data: { username: ADMIN_USER, password: ADMIN_PASSWORD },
    });
    expect(
      loginResp.ok(),
      `login failed (${loginResp.status()}) — is the stack up + seeded? ` +
        `run: ay_platform_core/scripts/e2e_stack.sh full`,
    ).toBeTruthy();
    const { access_token: token } = (await loginResp.json()) as { access_token: string };
    expect(token, "no access_token in /auth/login response").toBeTruthy();

    // 2. POST the file as multipart to C7 /sources/upload — EXACTLY as
    //    lib/apiClient.uploadSource does (R-100-081 v3: C7 owns byte custody;
    //    it stores the raw bytes + triggers the C12 workflow async, returns
    //    202). The tenant is taken server-side from the forward-auth header.
    const sourceId = `sys-upload-${Date.now()}`;
    const resp = await request.post(`/api/v1/memory/projects/${DEMO_PROJECT}/sources/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: "system-test.md",
          mimeType: "text/markdown",
          buffer: Buffer.from("# system test\nhello from the upload pipeline\n", "utf-8"),
        },
        source_id: sourceId,
        mime_type: "text/markdown",
        format: "md",
      },
      timeout: 30_000,
    });

    const status = resp.status();
    const bodyText = await resp.text().catch(() => "");

    // (1) The route exists and accepts POST. A 404 (route gone) or 405
    // (the original symptom: client hit a route the backend did not expose
    // for POST) MUST never recur.
    expect(
      [404, 405],
      `C7 /sources/upload returned ${status} — 404/405 means the D-020 ` +
        `ingestion route is broken (the original bug). Body: ${bodyText}`,
    ).not.toContain(status);

    // (2) C7 must not CRASH (5xx). C7 stores the raw bytes and triggers the
    // C12 workflow async, then returns 202. A 5xx means C7 failed to store
    // or could not reach C12 (it returns 502 in that case) — the pipeline is
    // broken. This is the assertion that catches a working-route-but-broken-
    // pipeline (the gap an earlier version missed by only checking 404/405).
    expect(
      status,
      `C7 /sources/upload returned ${status} — C7 failed (5xx). Body: ${bodyText}`,
    ).toBeLessThan(500);

    // (3) A successful upload is 202 Accepted with a `pending` SourcePublic
    // (R-100-081 v3 — the heavy extraction runs asynchronously). We assert
    // the SHAPE (C7 accepted + recorded the source), not the async outcome.
    expect(status, `expected 202 Accepted, got ${status}: ${bodyText}`).toBe(202);
    let body: { source_id?: unknown; parse_status?: unknown } = {};
    try {
      body = JSON.parse(bodyText || "{}");
    } catch {
      /* asserted below */
    }
    expect(
      body.source_id === sourceId && typeof body.parse_status === "string",
      `expected a pending SourcePublic for ${sourceId}, got: ${bodyText}`,
    ).toBeTruthy();
  });
});
