// =============================================================================
// File: auth.test.ts
// Version: 2
// Path: ay_platform_ui/tests/unit/lib/auth.test.ts
// Description: Unit tests for `lib/auth.ts` — JWT decoding helpers
//              + redirect sanitisation.
//
//              The decoder consumes tokens produced by C2 (HS256) ;
//              we don't import the C2 signing key (verification is
//              server-side only). Tests fabricate tokens by base64-
//              encoding a fixture payload to assert the parsing path
//              without spinning up C2.
//
//              v2 (2026-04-29) : adds the `sanitizeRedirect` suite
//              that covers the open-redirect defence used by the
//              "preserve location across re-auth" UX feature.
// =============================================================================

import { describe, expect, it } from "vitest";

import { decodeJWT, isTokenExpired, type JWTClaims, sanitizeRedirect } from "@/lib/auth";

/** Encode an object as a JWT-shaped payload segment.
 *  JWT = header.payload.signature ; we hard-code a fake header +
 *  signature since the decoder treats them as opaque. */
function makeJWT(claims: Record<string, unknown>): string {
  const header = { alg: "HS256", typ: "JWT" };
  const enc = (obj: unknown) =>
    Buffer.from(JSON.stringify(obj))
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${enc(header)}.${enc(claims)}.fake-signature`;
}

const NOW_SEC = 1_700_000_000;

describe("decodeJWT", () => {
  it("returns the payload claims for a well-formed token", () => {
    const token = makeJWT({
      sub: "user-1",
      username: "alice",
      tenant_id: "tenant-x",
      roles: ["project_editor", "admin"],
      exp: NOW_SEC + 3600,
      iat: NOW_SEC,
    });

    const claims = decodeJWT(token);

    expect(claims).not.toBeNull();
    expect(claims?.sub).toBe("user-1");
    expect(claims?.username).toBe("alice");
    expect(claims?.tenant_id).toBe("tenant-x");
    expect(claims?.roles).toEqual(["project_editor", "admin"]);
    expect(claims?.exp).toBe(NOW_SEC + 3600);
  });

  it("preserves arbitrary extra claims via the index signature", () => {
    const token = makeJWT({
      sub: "u",
      exp: NOW_SEC + 60,
      custom_field: { nested: 42 },
    });
    const claims = decodeJWT(token);
    expect(claims?.custom_field).toEqual({ nested: 42 });
  });

  it("returns null for a token without 3 dot-separated segments", () => {
    expect(decodeJWT("not.a.valid.jwt")).toBeNull();
    expect(decodeJWT("only-one-segment")).toBeNull();
    expect(decodeJWT("two.segments")).toBeNull();
    expect(decodeJWT("")).toBeNull();
  });

  it("returns null when the payload is not valid base64", () => {
    expect(decodeJWT("header.@@@invalid@@@.sig")).toBeNull();
  });

  it("returns null when the payload is not valid JSON", () => {
    const garbagePayload = Buffer.from("not-json-at-all").toString("base64").replace(/=+$/, "");
    expect(decodeJWT(`header.${garbagePayload}.sig`)).toBeNull();
  });

  it("returns null when required claims (`sub`, `exp`) are missing", () => {
    const noSub = makeJWT({ exp: NOW_SEC + 60 });
    const noExp = makeJWT({ sub: "u" });
    expect(decodeJWT(noSub)).toBeNull();
    expect(decodeJWT(noExp)).toBeNull();
  });

  it("returns null when `sub` is non-string or `exp` is non-number", () => {
    const wrongTypes = makeJWT({ sub: 42, exp: "soon" });
    expect(decodeJWT(wrongTypes)).toBeNull();
  });

  it("returns null when the payload is null/array/primitive", () => {
    const nullPayload = Buffer.from("null").toString("base64").replace(/=+$/, "");
    expect(decodeJWT(`h.${nullPayload}.s`)).toBeNull();

    const arrayPayload = Buffer.from("[1,2,3]").toString("base64").replace(/=+$/, "");
    expect(decodeJWT(`h.${arrayPayload}.s`)).toBeNull();
  });
});

describe("isTokenExpired", () => {
  const baseClaims: JWTClaims = {
    sub: "u",
    exp: NOW_SEC + 3600,
  };

  it("returns false for a token expiring well in the future", () => {
    expect(isTokenExpired(baseClaims, NOW_SEC)).toBe(false);
  });

  it("returns true for a token whose `exp` is already in the past", () => {
    const expired: JWTClaims = { sub: "u", exp: NOW_SEC - 60 };
    expect(isTokenExpired(expired, NOW_SEC)).toBe(true);
  });

  it("treats a token expiring within 30s as already expired (skew budget)", () => {
    // exp = now + 10s : within the 30s skew, considered expired
    const nearExpiry: JWTClaims = { sub: "u", exp: NOW_SEC + 10 };
    expect(isTokenExpired(nearExpiry, NOW_SEC)).toBe(true);
  });

  it("considers a token valid when exp is more than 30s away", () => {
    const safe: JWTClaims = { sub: "u", exp: NOW_SEC + 31 };
    expect(isTokenExpired(safe, NOW_SEC)).toBe(false);
  });

  it("uses the current time when no `nowSec` argument is passed", () => {
    // Build a token expiring 10 minutes from now.
    const claims: JWTClaims = {
      sub: "u",
      exp: Math.floor(Date.now() / 1000) + 600,
    };
    expect(isTokenExpired(claims)).toBe(false);
  });
});

describe("sanitizeRedirect", () => {
  it("accepts a simple absolute path starting with a single `/`", () => {
    expect(sanitizeRedirect("/dashboard")).toBe("/dashboard");
  });

  it("preserves query string and hash on a valid path", () => {
    expect(sanitizeRedirect("/projects/abc?tab=members#scroll-1")).toBe(
      "/projects/abc?tab=members#scroll-1",
    );
  });

  it("rejects null / undefined / empty string", () => {
    expect(sanitizeRedirect(null)).toBeNull();
    expect(sanitizeRedirect(undefined)).toBeNull();
    expect(sanitizeRedirect("")).toBeNull();
  });

  it("rejects protocol-relative URLs (`//evil.com/path`)", () => {
    // The classic open-redirect vector — `//evil.com` resolves to the
    // current scheme + a different host.
    expect(sanitizeRedirect("//evil.com")).toBeNull();
    expect(sanitizeRedirect("//evil.com/dashboard")).toBeNull();
  });

  it("rejects absolute URLs with explicit schemes", () => {
    expect(sanitizeRedirect("http://evil.com/dashboard")).toBeNull();
    expect(sanitizeRedirect("https://evil.com/dashboard")).toBeNull();
    expect(sanitizeRedirect("javascript:alert(1)")).toBeNull();
    expect(sanitizeRedirect("data:text/html,<script>")).toBeNull();
  });

  it("rejects backslash quirks (`/\\evil.com`) per legacy parser bugs", () => {
    // Some browsers historically resolved `/\evil.com` cross-origin.
    expect(sanitizeRedirect("/\\evil.com")).toBeNull();
    expect(sanitizeRedirect("/\\")).toBeNull();
  });

  it("rejects relative paths without the leading `/`", () => {
    expect(sanitizeRedirect("dashboard")).toBeNull();
    expect(sanitizeRedirect("./dashboard")).toBeNull();
    expect(sanitizeRedirect("../admin")).toBeNull();
  });

  it("rejects non-string inputs (defensive against runtime types)", () => {
    // Forced via `as unknown as string` because the type system would
    // reject this at compile time, but runtime data from URL params is
    // always string-or-null in practice — this guards a hypothetical
    // misuse.
    expect(sanitizeRedirect(42 as unknown as string)).toBeNull();
    expect(sanitizeRedirect({} as unknown as string)).toBeNull();
  });
});
