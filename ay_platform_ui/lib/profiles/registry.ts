// =============================================================================
// File: registry.ts
// Version: 2
// Path: ay_platform_ui/lib/profiles/registry.ts
// Description: Lookup for `Project.profile` → `ProfileDefinition`.
//              Single source of truth — every shell render goes
//              through `resolveProfile()` so an unknown profile id
//              fails gracefully (unsupported-profile placeholder)
//              instead of throwing.
//
//              Adding a profile = one import + one entry in the map.
//              No other UI file needs to change.
//
//              v2 (2026-05-14) : registers the `docgen` profile.
// =============================================================================

import { CODE_PROFILE } from "./code";
import { DOCGEN_PROFILE } from "./docgen";
import type { ProfileDefinition } from "./types";

const REGISTRY: Record<string, ProfileDefinition> = {
  [CODE_PROFILE.id]: CODE_PROFILE,
  [DOCGEN_PROFILE.id]: DOCGEN_PROFILE,
};

/** Map of every known profile id to its label, for switchers that
 *  need to enumerate available choices without owning the registry. */
export function listKnownProfiles(): { id: string; label: string }[] {
  return Object.values(REGISTRY).map((p) => ({ id: p.id, label: p.label }));
}

/** Returns the matching definition or null. Callers SHALL handle
 *  null (e.g. render an "unsupported profile" placeholder) — we
 *  refuse to silently fall back so an unknown profile is visible. */
export function resolveProfile(profileId: string): ProfileDefinition | null {
  return REGISTRY[profileId] ?? null;
}
