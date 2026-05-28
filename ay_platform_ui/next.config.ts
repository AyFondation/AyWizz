// =============================================================================
// File: next.config.ts
// Version: 2
// Path: ay_platform_ui/next.config.ts
// Description: Next.js configuration for the platform UI.
//
//              v2 (2026-04-29): adds `/ux/*` (required by the runtime-
//              config bootstrap that hits `GET /ux/config`) plus
//              `/api/v1/*` passthrough and `/admin/*`. Removed the
//              v1 `/api/platform/*` rewrite shape — making the UX
//              know the rewrite would defeat the runtime-config
//              decoupling.
//
//              In PROD (K8s), Traefik handles all routing same-origin
//              and these rewrites are inactive. In DEV, the env var
//              `NEXT_PUBLIC_PLATFORM_BASE_URL` (e.g.
//              `http://localhost:56000`) makes the dev server proxy
//              backend calls so the browser sees same-origin.
// =============================================================================

import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // `standalone` produces a self-contained server bundle in
  // `.next/standalone/` that the production Dockerfile.ui copies
  // forward. Without it, the prod image would need the entire
  // node_modules tree (~hundreds of MB).
  output: "standalone",
  async rewrites() {
    const base = process.env.NEXT_PUBLIC_PLATFORM_BASE_URL;
    if (!base) return [];
    return [
      { source: "/auth/:path*", destination: `${base}/auth/:path*` },
      { source: "/ux/:path*", destination: `${base}/ux/:path*` },
      { source: "/admin/:path*", destination: `${base}/admin/:path*` },
      { source: "/api/v1/:path*", destination: `${base}/api/v1/:path*` },
    ];
  },
};

export default config;
