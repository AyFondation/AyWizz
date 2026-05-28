// =============================================================================
// File: types.ts
// Version: 1
// Path: ay_platform_ui/lib/profiles/types.ts
// Description: Types for the profile-aware UX. A project's `profile`
//              field (set at creation time, e.g. `"code"`) is mapped
//              by the registry to a `ProfileDefinition` that drives
//              everything profile-specific in the UI : sidebar
//              sections, default landing section, brand accent
//              overrides, etc. v1 ships `code` only ; future profiles
//              (`data`, `doc`, custom domains) plug in without
//              changes to the shell.
// =============================================================================

/** A single navigation entry in the project sidebar, scoped to one
 *  profile. `path` is appended to `/projects/[pid]/` ; `iconName`
 *  references a known icon symbol set the shell knows how to render. */
export interface ProfileSection {
  /** Stable id used in URLs and CSS hooks. Lowercase kebab. */
  id: string;
  /** Human-readable label shown in the sidebar. */
  label: string;
  /** Path segment under `/projects/[pid]/` (no leading slash). */
  path: string;
  /** Heroicons-style symbol name (resolved by `<Sidebar />`). v1
   *  supports a small fixed set ; expand the registry as profiles
   *  demand new icons. */
  iconName: SectionIcon;
  /** Optional short description used in tooltips when the sidebar
   *  is collapsed AND in the empty-state of the section page. */
  description?: string;
}

/** Closed set of icon names the shell knows how to render inline.
 *  Adding a new icon = one entry here + one case in the shell's
 *  icon switch. Keeps the bundle small and tree-shakable. */
export type SectionIcon =
  | "home"
  | "folder"
  | "chat"
  | "document"
  | "shield-check"
  | "cog"
  | "lightning";

/** A complete profile definition. Future profiles (data, doc, etc.)
 *  expose the same shape so the shell stays profile-agnostic. */
export interface ProfileDefinition {
  /** Wire value matching `Project.profile` server-side. */
  id: string;
  /** Display label for badges, switchers, etc. */
  label: string;
  /** Short tagline shown on the project overview header. */
  tagline: string;
  /** Accent color override for the profile (hex). When null/undefined
   *  the brand accent from `/ux/config` is used. */
  accentColorHex?: string;
  /** Sidebar sections in display order. The first entry is the
   *  default landing when the user navigates to `/projects/[pid]/`. */
  sections: ProfileSection[];
}
