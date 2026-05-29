// =============================================================================
// File: setup.ts
// Version: 3
// Path: ay_platform_ui/tests/setup.ts
//
// v3 (2026-05-29): stub Element.scrollIntoView (absent in jsdom) so pages
// that auto-scroll a bottom anchor in an effect (the chat view) don't throw.
// Description: Vitest global setup. Loaded once per test process by
//              `vitest.config.ts:test.setupFiles`.
//
//              v2 (2026-04-29) adds MSW server lifecycle for the
//              integration tests : `setupServer` from `msw/node`
//              intercepts every fetch at the Node level so React
//              components see the mocked responses without code
//              change.
//
//              Sets up :
//                - `@testing-library/jest-dom` matchers
//                  (toBeInTheDocument, etc.)
//                - React Testing Library auto-cleanup between tests.
//                - localStorage / sessionStorage reset between tests.
//                - MSW server with default handlers in
//                  `tests/helpers/msw-handlers.ts` ; per-test
//                  overrides via `server.use()` auto-reset.
// =============================================================================

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";

import { server } from "./helpers/msw-server";

// jsdom does not implement Element.scrollIntoView. Pages that auto-scroll
// (e.g. the chat view's bottom anchor in an effect) call it on mount and
// would throw. Stub it to a no-op so those effects are inert under test.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

beforeAll(() => {
  // `error` on unhandled requests : we never want a silent network
  // call escaping the mocks. Tests that need to skip this can use
  // `server.use(http.all("*", () => HttpResponse.passthrough()))`.
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  cleanup();
  // Drop any per-test handler overrides ; default handlers stay.
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

beforeEach(() => {
  // Tests that need a populated localStorage opt in via
  // `window.localStorage.setItem(...)` in their own arrange block.
  if (typeof window !== "undefined") {
    window.localStorage.clear();
    window.sessionStorage.clear();
  }
});
