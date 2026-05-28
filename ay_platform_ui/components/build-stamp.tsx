// =============================================================================
// File: build-stamp.tsx
// Version: 3
// Path: ay_platform_ui/components/build-stamp.tsx
// Description: Two-line build-version block surfacing the UI bundle's
//              stamp + the API tier's stamp. The UI value is baked at
//              `next build` time via `NEXT_PUBLIC_BUILD_VERSION` (set
//              by `Dockerfile.ui`'s ARG) ; the API value comes from
//              `/ux/config.build_version` (also baked at docker build
//              time, on the Python image).
//
//              Purpose : a quick visual confirmation after a rebuild
//              that the browser is actually running the freshly-built
//              bundle (vs a cached one) AND that the API container
//              has been rebuilt alongside. If both stamps don't move
//              after a rebuild, the operator knows something is off
//              before chasing a phantom bug.
//
//              v3 : drops the framing styles (border / background) so
//              the block can be embedded directly in the navbar — the
//              parent owns the layout. Renders nothing in `ConfigState
//              != ready` so non-protected pages (login) silently
//              omit it.
// =============================================================================

"use client";

import { useConfigState } from "@/app/providers";

/** Build version baked into the UI bundle at `next build` time. The
 *  `NEXT_PUBLIC_*` prefix tells Next.js to embed the value in the
 *  client bundle ; non-prefixed env vars stay server-only. Defaults
 *  to "dev" when `next dev` is run without the ENV (local outside
 *  Docker). */
const UI_BUILD_VERSION = process.env.NEXT_PUBLIC_BUILD_VERSION ?? "dev";

export function BuildStamp() {
  const cfg = useConfigState();
  // Defensive null-render when the bootstrap isn't ready yet (login
  // page, etc.). The protected layout gates on `ConfigState.ready`,
  // so navbar consumers always see a value, but rendering this
  // component outside that gate (e.g. tests) shouldn't blow up.
  if (cfg.status !== "ready") return null;
  const apiBuild = cfg.config.ux.build_version ?? "unknown";
  return (
    <div
      className="select-text text-right font-mono text-[10px] leading-tight text-neutral-500"
      data-testid="build-stamp"
      title="Build stamps. Use them to confirm a rebuild took effect — the values change with every `e2e_stack.sh dev` run."
    >
      <div className="truncate" data-testid="build-stamp-ui">
        <span className="text-neutral-400">ui&nbsp;</span>
        {UI_BUILD_VERSION}
      </div>
      <div className="truncate" data-testid="build-stamp-api">
        <span className="text-neutral-400">api</span> {apiBuild}
      </div>
    </div>
  );
}
