// =============================================================================
// File: avatar.tsx
// Version: 2
// Path: ay_platform_ui/components/avatar.tsx
// Description: Small circular trigram badge used in chat messages and
//              in the global navbar. Native `title` attribute drives
//              the hover tooltip (browser ~700-1000 ms delay, free
//              accessibility tree integration).
//
//              v2 : accepts an optional `color` hex override for the
//              `user` variant. The default palette keeps the existing
//              blue tint when no override is set ; per-user colours
//              come from C2's `user_color` preference so the same
//              user shows up consistently across browsers (and, once
//              shared-project lands, across collaborators). The
//              `assistant` variant ignores `color` — the AyWizz brand
//              stays neutral grey by design.
// =============================================================================

interface Props {
  trigram: string;
  fullName: string;
  variant?: "user" | "assistant";
  size?: "sm" | "md";
  /** Hex (#RRGGBB) bubble + ring colour for the `user` variant.
   *  Ignored on the assistant variant (which uses the neutral
   *  brand palette). */
  color?: string | null;
}

export function Avatar({ trigram, fullName, variant = "user", size = "md", color = null }: Props) {
  const dim = size === "sm" ? "h-7 w-7 text-[10px]" : "h-9 w-9 text-xs";
  const baseClasses =
    "shrink-0 inline-flex items-center justify-center rounded-full font-mono font-semibold uppercase tracking-wide";
  // Built-in palettes used when no override is supplied. The user
  // palette is blue (cool, distinct from assistant) ; the assistant
  // is neutral so chat rows stay parsable at a glance.
  const fallbackUserClasses = "bg-blue-100 text-blue-900 ring-1 ring-blue-200";
  const assistantClasses = "bg-neutral-200 text-neutral-900 ring-1 ring-neutral-300";

  if (variant === "assistant") {
    return (
      <div
        role="img"
        className={[baseClasses, assistantClasses, dim].join(" ")}
        title={fullName}
        aria-label={`assistant ${fullName}`}
      >
        {trigram}
      </div>
    );
  }

  // User variant — apply the override hex when present, falling back
  // to the Tailwind blue palette otherwise. Inline styles are
  // necessary because the colour is user-supplied (Tailwind v4
  // can't JIT-purge arbitrary hex values at runtime without an
  // unsafe content shim).
  const inlineStyle: React.CSSProperties | undefined = color
    ? {
        backgroundColor: hexWithAlpha(color, 0.18),
        color: darkenHex(color, 0.5),
        boxShadow: `inset 0 0 0 1px ${hexWithAlpha(color, 0.4)}`,
      }
    : undefined;

  return (
    <div
      role="img"
      className={[baseClasses, color ? "" : fallbackUserClasses, dim].join(" ")}
      style={inlineStyle}
      title={fullName}
      aria-label={`user ${fullName}`}
    >
      {trigram}
    </div>
  );
}

/** Thinking indicator — three dots pulsing with a 200 ms stagger,
 *  used while the assistant stream is waiting for the first chunk
 *  (CPU LLM warmup) so the operator knows the request is alive. */
export function ThinkingDots() {
  return (
    <div
      role="status"
      className="inline-flex items-center gap-1"
      aria-label="Assistant is thinking"
      data-testid="thinking-dots"
    >
      <span
        className="block h-1.5 w-1.5 rounded-full bg-neutral-400 animate-pulse"
        style={{ animationDelay: "0ms" }}
      />
      <span
        className="block h-1.5 w-1.5 rounded-full bg-neutral-400 animate-pulse"
        style={{ animationDelay: "200ms" }}
      />
      <span
        className="block h-1.5 w-1.5 rounded-full bg-neutral-400 animate-pulse"
        style={{ animationDelay: "400ms" }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Color helpers — tiny inline implementations so the component stays
// self-contained (no lib/colors dependency for one usage site).
// ---------------------------------------------------------------------------

function hexWithAlpha(hex: string, alpha: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${a})`;
}

function darkenHex(hex: string, ratio: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  const k = Math.max(0, Math.min(1, ratio));
  const r = Math.round(rgb.r * (1 - k));
  const g = Math.round(rgb.g * (1 - k));
  const b = Math.round(rgb.b * (1 - k));
  return `rgb(${r}, ${g}, ${b})`;
}

function parseHex(hex: string): { r: number; g: number; b: number } | null {
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return null;
  return {
    r: Number.parseInt(hex.slice(1, 3), 16),
    g: Number.parseInt(hex.slice(3, 5), 16),
    b: Number.parseInt(hex.slice(5, 7), 16),
  };
}
