// =============================================================================
// File: layout.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/layout.tsx
// Description: Project-scoped shell layered under the global protected
//              layout. Fetches the project record (to read `profile`),
//              resolves the matching `ProfileDefinition`, and renders
//              the sidebar + content area with consistent padding.
//
//              The project record is fetched once on mount via the
//              tenant-scoped `GET /api/v1/projects` (we filter the
//              returned list by `project_id`) — once the dedicated
//              `GET /api/v1/projects/{pid}` endpoint exists, switch
//              to a direct fetch. The list call is cheap enough for
//              v1 (tenants typically have a handful of projects).
// =============================================================================

"use client";

import { useParams, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Sidebar } from "@/components/sidebar";
import { SidebarProvider, useSidebar } from "@/components/sidebar-context";
import { ApiClient } from "@/lib/apiClient";
import { resolveProfile } from "@/lib/profiles/registry";
import type { ProfileDefinition } from "@/lib/profiles/types";
import type { Project } from "@/lib/types";

import { useConfigState } from "../../../providers";

type LoadState =
  | { status: "loading" }
  | { status: "found"; project: Project; profile: ProfileDefinition | null }
  | { status: "not-found" }
  | { status: "error"; message: string };

export default function ProjectShellLayout({ children }: { children: ReactNode }) {
  const params = useParams<{ pid: string }>();
  const router = useRouter();
  const projectId = decodeURIComponent(params.pid);
  const configState = useConfigState();
  const [state, setState] = useState<LoadState>({ status: "loading" });

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
        if (cancelled) return;
        const project = resp.items.find((p) => p.project_id === projectId);
        if (!project) {
          setState({ status: "not-found" });
          return;
        }
        setState({
          status: "found",
          project,
          profile: resolveProfile(project.profile),
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <p className="text-neutral-500">Loading project…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <p className="text-red-700" role="alert">
          Failed to load project: {state.message}
        </p>
      </main>
    );
  }

  if (state.status === "not-found") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <h1 className="text-2xl font-semibold">Project not found</h1>
        <p className="mt-2 text-sm text-neutral-500">
          The project <code className="rounded bg-neutral-100 px-1">{projectId}</code> doesn't exist
          in your tenant or you don't have access to it.
        </p>
        <button
          type="button"
          onClick={() => router.push("/projects")}
          className="mt-6 rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
        >
          ← Back to projects
        </button>
      </main>
    );
  }

  const { project, profile } = state;

  if (profile === null) {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <h1 className="text-2xl font-semibold">{project.name}</h1>
        <p className="mt-2 text-sm text-neutral-500">
          This project uses profile{" "}
          <code className="rounded bg-neutral-100 px-1">{project.profile}</code>, which this version
          of the UX doesn't yet support. Upgrade or pick a different project.
        </p>
      </main>
    );
  }

  return (
    <SidebarProvider>
      <ProjectShellInner project={project} profile={profile}>
        {children}
      </ProjectShellInner>
    </SidebarProvider>
  );
}

/** Inner shell that consumes the sidebar collapse state so the content
 *  area's left-margin tracks the sidebar width in sync. */
function ProjectShellInner({
  project,
  profile,
  children,
}: {
  project: Project;
  profile: ProfileDefinition;
  children: ReactNode;
}) {
  const { collapsed } = useSidebar();
  return (
    <div className="flex min-h-[calc(100vh-3.5rem)]">
      <Sidebar profile={profile} projectId={project.project_id} />
      <div
        className={[
          "flex-1 transition-[margin] duration-200",
          // < md : no margin (sidebar is a drawer, hidden by default).
          // ≥ md : margin tracks sidebar width — 14 collapsed, 56 expanded.
          collapsed ? "md:ml-14" : "md:ml-56",
        ].join(" ")}
        data-testid="project-content"
      >
        <ProjectHeader project={project} profileLabel={profile.label} />
        {children}
      </div>
    </div>
  );
}

function ProjectHeader({ project, profileLabel }: { project: Project; profileLabel: string }) {
  return (
    <div className="border-b border-neutral-200 bg-white px-6 py-4">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate font-mono text-xs text-neutral-500">
            {project.tenant_id} / {project.project_id}
          </p>
          <h1 className="truncate text-xl font-semibold text-neutral-900">{project.name}</h1>
        </div>
        <span className="shrink-0 rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-900">
          {profileLabel}
        </span>
      </div>
    </div>
  );
}
