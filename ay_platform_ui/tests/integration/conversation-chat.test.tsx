// =============================================================================
// File: conversation-chat.test.tsx
// Path: ay_platform_ui/tests/integration/conversation-chat.test.tsx
// Description: Integration tests for the chat view (conversations/[cid]).
//              The SSE send loop is provider-owned, so workspace-store
//              (useWorkspaceSend / useConvRuntime / useProjectUi) is
//              mocked with controllable holders ; useConfigState + useAuth
//              come from renderWithProviders with a seeded token, and the
//              C3 read endpoints are scripted via MSW. Covers :
//                - loading → ready : header title, count, user + assistant
//                  bubbles, message content ;
//                - empty messages placeholder ;
//                - not-found (404) and load error (500) states ;
//                - streaming runtime → "Génération en cours" + disabled
//                  composer + "Streaming…" button ;
//                - provider send error surfaced in the error slot ;
//                - composer send → provider `send` invoked with the
//                  payload + first-message auto-rename PATCH ; composer
//                  cleared.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "@/app/(protected)/projects/[pid]/conversations/[cid]/page";
import { fakeJWT } from "../helpers/msw-handlers";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

const { sendMock, rtHolder, uiHolder, setUiMock, setDraftMock } = vi.hoisted(() => ({
  sendMock: vi.fn(),
  rtHolder: {
    streaming: false,
    liveAssistant: null as string | null,
    liveEvents: [] as unknown[],
    turnSeq: 0,
    error: null as string | null,
  },
  uiHolder: {
    activeConversationId: null as string | null,
    composerDrafts: {} as Record<string, string>,
  },
  setUiMock: vi.fn(),
  setDraftMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1", cid: "conv-1" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/projects/p1/conversations/conv-1",
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/app/(protected)/workspace-store", () => ({
  useProjectUi: () => ({ ui: uiHolder, setUi: setUiMock, setDraft: setDraftMock }),
  useWorkspaceSend: () => sendMock,
  useConvRuntime: () => rtHolder,
}));

const CONV = "/api/v1/conversations/conv-1";

function makeConv(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "conv-1",
    title: "Existing chat",
    project_id: "p1",
    message_count: 2,
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function makeMsg(role: string, content: string, id: string) {
  return { id, conversation_id: "conv-1", role, content, timestamp: "2026-01-01T00:00:00Z" };
}

/** Read endpoints the page hits on mount. `conv`/`messages` overridable. */
function readHandlers(opts: { conv?: unknown; messages?: unknown[] } = {}) {
  return [
    http.get(CONV, () => HttpResponse.json({ conversation: opts.conv ?? makeConv() })),
    http.get(`${CONV}/messages`, () => HttpResponse.json({ messages: opts.messages ?? [] })),
    http.get("/api/v1/users/me/preferences", () =>
      HttpResponse.json({ user_prompt: "", user_color: null }),
    ),
    http.get("/api/v1/projects/p1", () =>
      HttpResponse.json({ project_id: "p1", profile: "code", name: "P", system_prompt: "" }),
    ),
  ];
}

beforeEach(() => {
  sendMock.mockClear();
  rtHolder.streaming = false;
  rtHolder.liveAssistant = null;
  rtHolder.liveEvents = [];
  rtHolder.turnSeq = 0;
  rtHolder.error = null;
  uiHolder.activeConversationId = null;
  uiHolder.composerDrafts = {};
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "alice",
      tenant_id: "t1",
      roles: ["admin"],
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
});

afterEach(() => vi.restoreAllMocks());

describe("ChatPage load states", () => {
  it("renders the conversation with its messages", async () => {
    server.use(
      ...readHandlers({
        messages: [makeMsg("user", "Hello there", "m1"), makeMsg("assistant", "Hi!", "m2")],
      }),
    );
    renderWithProviders(<ChatPage />);

    await waitFor(() => expect(screen.getByTestId("chat-view")).toBeInTheDocument());
    expect(screen.getByText("Existing chat")).toBeInTheDocument();
    expect(screen.getByText("Hello there")).toBeInTheDocument();
    expect(screen.getByText("Hi!")).toBeInTheDocument();
    expect(screen.getByTestId("message-user")).toBeInTheDocument();
    expect(screen.getByTestId("message-assistant")).toBeInTheDocument();
  });

  it("shows the empty-messages placeholder", async () => {
    server.use(...readHandlers({ messages: [] }));
    renderWithProviders(<ChatPage />);
    await waitFor(() => expect(screen.getByTestId("chat-view")).toBeInTheDocument());
    expect(screen.getByText(/No messages yet/)).toBeInTheDocument();
  });

  it("renders the not-found state on a 404", async () => {
    server.use(http.get(CONV, () => HttpResponse.json({ detail: "gone" }, { status: 404 })));
    renderWithProviders(<ChatPage />);
    await waitFor(() => expect(screen.getByText(/Conversation not found/)).toBeInTheDocument());
  });

  it("renders the error state on a non-404 failure", async () => {
    server.use(http.get(CONV, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderWithProviders(<ChatPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load:/)).toBeInTheDocument();
  });
});

describe("ChatPage streaming + errors", () => {
  it("shows the in-flight indicator and disables the composer while streaming", async () => {
    rtHolder.streaming = true;
    rtHolder.liveAssistant = "thinking…";
    server.use(...readHandlers({ messages: [makeMsg("user", "Q", "m1")] }));
    renderWithProviders(<ChatPage />);

    await waitFor(() => expect(screen.getByTestId("chat-generating")).toBeInTheDocument());
    expect(screen.getByTestId("composer-input")).toBeDisabled();
    expect(screen.getByTestId("composer-send")).toHaveTextContent("Streaming…");
  });

  it("surfaces a provider send error in the error slot", async () => {
    rtHolder.error = "429 rate limited";
    server.use(...readHandlers());
    renderWithProviders(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByText(/Send failed: 429 rate limited/)).toBeInTheDocument(),
    );
  });
});

describe("ChatPage send", () => {
  it("invokes the provider send + auto-renames a placeholder-titled conversation", async () => {
    const patch = vi.fn(() => HttpResponse.json({ conversation: makeConv({ title: "Build it" }) }));
    server.use(
      ...readHandlers({ conv: makeConv({ title: "New conversation" }), messages: [] }),
      http.patch(CONV, patch),
    );
    renderWithProviders(<ChatPage />);
    await waitFor(() => expect(screen.getByTestId("composer-input")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByTestId("composer-input"), "Build it");
    await user.click(screen.getByTestId("composer-send"));

    expect(sendMock).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId: "conv-1", payload: "Build it" }),
    );
    // composer cleared after send
    expect(screen.getByTestId("composer-input")).toHaveValue("");
    // first-message auto-rename PATCH fired (placeholder title)
    await waitFor(() => expect(patch).toHaveBeenCalled());
  });
});
