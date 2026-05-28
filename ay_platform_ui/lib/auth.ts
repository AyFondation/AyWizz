// =============================================================================
// File: auth.ts
// Version: 2
// Path: ay_platform_ui/lib/auth.ts
// Description: JWT decoding helpers for the client. The token is signed
//              by C2 server-side ; the client decodes (without
//              verifying the signature — that's enforced server-side
//              on every protected request) to read the claims it needs
//              to render UI affordances : username, tenant_id, roles,
//              expiration timestamp.
//
//              v2 (2026-04-29) : adds `sanitizeRedirect()` for the
//              "preserve location across re-auth" UX feature
//              (?redirect=<path> on /login). Rejects external URLs
//              to prevent open-redirect attacks.
//
//              Decoding manuel base64-url plutôt que la dep `jwt-decode`
//              (~1 KB de code, zero deps).
// =============================================================================

/** Subset of the C2 JWT claims the UI cares about. C2 may include more
 *  fields ; we type only what we read so a server-side claim addition
 *  doesn't require a UI change. */
export interface JWTClaims {
  sub: string; // user_id (subject)
  username?: string;
  tenant_id?: string;
  roles?: string[];
  exp: number; // unix timestamp seconds
  iat?: number; // issued-at unix timestamp
  [key: string]: unknown; // forward-compat for additional server claims
}

/** Decode a JWT payload (the second segment between dots). Returns
 *  null for any malformed input — the caller treats null the same as
 *  "no usable token" rather than crashing. */
export function decodeJWT(token: string): JWTClaims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    // JWT uses base64url ; convert to standard base64 + pad before atob.
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const json =
      typeof window !== "undefined" && window.atob
        ? window.atob(padded)
        : Buffer.from(padded, "base64").toString("utf-8");
    const obj = JSON.parse(json) as unknown;
    if (!obj || typeof obj !== "object") return null;
    const claims = obj as Record<string, unknown>;
    if (typeof claims.sub !== "string" || typeof claims.exp !== "number") {
      // Required fields per C2's JWTClaims schema.
      return null;
    }
    return claims as unknown as JWTClaims;
  } catch {
    return null;
  }
}

/** True iff the JWT's exp claim has already passed. The 30s skew
 *  budget compensates for clock drift between client and server —
 *  we treat tokens as expired SLIGHTLY EARLY rather than risk a 401
 *  on the next request. */
export function isTokenExpired(
  claims: JWTClaims,
  nowSec: number = Math.floor(Date.now() / 1000),
): boolean {
  const skewSec = 30;
  return claims.exp < nowSec + skewSec;
}

/** Validate a redirect target so a malicious `?redirect=...` value
 *  can't bounce the user to an external phishing page after login.
 *
 *  Allows :  paths starting with a single `/` (e.g. `/dashboard`,
 *            `/projects/abc?tab=members#scroll-1`).
 *  Rejects : empty / null / undefined ; protocol-relative
 *            (`//evil.com`) ; absolute URLs (`http://...`,
 *            `javascript:...`) ; legacy backslash quirks (`/\evil`).
 *
 *  Returns the validated path on success, null otherwise. The
 *  caller substitutes its own default (`"/dashboard"`) on null. */
export function sanitizeRedirect(value: string | null | undefined): string | null {
  if (!value) return null;
  if (typeof value !== "string") return null;
  if (!value.startsWith("/")) return null;
  // Protocol-relative URLs are 2-slash (`//evil.com/path`) — they
  // resolve to the current scheme + a different host.
  if (value.startsWith("//")) return null;
  // `\` is a known legacy IE/Edge quirk — `/\evil.com` resolved
  // cross-origin in some parsers. Reject defensively.
  if (value.startsWith("/\\")) return null;
  return value;
}
