// =============================================================================
// File: backups-page.test.tsx
// Path: ay_platform_ui/tests/integration/backups-page.test.tsx
// Description: Tests for the project Backups page (D-022): lists stored
//              backups, creates a snapshot, and surfaces the backend's real
//              error detail on a guarded operation.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BackupsPage from "@/app/(protected)/projects/[pid]/backups/page";
import { server } from "../helpers/msw-server";

const READY_CONFIG = {
  runtime: { apiBaseUrl: "", publicBaseUrl: "" },
  ux: {
    api_version: "v1",
    auth_mode: "local",
    brand: { name: "AyWizz", short_name: "AY", accent_color_hex: "#000" },
    features: {
      chat_enabled: true,
      kg_enabled: true,
      cross_tenant_enabled: false,
      file_download_enabled: true,
    },
  },
};

vi.mock("@/app/providers", () => ({ useReadyConfig: () => READY_CONFIG }));
vi.mock("next/navigation", () => ({ useParams: () => ({ pid: "p1" }) }));

const B = "/api/v1/projects/p1/backups";

function record(over: Partial<Record<string, unknown>> = {}) {
  return {
    backup_id: "abc123def456",
    scope: "project",
    tenant_id: "t1",
    project_id: "p1",
    created_at: "2026-08-31T10:00:00+00:00",
    created_by: "alice",
    size_bytes: 2048,
    object_key: "tenant/t1/project/p1/abc.tar.gz",
    checksum: "x".repeat(64),
    origin: "generated",
    manifest_version: 1,
    ...over,
  };
}

beforeEach(() => window.localStorage.clear());

describe("BackupsPage", () => {
  it("lists backups", async () => {
    server.use(http.get(B, () => HttpResponse.json([record()])));
    render(<BackupsPage />);
    await waitFor(() => expect(screen.getByTestId("backups-table")).toBeInTheDocument());
    expect(screen.getByTestId("backups-row-abc123def456")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    server.use(http.get(B, () => HttpResponse.json([])));
    render(<BackupsPage />);
    await waitFor(() => expect(screen.getByTestId("backups-empty")).toBeInTheDocument());
  });

  it("creates a snapshot", async () => {
    const post = vi.fn(() => HttpResponse.json(record({ backup_id: "new999" }), { status: 201 }));
    server.use(
      http.get(B, () => HttpResponse.json([])),
      http.post(B, post),
    );
    render(<BackupsPage />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByTestId("backups-empty")).toBeInTheDocument());
    await user.click(screen.getByTestId("backups-snapshot"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(await screen.findByTestId("backups-notice")).toHaveTextContent(/Snapshot created/);
  });

  it("surfaces the backend's real reason on a guarded snapshot (403)", async () => {
    server.use(
      http.get(B, () => HttpResponse.json([])),
      http.post(B, () =>
        HttpResponse.json(
          { detail: "requires one of: project_owner, tenant_admin, admin, platform_manager" },
          { status: 403 },
        ),
      ),
    );
    render(<BackupsPage />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByTestId("backups-empty")).toBeInTheDocument());
    await user.click(screen.getByTestId("backups-snapshot"));
    const alert = await screen.findByTestId("backups-error");
    expect(alert).toHaveTextContent(/requires one of/i);
  });
});
