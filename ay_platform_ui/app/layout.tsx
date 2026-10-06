// =============================================================================
// File: layout.tsx
// Version: 4
// Path: ay_platform_ui/app/layout.tsx
// Description: Root layout. Wraps every page in two providers :
//                - <ConfigProvider> bootstraps runtime + UX config.
//                - <AuthProvider>   hydrates JWT from localStorage.
//
//              Order : Auth nested INSIDE Config so brand-aware
//              error rendering can read the config. Both run their
//              effects on mount in parallel.
//
//              v4 (2026-10-06) : renders <SourceOffer> — the AGPL §13
//              network source offer — OUTSIDE the providers. It must appear
//              for every user interacting with the service remotely,
//              including unauthenticated ones on /login, so it cannot sit
//              behind the config bootstrap (which is why it is not part of
//              <BuildStamp>, whose navbar slot renders null until the
//              config is ready).
//
//              v3 (2026-04-29) : adds AuthProvider.
// =============================================================================

import type { Metadata } from "next";

import { SourceOffer } from "../components/source-offer";
import { AuthProvider } from "./auth-provider";
import { ConfigProvider } from "./providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "ay platform",
  description: "Requirements-driven artifact generation platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <ConfigProvider>
          <AuthProvider>{children}</AuthProvider>
        </ConfigProvider>
        {/* AGPL §13: offered to every network user, authenticated or not. */}
        <SourceOffer />
      </body>
    </html>
  );
}
