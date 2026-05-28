// =============================================================================
// File: render.tsx
// Version: 1
// Path: ay_platform_ui/tests/helpers/render.tsx
// Description: Test render helper for components that depend on the
//              <ConfigProvider> + <AuthProvider> tree. Wraps the unit
//              under test so individual integration tests don't have
//              to repeat the boilerplate.
//
//              Also exposes `mockRouter` — a vi.fn-backed router stub
//              that tests assert against (`expect(router.push).toHaveBeenCalledWith(...)`).
// =============================================================================

import { type RenderOptions, type RenderResult, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { vi } from "vitest";

import { AuthProvider } from "@/app/auth-provider";
import { ConfigProvider } from "@/app/providers";

export interface MockRouter {
  push: ReturnType<typeof vi.fn>;
  replace: ReturnType<typeof vi.fn>;
  back: ReturnType<typeof vi.fn>;
  refresh: ReturnType<typeof vi.fn>;
  prefetch: ReturnType<typeof vi.fn>;
}

/** Build a fresh mock router. Call once per test (or `beforeEach`)
 *  before importing the component under test, then assert against
 *  the returned spies. */
export function makeMockRouter(): MockRouter {
  return {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  };
}

interface ProvidersProps {
  children: ReactNode;
}

/** Wrapper component that nests both providers in the same order
 *  as the production root layout. */
function Providers({ children }: ProvidersProps) {
  return (
    <ConfigProvider>
      <AuthProvider>{children}</AuthProvider>
    </ConfigProvider>
  );
}

/** Drop-in replacement for `render` that includes the providers. */
export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult {
  return render(ui, { wrapper: Providers, ...options });
}
