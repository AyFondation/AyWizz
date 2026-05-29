// =============================================================================
// File: chat-sidebar.test.tsx
// Path: ay_platform_ui/tests/integration/chat-sidebar.test.tsx
// Description: Tests for the DocGen workspace ChatSidebar. It takes `cfg` as
//              a prop (no provider) and is driven by the workspace-store
//              (mocked: send / runtime / ui). C3 conversation endpoints via
//              MSW. Covers : load + conversation picker + messages ; empty
//              state (composer disabled) ; "+ New" create ; send → provider
//              `send` with the quoted-snippet prefix ; streaming indicator ;
//              list error.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatSidebar } from "@/components/chat-sidebar";
import type { PlatformConfig } from "@/lib/types";
import { server } from "../helpers/msw-server";

const { sendMock, rtHolder } = vi.hoisted(() => ({
  sendMock: vi.fn(),
  rtHolder: {
    streaming: false,
    liveAssistant: null as string | null,
    liveEvents: [] as unknown[],
    turnSeq: 0,
    error: null as string | null,
  },
}));

vi.mock("@/app/(protected)/workspace-store", () => ({
  useProjectUi: () => ({
    ui: { composerDrafts: {}, activeConversationId: null },
    setUi: vi.fn(),
    setDraft: vi.fn(),
  }),
  useWorkspaceSend: () => sendMock,
  useConvRuntime: () => rtHolder,
}));

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

const CONV_URL = "/api/v1/conversations";

function makeConv(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "c1",
    title: "First chat",
    project_id: "p1",
    message_count: 1,
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function renderSidebar(props: Partial<React.ComponentProps<typeof ChatSidebar>> = {}) {
  return render(
    <ChatSidebar cfg={CFG} projectId="p1" quoted={null} onClearQuote={vi.fn()} {...props} />,
  );
}

beforeEach(() => {
  sendMock.mockClear();
  rtHolder.streaming = false;
  rtHolder.liveAssistant = null;
  rtHolder.liveEvents = [];
  rtHolder.turnSeq = 0;
  rtHolder.error = null;
});

afterEach(() => vi.restoreAllMocks());

describe("ChatSidebar", () => {
  it("loads the project conversations + the active conversation's messages", async () => {
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })),
      http.get(`${CONV_URL}/c1/messages`, () =>
        HttpResponse.json({
          messages: [
            {
              id: "m1",
              conversation_id: "c1",
              role: "user",
              content: "hi",
              timestamp: "2026-01-01T00:00:00Z",
            },
            {
              id: "m2",
              conversation_id: "c1",
              role: "assistant",
              content: "hello",
              timestamp: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      ),
    );
    renderSidebar();

    await waitFor(() => expect(screen.getByTestId("docgen-chat-sidebar")).toBeInTheDocument());
    expect(await screen.findByTestId("chat-msg-user")).toHaveTextContent("hi");
    expect(screen.getByTestId("chat-msg-assistant")).toHaveTextContent("hello");
  });

  it("shows the empty state + disabled composer when no conversation exists", async () => {
    server.use(http.get(CONV_URL, () => HttpResponse.json({ conversations: [] })));
    renderSidebar();
    await waitFor(() =>
      expect(screen.getByText(/No conversation on this project yet/)).toBeInTheDocument(),
    );
    expect(screen.getByTestId("chat-input")).toBeDisabled();
  });

  it("creates a new conversation via '+ New'", async () => {
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [] })),
      http.get(`${CONV_URL}/:cid/messages`, () => HttpResponse.json({ messages: [] })),
      http.post(CONV_URL, () =>
        HttpResponse.json({ conversation: makeConv({ id: "new-1", title: "New conversation" }) }),
      ),
    );
    renderSidebar();
    await waitFor(() => expect(screen.getByTestId("docgen-chat-sidebar")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "+ New" }));
    // composer becomes enabled once a conversation is active
    await waitFor(() => expect(screen.getByTestId("chat-input")).not.toBeDisabled());
  });

  it("sends a message (with the quoted-snippet prefix) via the provider send", async () => {
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })),
      http.get(`${CONV_URL}/c1/messages`, () => HttpResponse.json({ messages: [] })),
    );
    renderSidebar({ quoted: { path: "doc.md", text: "line1" } });
    await waitFor(() => expect(screen.getByTestId("chat-input")).not.toBeDisabled());

    const user = userEvent.setup();
    await user.type(screen.getByTestId("chat-input"), "please update");
    await user.click(screen.getByTestId("chat-send"));

    expect(sendMock).toHaveBeenCalledTimes(1);
    const arg = sendMock.mock.calls[0][0] as { conversationId: string; payload: string };
    expect(arg.conversationId).toBe("c1");
    expect(arg.payload).toContain("> from `doc.md`"); // quote prefix
    expect(arg.payload).toContain("please update");
  });

  it("shows the streaming indicator and disables Send while streaming", async () => {
    rtHolder.streaming = true;
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })),
      http.get(`${CONV_URL}/c1/messages`, () => HttpResponse.json({ messages: [] })),
    );
    renderSidebar();
    await waitFor(() => expect(screen.getByTestId("chat-generating")).toBeInTheDocument());
    expect(screen.getByTestId("chat-send")).toHaveTextContent("Sending…");
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });

  it("renders an error when the conversation list fails", async () => {
    server.use(http.get(CONV_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderSidebar();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Could not list conversations/)).toBeInTheDocument();
  });
});
