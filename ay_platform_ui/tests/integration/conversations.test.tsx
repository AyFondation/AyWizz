// =============================================================================
// File: conversations.test.tsx
// Path: ay_platform_ui/tests/integration/conversations.test.tsx
// Description: Integration tests for the project Conversations LIST page.
//              Uses useConfigState (→ renderWithProviders) + mocked
//              next/navigation and workspace-store, scripting the C3
//              conversation endpoints via MSW. Covers :
//                - ready list scoped to the active project (the C3 list
//                  endpoint returns the caller's conversations across
//                  ALL projects ; the page narrows client-side) ;
//                - empty + error states ;
//                - "New conversation" → create + navigate to the chat ;
//                - row rename (prompt → PATCH + refresh), delete
//                  (confirm → DELETE + refresh), "Open in Working area"
//                  navigation ;
//                - the user-initiated "Resume last conversation" link
//                  shown only when the stored active id is still listed.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConversationsListPage from "@/app/(protected)/projects/[pid]/conversations/page";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

const { mockRouter, uiHolder } = vi.hoisted(() => ({
  mockRouter: {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  },
  uiHolder: { activeConversationId: null as string | null },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useRouter: () => mockRouter,
  usePathname: () => "/projects/p1/conversations",
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
  useProjectUi: () => ({ ui: uiHolder }),
}));

const CONV_URL = "/api/v1/conversations";

function makeConv(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "c1",
    title: "First chat",
    project_id: "p1",
    message_count: 3,
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  mockRouter.push.mockClear();
  uiHolder.activeConversationId = null;
});

afterEach(() => vi.restoreAllMocks());

describe("ConversationsListPage list states", () => {
  it("lists only the active project's conversations with a count", async () => {
    server.use(
      http.get(CONV_URL, () =>
        HttpResponse.json({
          conversations: [makeConv(), makeConv({ id: "c2", project_id: "other-proj" })],
        }),
      ),
    );
    renderWithProviders(<ConversationsListPage />);

    await waitFor(() => expect(screen.getByTestId("conversations-list")).toBeInTheDocument());
    expect(screen.getByTestId("conversation-row-c1")).toBeInTheDocument();
    expect(screen.queryByTestId("conversation-row-c2")).not.toBeInTheDocument();
    expect(screen.getByTestId("conversations-count")).toHaveTextContent("1 conversation");
  });

  it("shows the empty-state when no conversation matches the project", async () => {
    server.use(
      http.get(CONV_URL, () =>
        HttpResponse.json({ conversations: [makeConv({ id: "c2", project_id: "other" })] }),
      ),
    );
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() =>
      expect(screen.getByTestId("conversations-empty-state")).toBeInTheDocument(),
    );
  });

  it("surfaces an HTTP error", async () => {
    server.use(http.get(CONV_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load: HTTP 500/)).toBeInTheDocument();
  });

  it("shows a Resume link only when the stored active id is still listed", async () => {
    uiHolder.activeConversationId = "c1";
    server.use(http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })));
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() => expect(screen.getByTestId("resume-last-conversation")).toBeInTheDocument());
    expect(screen.getByTestId("resume-last-conversation")).toHaveAttribute(
      "href",
      "/projects/p1/conversations/c1",
    );
  });
});

describe("ConversationsListPage actions", () => {
  it("creates a conversation and navigates to the chat", async () => {
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [] })),
      http.post(CONV_URL, () =>
        HttpResponse.json({ conversation: makeConv({ id: "new-1", title: "New conversation" }) }),
      ),
    );
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() => expect(screen.getByTestId("new-conversation-submit")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("new-conversation-submit"));

    await waitFor(() =>
      expect(mockRouter.push).toHaveBeenCalledWith("/projects/p1/conversations/new-1"),
    );
  });

  it("renames a row via prompt → PATCH + refresh", async () => {
    const patch = vi.fn(() => HttpResponse.json({ conversation: makeConv({ title: "Renamed" }) }));
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })),
      http.patch(`${CONV_URL}/c1`, patch),
    );
    vi.spyOn(window, "prompt").mockReturnValue("Renamed");
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() => expect(screen.getByTestId("conversation-rename-c1")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("conversation-rename-c1"));
    await waitFor(() => expect(patch).toHaveBeenCalled());
  });

  it("does not PATCH when the rename prompt is cancelled", async () => {
    const patch = vi.fn(() => HttpResponse.json({ conversation: makeConv() }));
    server.use(
      http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })),
      http.patch(`${CONV_URL}/c1`, patch),
    );
    vi.spyOn(window, "prompt").mockReturnValue(null); // cancelled
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() => expect(screen.getByTestId("conversation-rename-c1")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("conversation-rename-c1"));
    expect(patch).not.toHaveBeenCalled();
  });

  it("deletes a row after confirmation", async () => {
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    let listCalls = 0;
    server.use(
      http.get(CONV_URL, () => {
        listCalls += 1;
        return HttpResponse.json({ conversations: listCalls === 1 ? [makeConv()] : [] });
      }),
      http.delete(`${CONV_URL}/c1`, del),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() => expect(screen.getByTestId("conversation-delete-c1")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("conversation-delete-c1"));
    await waitFor(() => expect(del).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("conversations-empty-state")).toBeInTheDocument(),
    );
  });

  it("opens a conversation in the Working area", async () => {
    server.use(http.get(CONV_URL, () => HttpResponse.json({ conversations: [makeConv()] })));
    renderWithProviders(<ConversationsListPage />);
    await waitFor(() =>
      expect(screen.getByTestId("conversation-open-working-c1")).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    await user.click(screen.getByTestId("conversation-open-working-c1"));
    expect(mockRouter.push).toHaveBeenCalledWith("/projects/p1/working-area?conv=c1");
  });
});
