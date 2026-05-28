// =============================================================================
// File: preferences.ts
// Version: 2
// Path: ay_platform_ui/lib/preferences.ts
// Description: User-scoped UI preferences (trigram avatar today, more
//              fields later). Persisted client-side in localStorage so
//              each browser keeps its own override per-user (`sub`).
//              v2 will migrate to a backend C2 `/users/me/preferences`
//              endpoint when one exists.
//
//              v2 (2026-05-21): `workingAreaPaneWidths` — the operator's
//              chosen pixel widths for the working-area left (tree) and
//              right (chat) panes, persisted across sessions (#6).
//
//              Default trigram derivation (French convention) :
//                "Jean Dupont"     → "DUJ"   (2 letters of last name + 1 of first)
//                "Alice Martin"    → "MAA"
//                "Maria del Carmen Lopez" → "LOM" (last name + first-token initial)
//                no `name` set     → first 3 chars of `username`, else `sub`
//
//              User-supplied trigram SHALL be 3-4 chars, alphanumeric.
// =============================================================================

import type { JWTClaims } from "./auth";

const PREFS_KEY_PREFIX = "aywizz.prefs.";

/** Persisted prefs shape. Add fields here without changing the wire
 *  format on the next iteration. */
export interface UserPreferences {
  trigram?: string;
  /** Working-area resizable 3-pane layout : pixel widths of the left
   *  (file tree) and right (chat) panes ; the middle viewer flexes to
   *  fill the rest (#6). */
  workingAreaPaneWidths?: { left: number; right: number };
}

/** Default trigram from JWT claims. Always returns a 3-4 char ASCII
 *  uppercase string. Never throws — falls back to "USR" when no
 *  usable identifier is present. */
export function defaultTrigramFromClaims(claims: JWTClaims): string {
  const name = typeof claims.name === "string" ? claims.name.trim() : "";
  if (name) {
    // Split on whitespace ; conventional "<first> <last>" or longer
    // patterns are both supported.
    const parts = name.split(/\s+/).filter((p) => p.length > 0);
    if (parts.length >= 2) {
      const firstName = parts[0];
      const lastName = parts[parts.length - 1];
      // Per the French trigramme convention : 2 first letters of the
      // last name + 1 first letter of the first name. Result is
      // always 3 chars.
      const tri = (lastName.slice(0, 2) + firstName.charAt(0)).toUpperCase();
      return _sanitize(tri, 3);
    }
    // Single-token name → first 3 chars.
    return _sanitize(name.slice(0, 3).toUpperCase(), 3);
  }
  // No display name : fall back to username (login) or sub (user id).
  const username = claims.username ?? claims.sub;
  return _sanitize(username.slice(0, 3).toUpperCase(), 3);
}

/** Effective trigram for a user — the stored override if set,
 *  otherwise the derived default. */
export function getEffectiveTrigram(claims: JWTClaims): string {
  const stored = readPreferences(claims.sub).trigram;
  if (stored && isValidTrigram(stored)) return stored;
  return defaultTrigramFromClaims(claims);
}

/** Validate a user-supplied trigram. Rules : 3-4 chars, alphanumeric. */
export function isValidTrigram(value: string): boolean {
  return /^[A-Za-z0-9]{3,4}$/.test(value);
}

/** Read all preferences for `sub`. Returns an empty object when
 *  nothing is stored or the value is corrupt. */
export function readPreferences(sub: string): UserPreferences {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(PREFS_KEY_PREFIX + sub);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as UserPreferences;
  } catch {
    return {};
  }
}

/** Persist preferences for `sub`. Pass `null` or `undefined` for a
 *  field to remove it. */
export function writePreferences(sub: string, prefs: UserPreferences): void {
  if (typeof window === "undefined") return;
  const current = readPreferences(sub);
  const next: UserPreferences = { ...current, ...prefs };
  // Drop undefined keys so they don't roundtrip as "undefined" strings.
  for (const key of Object.keys(next) as (keyof UserPreferences)[]) {
    if (next[key] === undefined || next[key] === "") delete next[key];
  }
  window.localStorage.setItem(PREFS_KEY_PREFIX + sub, JSON.stringify(next));
}

/** Human-readable name to surface in tooltips. `displayName` when
 *  present + `username` as a parenthetical when distinct ; falls
 *  back to `username` alone, then `sub`. */
export function fullNameForTooltip(claims: JWTClaims): string {
  const name = typeof claims.name === "string" ? claims.name.trim() : "";
  const username = claims.username ?? "";
  if (name && username && name !== username) return `${name} (${username})`;
  if (name) return name;
  if (username) return username;
  return claims.sub;
}

function _sanitize(value: string, minLen: number): string {
  const cleaned = value.replace(/[^A-Za-z0-9]/g, "");
  if (cleaned.length >= minLen) return cleaned.slice(0, 4);
  // Pad with "X" if too short to keep the 3-char minimum.
  return `${cleaned}XXX`.slice(0, Math.max(minLen, cleaned.length));
}
