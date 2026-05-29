// =============================================================================
// File: preferences.test.tsx
// Path: ay_platform_ui/tests/integration/preferences.test.tsx
// Description: Integration tests for the user Preferences page. useReadyConfig
//              throws until ready, so @/app/providers is mocked to return a
//              ready config ; useAuth comes from a real AuthProvider with a
//              seeded token. The C2 preferences endpoints are scripted via
//              MSW. Covers :
//                - hydrate from GET prefs (trigram / prompt / colour inputs +
//                  the override reset buttons) ;
//                - load error surfaces the amber banner ;
//                - trigram save (valid → PUT + saved msg ; invalid → client
//                  validation error, no PUT) and reset-to-default ;
//                - user-prompt save + reset ;
//                - bubble-colour save (invalid hex → error) + reset.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PreferencesPage from "@/app/(protected)/preferences/page";
import { AuthProvider } from "@/app/auth-provider";
import { fakeJWT } from "../helpers/msw-handlers";
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

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
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

const PREFS = "/api/v1/users/me/preferences";

function makePrefs(over: Partial<Record<string, unknown>> = {}) {
  return {
    trigram: "ABC",
    user_prompt: "be nice",
    user_prompt_is_default: false,
    user_color: "#aabbcc",
    ...over,
  };
}

function renderPrefs() {
  return render(
    <AuthProvider>
      <PreferencesPage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "jdupont",
      name: "Jean Dupont",
      tenant_id: "t1",
      roles: ["admin"],
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
});

afterEach(() => vi.restoreAllMocks());

describe("PreferencesPage hydration", () => {
  it("hydrates the three inputs + override reset buttons from the server prefs", async () => {
    server.use(http.get(PREFS, () => HttpResponse.json(makePrefs())));
    renderPrefs();

    await waitFor(() => expect(screen.getByTestId("trigram-input")).toHaveValue("ABC"));
    expect(screen.getByTestId("user-prompt-input")).toHaveValue("be nice");
    expect(screen.getByTestId("user-color-input")).toHaveValue("#aabbcc");
    // overrides present → reset buttons rendered
    expect(screen.getByTestId("trigram-reset")).toBeInTheDocument();
    expect(screen.getByTestId("user-prompt-reset")).toBeInTheDocument();
    expect(screen.getByTestId("user-color-reset")).toBeInTheDocument();
  });

  it("hides reset buttons when no overrides are set (defaults)", async () => {
    server.use(
      http.get(PREFS, () =>
        HttpResponse.json(
          makePrefs({ trigram: null, user_color: null, user_prompt_is_default: true }),
        ),
      ),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("preferences-trigram")).toBeInTheDocument());
    expect(screen.queryByTestId("trigram-reset")).not.toBeInTheDocument();
    expect(screen.queryByTestId("user-prompt-reset")).not.toBeInTheDocument();
    expect(screen.queryByTestId("user-color-reset")).not.toBeInTheDocument();
  });

  it("surfaces a load error", async () => {
    server.use(http.get(PREFS, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderPrefs();
    await waitFor(() => expect(screen.getByText(/Failed to load preferences/)).toBeInTheDocument());
  });
});

describe("PreferencesPage trigram", () => {
  it("saves a valid trigram (PUT + confirmation)", async () => {
    const put = vi.fn(() => HttpResponse.json(makePrefs({ trigram: "XYZ" })));
    server.use(
      http.get(PREFS, () => HttpResponse.json(makePrefs())),
      http.put(PREFS, put),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("trigram-input")).toHaveValue("ABC"));

    const user = userEvent.setup();
    await user.clear(screen.getByTestId("trigram-input"));
    await user.type(screen.getByTestId("trigram-input"), "xyz");
    await user.click(screen.getByTestId("trigram-save"));

    await waitFor(() =>
      expect(screen.getByTestId("preferences-saved")).toHaveTextContent(/saved/i),
    );
    expect(put).toHaveBeenCalled();
  });

  it("rejects an invalid trigram client-side without a PUT", async () => {
    const put = vi.fn(() => HttpResponse.json(makePrefs()));
    server.use(
      http.get(PREFS, () => HttpResponse.json(makePrefs())),
      http.put(PREFS, put),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("trigram-input")).toHaveValue("ABC"));

    const user = userEvent.setup();
    await user.clear(screen.getByTestId("trigram-input"));
    await user.type(screen.getByTestId("trigram-input"), "ab"); // too short
    await user.click(screen.getByTestId("trigram-save"));

    expect(screen.getByTestId("preferences-error")).toHaveTextContent(/3 to 4/);
    expect(put).not.toHaveBeenCalled();
  });

  it("resets the trigram to default", async () => {
    const put = vi.fn(() => HttpResponse.json(makePrefs({ trigram: null })));
    server.use(
      http.get(PREFS, () => HttpResponse.json(makePrefs())),
      http.put(PREFS, put),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("trigram-reset")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("trigram-reset"));
    await waitFor(() =>
      expect(screen.getByTestId("preferences-saved")).toHaveTextContent(/reset to default/i),
    );
    expect(put).toHaveBeenCalled();
  });
});

describe("PreferencesPage prompt + colour", () => {
  it("saves the user prompt", async () => {
    const put = vi.fn(() => HttpResponse.json(makePrefs({ user_prompt: "terse" })));
    server.use(
      http.get(PREFS, () => HttpResponse.json(makePrefs())),
      http.put(PREFS, put),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("user-prompt-input")).toHaveValue("be nice"));

    const user = userEvent.setup();
    await user.clear(screen.getByTestId("user-prompt-input"));
    await user.type(screen.getByTestId("user-prompt-input"), "terse");
    await user.click(screen.getByTestId("user-prompt-save"));

    await waitFor(() =>
      expect(screen.getByTestId("preferences-saved")).toHaveTextContent(/User prompt saved/),
    );
    expect(put).toHaveBeenCalled();
  });

  it("rejects an invalid bubble colour client-side", async () => {
    const put = vi.fn(() => HttpResponse.json(makePrefs()));
    server.use(
      http.get(PREFS, () => HttpResponse.json(makePrefs())),
      http.put(PREFS, put),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("user-color-input")).toHaveValue("#aabbcc"));

    const user = userEvent.setup();
    await user.clear(screen.getByTestId("user-color-input"));
    await user.type(screen.getByTestId("user-color-input"), "#zzz");
    await user.click(screen.getByTestId("user-color-save"));

    expect(screen.getByTestId("preferences-error")).toHaveTextContent(/7-character hex/);
    expect(put).not.toHaveBeenCalled();
  });

  it("resets the bubble colour", async () => {
    const put = vi.fn(() => HttpResponse.json(makePrefs({ user_color: null })));
    server.use(
      http.get(PREFS, () => HttpResponse.json(makePrefs())),
      http.put(PREFS, put),
    );
    renderPrefs();
    await waitFor(() => expect(screen.getByTestId("user-color-reset")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("user-color-reset"));
    await waitFor(() =>
      expect(screen.getByTestId("preferences-saved")).toHaveTextContent(/colour reset/i),
    );
    expect(put).toHaveBeenCalled();
  });
});
