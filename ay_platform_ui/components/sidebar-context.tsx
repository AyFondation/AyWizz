// =============================================================================
// File: sidebar-context.tsx
// Version: 1
// Path: ay_platform_ui/components/sidebar-context.tsx
// Description: Tiny context that pins the project-sidebar's `collapsed`
//              state at the project-shell layout level so both
//              `<Sidebar />` and the content area share a single source
//              of truth. Persists to localStorage on every toggle.
//              Hydrates from localStorage on first mount.
// =============================================================================

"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

const STORAGE_KEY = "aywizz.sidebar.collapsed";

interface SidebarCtx {
  collapsed: boolean;
  toggle: () => void;
}

const Ctx = createContext<SidebarCtx>({ collapsed: false, toggle: () => {} });

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "true") setCollapsed(true);
  }, []);

  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, String(next));
      }
      return next;
    });
  }, []);

  return <Ctx.Provider value={{ collapsed, toggle }}>{children}</Ctx.Provider>;
}

export function useSidebar(): SidebarCtx {
  return useContext(Ctx);
}
