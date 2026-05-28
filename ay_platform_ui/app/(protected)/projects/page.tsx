// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/page.tsx
// Description: Landing page after login — lists the projects accessible
//              to the current user (calls `GET /api/v1/projects`,
//              tenant-scoped). Each card shows name, profile badge,
//              creation metadata and links to the project shell.
//
//              Profile badge is rendered via the registry so unknown
//              profiles surface a neutral "Unknown" tag rather than
//              an opaque id — operator gets a visible signal that
//              their server uses a profile this UX doesn't support.
// =============================================================================

"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import { resolveProfile } from "@/lib/profiles/registry";
import type { Project } from "@/lib/types";

import { useConfigState } from "../../providers";

type ListState =
  | { status: "loading" }
  | { status: "ready"; items: Project[] }
  | { status: "error"; message: string };

export default function ProjectsPage() {
  const configState = useConfigState();
  const [state, setState] = useState<ListState>({ status: "loading" });

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    apiClient
      .listProjects()
      .then((resp) => {
        if (!cancelled) setState({ status: "ready", items: resp.items });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? `HTTP ${err.status}` : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <p className="text-neutral-500">Loading projects…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <h1 className="text-2xl font-semibold">Projects</h1>
        <p className="mt-4 text-sm text-red-700" role="alert">
          Failed to load projects: {state.message}
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
        <p className="text-sm text-neutral-500">
          {state.items.length} project{state.items.length === 1 ? "" : "s"}
        </p>
      </div>

      {state.items.length === 0 ? (
        <div
          className="mt-10 rounded-lg border border-dashed border-neutral-300 p-10 text-center"
          data-testid="projects-empty-state"
        >
          <p className="text-neutral-600">No projects yet.</p>
          <p className="mt-1 text-sm text-neutral-500">
            Ask your tenant admin to create one, or sign in with an admin account to create a
            project here.
          </p>
        </div>
      ) : (
        <ul
          className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          data-testid="projects-list"
        >
          {state.items.map((p) => (
            <li key={p.project_id}>
              <ProjectCard project={p} />
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function ProjectCard({ project }: { project: Project }) {
  const profile = resolveProfile(project.profile);
  const profileLabel = profile?.label ?? `Unknown (${project.profile})`;
  const profileColor = profile ? "bg-blue-100 text-blue-900" : "bg-neutral-200 text-neutral-700";
  return (
    <Link
      href={`/projects/${encodeURIComponent(project.project_id)}`}
      className="block rounded-lg border border-neutral-200 bg-white p-5 transition-shadow hover:shadow-md"
      data-testid={`project-card-${project.project_id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-lg font-semibold text-neutral-900">{project.name}</h2>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${profileColor}`}>
          {profileLabel}
        </span>
      </div>
      <p className="mt-1 font-mono text-xs text-neutral-500">{project.project_id}</p>
      <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-neutral-500">
        <dt>Tenant</dt>
        <dd className="text-neutral-700">{project.tenant_id}</dd>
        <dt>Created by</dt>
        <dd className="text-neutral-700">{project.created_by}</dd>
        <dt>Created at</dt>
        <dd className="text-neutral-700">{new Date(project.created_at).toLocaleDateString()}</dd>
      </dl>
    </Link>
  );
}
