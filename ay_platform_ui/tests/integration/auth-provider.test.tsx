// =============================================================================
// File: auth-provider.test.tsx
// Version: 2
// Path: ay_platform_ui/tests/integration/auth-provider.test.tsx
// Description: Integration tests for <AuthProvider>. Exercises the
//              localStorage hydration paths + setToken/clearAuth
//              transitions through a small probe component that
//              renders the auth state as text.
//
//              These are integration (not unit) tests because they
//              run the actual provider wired to a JSDOM window +
//              localStorage — closer to runtime than mocked-state
//              unit tests.
//
//              v2 (2026-04-29) : adds the 60s expiration-watchdog
//              suite using fake timers. The watchdog SHALL detect
//              an exp boundary crossed mid-session and drop to
//              anonymous so the protected layout can redirect.
// =============================================================================

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/app/auth-provider";

import { fakeJWT } from "../helpers/msw-handlers";

const NOW = Math.floor(Date.now() / 1000);

/** Probe component that renders the auth state as JSON so tests can
 *  assert against text content. Buttons trigger setToken / clearAuth
 *  to exercise the transitions. */
function AuthProbe() {
  const { state, setToken, clearAuth } = useAuth();
  return (
    <div>
      <div data-testid="status">{state.status}</div>
      {state.status === "authenticated" ? (
        <>
          <div data-testid="user-id">{state.claims.sub}</div>
          <div data-testid="username">{state.claims.username ?? ""}</div>
        </>
      ) : null}
      <button
        type="button"
        data-testid="set-token"
        onClick={() =>
          setToken(
            fakeJWT({
              sub: "user-x",
              username: "x",
              exp: NOW + 3600,
              iat: NOW,
            }),
          )
        }
      >
        set-token
      </button>
      <button type="button" data-testid="clear-auth" onClick={clearAuth}>
        clear-auth
      </button>
    </div>
  );
}

describe("AuthProvider hydration", () => {
  it("starts in `loading`, then transitions to `anonymous` when localStorage is empty", async () => {
    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    // After useEffect runs, state has resolved.
    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("anonymous");
    });
  });

  it("hydrates to `authenticated` when a valid token sits in localStorage", async () => {
    const token = fakeJWT({
      sub: "user-alice",
      username: "alice",
      exp: NOW + 3600,
      iat: NOW,
    });
    window.localStorage.setItem("aywizz.token", token);

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("authenticated");
    });
    expect(screen.getByTestId("user-id")).toHaveTextContent("user-alice");
    expect(screen.getByTestId("username")).toHaveTextContent("alice");
  });

  it("falls back to `anonymous` and clears localStorage when the token is expired", async () => {
    const expired = fakeJWT({
      sub: "user-old",
      exp: NOW - 100, // already expired
      iat: NOW - 3700,
    });
    window.localStorage.setItem("aywizz.token", expired);

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("anonymous");
    });
    // The expired token SHALL be removed so subsequent boots don't
    // re-attempt the same broken hydration.
    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
  });

  it("falls back to `anonymous` when the token is malformed", async () => {
    window.localStorage.setItem("aywizz.token", "not-a-jwt");

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("anonymous");
    });
    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
  });
});

describe("AuthProvider setToken / clearAuth", () => {
  it("setToken transitions to authenticated AND persists to localStorage", async () => {
    const { getByTestId } = render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(getByTestId("status")).toHaveTextContent("anonymous");
    });

    act(() => {
      getByTestId("set-token").click();
    });

    await waitFor(() => {
      expect(getByTestId("status")).toHaveTextContent("authenticated");
    });
    expect(getByTestId("user-id")).toHaveTextContent("user-x");
    expect(window.localStorage.getItem("aywizz.token")).not.toBeNull();
  });

  it("setToken with a malformed token transitions to anonymous (defensive guard)", async () => {
    function MalformedSetterProbe() {
      const { state, setToken } = useAuth();
      return (
        <div>
          <div data-testid="status">{state.status}</div>
          <button type="button" data-testid="bad-set" onClick={() => setToken("not-a-jwt")}>
            bad-set
          </button>
        </div>
      );
    }
    const { getByTestId } = render(
      <AuthProvider>
        <MalformedSetterProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(getByTestId("status")).toHaveTextContent("anonymous");
    });

    act(() => {
      getByTestId("bad-set").click();
    });

    // Still anonymous — bad token rejected.
    expect(getByTestId("status")).toHaveTextContent("anonymous");
    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
  });

  it("clearAuth transitions back to anonymous AND clears localStorage", async () => {
    // Seed a valid token via direct localStorage write so the
    // provider hydrates as authenticated.
    window.localStorage.setItem(
      "aywizz.token",
      fakeJWT({ sub: "u", username: "u", exp: NOW + 3600, iat: NOW }),
    );
    const { getByTestId } = render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(getByTestId("status")).toHaveTextContent("authenticated");
    });

    act(() => {
      getByTestId("clear-auth").click();
    });

    expect(getByTestId("status")).toHaveTextContent("anonymous");
    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
  });
});

describe("AuthProvider expiration watchdog (60s tick)", () => {
  afterEach(() => {
    // Always restore real timers — a leaked fake-timer state would
    // freeze unrelated suites that rely on async waits.
    vi.useRealTimers();
  });

  it("transitions to anonymous + clears storage when exp passes mid-session", () => {
    // Token expires in 30 seconds (relative to the fake clock origin).
    // The fake-timer setup MUST be in place BEFORE render so that
    // AuthProvider's setInterval lands in the fake queue —
    // otherwise the interval is registered against the real timer
    // backend and `advanceTimersByTime` is a no-op.
    const T0_MS = 1_700_000_000_000;
    const T0_SEC = Math.floor(T0_MS / 1000);
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date(T0_MS));

    const tokenExpiringSoon = fakeJWT({
      sub: "user-soon",
      username: "soon",
      exp: T0_SEC + 60, // safely outside the 30s skew at T0
      iat: T0_SEC,
    });
    window.localStorage.setItem("aywizz.token", tokenExpiringSoon);

    const { getByTestId } = render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    // Hydration useEffect runs synchronously inside RTL's act() so
    // the state has resolved by the time render() returns.
    expect(getByTestId("status")).toHaveTextContent("authenticated");

    // Advance the system clock past exp+skew (token + 60s real, +30s
    // skew → bounce at +30s ; we jump to +120s to be unambiguous).
    vi.setSystemTime(new Date(T0_MS + 120_000));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(getByTestId("status")).toHaveTextContent("anonymous");
    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
  });

  it("stays authenticated while the token is still well within its `exp`", () => {
    const T0_MS = 1_700_000_000_000;
    const T0_SEC = Math.floor(T0_MS / 1000);
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date(T0_MS));

    window.localStorage.setItem(
      "aywizz.token",
      fakeJWT({
        sub: "user-fresh",
        username: "fresh",
        exp: T0_SEC + 3600, // 1h ahead of T0
        iat: T0_SEC,
      }),
    );

    const { getByTestId } = render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    expect(getByTestId("status")).toHaveTextContent("authenticated");

    // Several watchdog ticks within the token's lifetime — clock
    // stays well below `exp` so the watchdog SHALL be a no-op.
    act(() => {
      vi.advanceTimersByTime(60_000 * 5); // 5 minutes worth of ticks
    });

    expect(getByTestId("status")).toHaveTextContent("authenticated");
    expect(window.localStorage.getItem("aywizz.token")).not.toBeNull();
  });
});
