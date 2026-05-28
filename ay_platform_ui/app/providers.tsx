// =============================================================================
// File: providers.tsx
// Version: 1
// Path: ay_platform_ui/app/providers.tsx
// Description: Client-Component wrapper that bootstraps the platform
//              runtime + UX config on mount and exposes it to every
//              descendent via React Context. Children render the
//              appropriate state ('loading' / 'error' / 'ready').
//
//              Gates the entire UX behind a successful bootstrap so
//              individual pages don't have to handle "config not yet
//              loaded" branches.
// =============================================================================

"use client";

import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { bootstrapConfig } from "@/lib/runtimeConfig";
import type { PlatformConfig } from "@/lib/types";

type ConfigState =
  | { status: "loading" }
  | { status: "ready"; config: PlatformConfig }
  | { status: "error"; error: string };

const ConfigContext = createContext<ConfigState>({ status: "loading" });

/** Returns the raw state union — use this when you want to render
 *  loading / error UI alongside the ready state (e.g. the root layout). */
export function useConfigState(): ConfigState {
  return useContext(ConfigContext);
}

/** Returns the loaded config or throws — use this in pages that are
 *  guaranteed to render only after bootstrap succeeds. */
export function useReadyConfig(): PlatformConfig {
  const state = useContext(ConfigContext);
  if (state.status !== "ready") {
    throw new Error(`useReadyConfig: bootstrap not complete (status=${state.status})`);
  }
  return state.config;
}

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfigState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    bootstrapConfig()
      .then((config) => {
        if (!cancelled) setState({ status: "ready", config });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setState({ status: "error", error: msg });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <ConfigContext.Provider value={state}>{children}</ConfigContext.Provider>;
}
