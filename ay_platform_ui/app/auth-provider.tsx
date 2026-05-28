// =============================================================================
// File: auth-provider.tsx
// Version: 3
// Path: ay_platform_ui/app/auth-provider.tsx
// Description: Authentication state provider. Hydrates the JWT from
//              localStorage on mount, exposes a typed `useAuth()` hook
//              with login/logout helpers + decoded claims.
//
//              Three states :
//                - "loading"      : initial mount before hydration ;
//                                   render skeleton, NOT a redirect.
//                - "authenticated": valid non-expired token in storage.
//                - "anonymous"    : no token, or token expired/malformed.
//
//              Token storage layer lives in lib/apiClient.ts ; this
//              provider just orchestrates state. Login/logout flows :
//                login()  : ApiClient.login() → token → setToken(token)
//                logout() : clearAuth() → router.push("/login")
//
//              v3 : registers a session-revoked handler with
//              `ApiClient` so any 401 surfaced by C2 (token expired,
//              session wiped after a backend restart, key rotation)
//              transparently flips the state to "anonymous" — the
//              protected gate then redirects to /login instead of
//              every page rendering a raw "API 401 …" string.
//
//              v2 (2026-04-29) : adds a 60s expiration watchdog so a
//              user who stays on a protected page past the token's
//              `exp` is bounced to login proactively (instead of
//              waiting for the next API call to 401). The watchdog
//              transitions to "anonymous" ; ProtectedLayout owns the
//              redirect (with `?redirect=<current-path>` so the user
//              lands back where they were after re-auth).
// =============================================================================

"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import {
  clearStoredToken,
  readStoredToken,
  setSessionRevokedHandler,
  writeStoredToken,
} from "@/lib/apiClient";
import { decodeJWT, isTokenExpired, type JWTClaims } from "@/lib/auth";

export type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; token: string; claims: JWTClaims }
  | { status: "anonymous" };

interface AuthContextValue {
  state: AuthState;
  /** Persist the token + decode claims + transition to authenticated.
   *  Called by the login page after a successful POST /auth/login. */
  setToken: (token: string) => void;
  /** Forget the token + transition to anonymous. The caller is
   *  expected to navigate away (e.g. router.push("/login")). */
  clearAuth: () => void;
}

const AuthContext = createContext<AuthContextValue>({
  state: { status: "loading" },
  setToken: () => {
    /* default no-op — provider must wrap consumers */
  },
  clearAuth: () => {
    /* default no-op */
  },
});

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  // Hydrate from localStorage on mount. Runs ONCE per page-load
  // because the auth state is owned by the React tree from here on.
  useEffect(() => {
    const token = readStoredToken();
    if (!token) {
      setState({ status: "anonymous" });
      return;
    }
    const claims = decodeJWT(token);
    if (!claims || isTokenExpired(claims)) {
      clearStoredToken();
      setState({ status: "anonymous" });
      return;
    }
    setState({ status: "authenticated", token, claims });
  }, []);

  const setToken = useCallback((token: string) => {
    const claims = decodeJWT(token);
    if (!claims || isTokenExpired(claims)) {
      // Refuse to store a malformed / pre-expired token. Caller
      // surfaces the failure as a generic login error.
      clearStoredToken();
      setState({ status: "anonymous" });
      return;
    }
    writeStoredToken(token);
    setState({ status: "authenticated", token, claims });
  }, []);

  const clearAuth = useCallback(() => {
    clearStoredToken();
    setState({ status: "anonymous" });
  }, []);

  // Wire the session-revoked funnel : any 401 surfaced by `ApiClient`
  // on a token-bearing request calls back here, we clear the token,
  // and the protected gate's useEffect redirects to /login on the
  // next render. Re-registering on every mount keeps the handler
  // current after fast-refresh / route changes ; the unregister on
  // unmount avoids leaking a stale closure into the next session.
  useEffect(() => {
    setSessionRevokedHandler(() => {
      clearStoredToken();
      setState({ status: "anonymous" });
    });
    return () => {
      setSessionRevokedHandler(null);
    };
  }, []);

  // Expiration watchdog. Re-evaluates the current token's `exp` every
  // 60s ; on transition to expired, drops to anonymous so the
  // ProtectedLayout redirects with `?redirect=<current-path>`. The
  // 30s skew budget in `isTokenExpired` means we bounce slightly
  // before the server would reject the next request.
  useEffect(() => {
    if (state.status !== "authenticated") return;
    const intervalId = setInterval(() => {
      if (isTokenExpired(state.claims)) {
        clearStoredToken();
        setState({ status: "anonymous" });
      }
    }, 60_000);
    return () => clearInterval(intervalId);
  }, [state]);

  return (
    <AuthContext.Provider value={{ state, setToken, clearAuth }}>{children}</AuthContext.Provider>
  );
}
