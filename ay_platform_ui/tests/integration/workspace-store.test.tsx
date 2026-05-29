// =============================================================================
// File: workspace-store.test.tsx
// Path: ay_platform_ui/tests/integration/workspace-store.test.tsx
// Description: Tests for the WorkspaceProvider store + its hooks. Exercises
//              the UI-state map (get / patch / setDraft + sessionStorage
//              hydrate & persist), the outside-provider safety fallbacks,
//              and the provider-owned SSE send loop (useWorkspaceSend +
//              useConvRuntime) : streaming runtime, inline-event collapse,
//              onMutatingTool callback, turnSeq bump, and the error path.
//              The chat SSE endpoint is streamed via MSW.
// =============================================================================

import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type SendArgs,
  useConvRuntime,
  useProjectUi,
  useWorkspaceSend,
  WorkspaceProvider,
} from "@/app/(protected)/workspace-store";
import type { PlatformConfig } from "@/lib/types";
import { server } from "../helpers/msw-server";

const STORAGE_KEY = "aywizz.workspace.ui.v2";

const CFG: PlatformConfig = {
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

beforeEach(() => {
  window.sessionStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

/** Stream an SSE body through MSW for the chat endpoint. */
function sseResponse(blocks: string[]) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const b of blocks) controller.enqueue(encoder.encode(b));
      controller.close();
    },
  });
  return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
}

describe("WorkspaceProvider — UI state map", () => {
  it("starts empty, patches a slice, and persists to sessionStorage", async () => {
    const { result } = renderHook(() => useProjectUi("p1"), { wrapper: WorkspaceProvider });
    expect(result.current.ui.selectedRunId).toBeNull();

    act(() => result.current.setUi({ selectedRunId: "live-docs", selectedPath: "a.md" }));
    expect(result.current.ui.selectedRunId).toBe("live-docs");
    expect(result.current.ui.selectedPath).toBe("a.md");

    await waitFor(() => {
      const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? "{}");
      expect(stored.p1?.selectedRunId).toBe("live-docs");
    });
  });

  it("stores per-conversation composer drafts without bleeding across ids", () => {
    const { result } = renderHook(() => useProjectUi("p1"), { wrapper: WorkspaceProvider });
    act(() => result.current.setDraft("c1", "draft one"));
    act(() => result.current.setDraft("c2", "draft two"));
    expect(result.current.ui.composerDrafts.c1).toBe("draft one");
    expect(result.current.ui.composerDrafts.c2).toBe("draft two");
  });

  it("hydrates a normalised slice from sessionStorage on mount", async () => {
    window.sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        p1: {
          activeConversationId: "c9",
          selectedRunId: "r1",
          selectedPath: null,
          composerDrafts: { c9: "hi" },
        },
        // legacy/foreign shape must be normalised, not crash
        p2: { activeConversationId: 123, composerDrafts: ["bad"] },
      }),
    );
    const { result } = renderHook(() => useProjectUi("p1"), { wrapper: WorkspaceProvider });
    await waitFor(() => expect(result.current.ui.activeConversationId).toBe("c9"));
    expect(result.current.ui.composerDrafts.c9).toBe("hi");
  });

  it("is safe to use outside the provider (inert fallbacks)", () => {
    const { result } = renderHook(() => useProjectUi("p1"));
    expect(result.current.ui.selectedRunId).toBeNull();
    // no-op patch must not throw
    act(() => result.current.setUi({ selectedRunId: "x" }));
    expect(result.current.ui.selectedRunId).toBeNull();
  });
});

describe("WorkspaceProvider — SSE send loop", () => {
  function setup() {
    return renderHook(() => ({ send: useWorkspaceSend(), rt: useConvRuntime("c1") }), {
      wrapper: WorkspaceProvider,
    });
  }

  it("streams a turn: collapses stage events, fires onMutatingTool, bumps turnSeq", async () => {
    server.use(
      http.post("/api/v1/conversations/c1/messages", () =>
        sseResponse([
          'event: inline\ndata: {"kind":"stage","name":"retrieve","status":"running"}\n\n',
          'event: inline\ndata: {"kind":"stage","name":"retrieve","status":"done"}\n\n',
          "data: Hello\n\n",
          'event: inline\ndata: {"kind":"tool_call","name":"create_document","status":"done"}\n\n',
          "data: [DONE]\n\n",
        ]),
      ),
    );
    const onMutatingTool = vi.fn();
    const { result } = setup();

    await act(async () => {
      await result.current.send({
        cfg: CFG,
        conversationId: "c1",
        payload: "hi",
        onMutatingTool,
      } satisfies SendArgs);
    });

    await waitFor(() => expect(result.current.rt.turnSeq).toBe(1));
    expect(result.current.rt.streaming).toBe(false);
    expect(result.current.rt.liveAssistant).toBeNull();
    expect(result.current.rt.error).toBeNull();
    // stage running→done collapsed into ONE entry (status done) + the tool_call
    expect(result.current.rt.liveEvents).toHaveLength(2);
    const stage = result.current.rt.liveEvents.find((e) => e.kind === "stage");
    expect(stage?.status).toBe("done");
    // create_document tool_call done → parent refresh hook fired
    expect(onMutatingTool).toHaveBeenCalledTimes(1);
  });

  it("records the error and still bumps turnSeq when the stream request fails", async () => {
    server.use(
      http.post("/api/v1/conversations/c1/messages", () =>
        HttpResponse.json({ detail: "nope" }, { status: 401 }),
      ),
    );
    const { result } = setup();
    await act(async () => {
      await result.current.send({ cfg: CFG, conversationId: "c1", payload: "hi" });
    });
    await waitFor(() => expect(result.current.rt.error).toMatch(/Send failed \(401\)/));
    expect(result.current.rt.turnSeq).toBe(1);
    expect(result.current.rt.streaming).toBe(false);
  });
});
