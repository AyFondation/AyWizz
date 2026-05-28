// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/page.tsx
// Description: Index route under a project shell — redirects to the
//              project's default section (first entry in the profile's
//              section list, typically `/overview`). The layout above
//              has already validated the project + profile, so we can
//              redirect unconditionally from useEffect.
// =============================================================================

"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

export default function ProjectIndexRedirect() {
  const params = useParams<{ pid: string }>();
  const router = useRouter();

  useEffect(() => {
    // Default landing = `overview`. This matches the first section of
    // the `code` profile and is the universal "home" tab the registry
    // points at. If a future profile reorders sections, this stays
    // correct as long as `overview` is the conventional landing slug.
    const target = `/projects/${encodeURIComponent(params.pid)}/overview`;
    router.replace(target);
  }, [params.pid, router]);

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <p className="text-neutral-500">Loading project overview…</p>
    </main>
  );
}
