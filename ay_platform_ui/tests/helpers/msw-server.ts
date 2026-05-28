// =============================================================================
// File: msw-server.ts
// Version: 1
// Path: ay_platform_ui/tests/helpers/msw-server.ts
// Description: MSW Node server for Vitest integration tests. The server
//              intercepts `fetch` calls at the Node level so React
//              components rendered via @testing-library/react see the
//              mocked responses without any code change to the
//              components themselves.
//
//              Lifecycle (wired in tests/setup.ts) :
//                beforeAll  : server.listen()
//                afterEach  : server.resetHandlers() — drops per-test
//                             overrides, keeps defaults
//                afterAll   : server.close()
// =============================================================================

import { setupServer } from "msw/node";

import { defaultHandlers } from "./msw-handlers";

export const server = setupServer(...defaultHandlers);
