// =============================================================================
// File: sidebar.tsx
// Version: 1
// Path: ay_platform_ui/components/sidebar.tsx
// Description: Project-scoped left sidebar listing the active profile's
//              sections. Three responsive modes :
//                - < md (mobile)  : hidden by default ; drawer overlay
//                                   on burger-button tap, dimmed backdrop.
//                - md ≤ x < lg    : icon-only by default (~56px wide) ;
//                                   tooltip on hover ; expandable.
//                - ≥ lg (desktop) : expanded by default (~240px) ;
//                                   collapse button toggles icon-only.
//              Collapsed/expanded preference persists in localStorage
//              (`aywizz.sidebar.collapsed`).
// =============================================================================

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import type { ProfileDefinition, SectionIcon } from "@/lib/profiles/types";

import { useSidebar } from "./sidebar-context";

interface SidebarProps {
  profile: ProfileDefinition;
  projectId: string;
}

export function Sidebar({ profile, projectId }: SidebarProps) {
  const pathname = usePathname();
  const { collapsed: isCollapsed, toggle: toggleCollapsed } = useSidebar();
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <>
      {/* Mobile burger button — visible only below md. */}
      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        className="fixed left-3 top-3 z-30 rounded-md border border-neutral-300 bg-white p-2 shadow-sm md:hidden"
        aria-label="Open navigation"
        data-testid="sidebar-burger"
      >
        <BurgerIcon />
      </button>

      {/* Backdrop for mobile drawer. */}
      {drawerOpen ? (
        <button
          type="button"
          onClick={() => setDrawerOpen(false)}
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          aria-label="Close navigation"
        />
      ) : null}

      {/* Sidebar itself. Three layered behaviors via Tailwind responsive
       *  classes : drawer (<md), iconified (md), expanded (≥lg). */}
      <aside
        className={[
          // Base layout : fixed on the left side, full height.
          "fixed left-0 top-0 z-40 flex h-screen flex-col border-r border-neutral-200 bg-white transition-all duration-200",
          // Mobile : translate off-screen unless drawer open ; full width inside.
          drawerOpen ? "translate-x-0" : "-translate-x-full",
          "w-56", // default mobile drawer width
          // md+ : always visible, width depends on collapsed.
          "md:translate-x-0",
          isCollapsed ? "md:w-14" : "md:w-56",
        ].join(" ")}
        data-testid="sidebar"
        data-collapsed={isCollapsed ? "true" : "false"}
      >
        {/* Header : brand + collapse toggle (visible from md). */}
        <div className="flex h-14 items-center justify-between border-b border-neutral-200 px-3">
          {!isCollapsed ? (
            <Link href="/projects" className="text-sm font-semibold text-neutral-900">
              ← All projects
            </Link>
          ) : (
            <Link
              href="/projects"
              className="mx-auto text-neutral-700 hover:text-neutral-900"
              aria-label="All projects"
              title="All projects"
            >
              <BackIcon />
            </Link>
          )}
          <button
            type="button"
            onClick={toggleCollapsed}
            className="hidden rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 md:block"
            aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            data-testid="sidebar-toggle"
          >
            {isCollapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
          </button>
        </div>

        {/* Sections nav. */}
        <nav className="flex-1 overflow-y-auto py-3" aria-label="Project sections">
          <ul className="space-y-0.5 px-2">
            {profile.sections.map((section) => {
              const href = `/projects/${encodeURIComponent(projectId)}/${section.path}`;
              const active = pathname?.startsWith(href);
              return (
                <li key={section.id}>
                  <Link
                    href={href}
                    onClick={() => setDrawerOpen(false)}
                    title={isCollapsed ? section.label : undefined}
                    className={[
                      "flex items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors",
                      active ? "bg-blue-50 text-blue-900" : "text-neutral-700 hover:bg-neutral-100",
                      isCollapsed ? "justify-center md:px-0" : "",
                    ].join(" ")}
                    data-testid={`sidebar-link-${section.id}`}
                    data-active={active ? "true" : "false"}
                  >
                    <span className="shrink-0">
                      <SectionIconRender name={section.iconName} />
                    </span>
                    {!isCollapsed ? <span>{section.label}</span> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// Inline icons — kept here so the sidebar has no extra dep. Add an entry
// to `SectionIcon` and a case here when a new section icon is needed.
// All icons are 20×20 SVG, currentColor.
// ---------------------------------------------------------------------------

function SectionIconRender({ name }: { name: SectionIcon }) {
  switch (name) {
    case "home":
      return <HomeIcon />;
    case "folder":
      return <FolderIcon />;
    case "chat":
      return <ChatIcon />;
    case "document":
      return <DocumentIcon />;
    case "shield-check":
      return <ShieldCheckIcon />;
    case "cog":
      return <CogIcon />;
    case "lightning":
      return <LightningIcon />;
  }
}

const svgProps = {
  xmlns: "http://www.w3.org/2000/svg",
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

function HomeIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Home</title>
      <path d="M3 12 12 4l9 8" />
      <path d="M5 10v10h14V10" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Folder</title>
      <path d="M3 7h6l2 2h10v10H3z" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Chat</title>
      <path d="M21 12c0 4-4 7-9 7-1.6 0-3-.3-4.3-.8L3 20l1-4c-1-1.3-1.5-2.8-1.5-4 0-4 4-7 9-7s9.5 3 9.5 7z" />
    </svg>
  );
}

function DocumentIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Document</title>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M15 3v4h4" />
      <path d="M8 12h8M8 16h6" />
    </svg>
  );
}

function ShieldCheckIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Shield check</title>
      <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

function CogIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Cog</title>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .4 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.4 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .4-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.4-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.4h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h.1a1.7 1.7 0 0 0 1.9-.4l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.4 1.9v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}

function LightningIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Lightning</title>
      <path d="M13 3 4 14h7l-1 7 9-11h-7z" />
    </svg>
  );
}

function BurgerIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Menu</title>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Collapse</title>
      <path d="m15 6-6 6 6 6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Expand</title>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg {...svgProps} aria-hidden="true">
      <title>Back to projects</title>
      <path d="M19 12H5M12 5l-7 7 7 7" />
    </svg>
  );
}
