// =============================================================================
// File: layout.tsx
// Version: 3
// Path: ay_platform_ui/app/layout.tsx
// Description: Root layout. Wraps every page in two providers :
//                - <ConfigProvider> bootstraps runtime + UX config.
//                - <AuthProvider>   hydrates JWT from localStorage.
//
//              Order : Auth nested INSIDE Config so brand-aware
//              error rendering can read the config. Both run their
//              effects on mount in parallel.
//
//              v3 (2026-04-29) : adds AuthProvider.
// =============================================================================

import type { Metadata } from "next";

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
      </body>
    </html>
  );
}
