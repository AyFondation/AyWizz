// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/overview/page.tsx
// Description: Project Overview — landing section after picking a
//              project. Phase A renders a minimal layout with cards
//              for the other sections so the operator gets a visual
//              anchor and can navigate quickly. Real stats (source
//              count, recent conversations, pipeline status) land in
//              their respective phases.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { CODE_PROFILE } from "@/lib/profiles/code";

export default function OverviewPage() {
  const params = useParams<{ pid: string }>();
  const pid = decodeURIComponent(params.pid);
  const sections = CODE_PROFILE.sections.filter((s) => s.id !== "overview");

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">Overview</h2>
        <p className="mt-1 text-sm text-neutral-500">{CODE_PROFILE.tagline}</p>
      </header>

      <section className="mt-8">
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          Quick links
        </h3>
        <ul
          className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          data-testid="overview-quicklinks"
        >
          {sections.map((s) => (
            <li key={s.id}>
              <Link
                href={`/projects/${encodeURIComponent(pid)}/${s.path}`}
                className="block rounded-lg border border-neutral-200 bg-white p-5 transition-shadow hover:shadow-md"
                data-testid={`overview-link-${s.id}`}
              >
                <h4 className="text-base font-semibold text-neutral-900">{s.label}</h4>
                {s.description ? (
                  <p className="mt-1 text-sm text-neutral-500">{s.description}</p>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-10 rounded-lg border border-dashed border-neutral-300 p-6 text-sm text-neutral-500">
        <p>Section-specific stats land in subsequent phases :</p>
        <ul className="mt-2 list-disc pl-5">
          <li>Sources — count, total size, last upload (Phase C)</li>
          <li>Conversations — recent threads, message count (Phase D)</li>
          <li>Requirements — document/entity counts (Phase E)</li>
          <li>Validation — last run, pass/fail summary (Phase F)</li>
        </ul>
      </section>
    </main>
  );
}
