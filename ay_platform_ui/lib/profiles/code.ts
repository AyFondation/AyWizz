// =============================================================================
// File: code.ts
// Version: 3
// Path: ay_platform_ui/lib/profiles/code.ts
// Description: The `code` production profile — the only one shipped
//              in v1. Drives the project sidebar : Overview, Sources,
//              Conversations, Requirements, Pipeline, Validation,
//              Code source (artifacts), Settings.
//
//              v3 (2026-05-12) : new `pipeline` section that triggers
//              an orchestrator run (R-200-150..152) — goal input,
//              live phase tracking, Gate A approval, redirect to
//              Code source on completion.
//              v2 (2026-05-12) : new `artifacts` section labelled
//              "Code source" for the `code` profile (R-200-131). The
//              endpoint is profile-agnostic ; the future `docgen`
//              profile reuses the same section with a different label
//              ("Documents générés").
// =============================================================================

import type { ProfileDefinition } from "./types";

export const CODE_PROFILE: ProfileDefinition = {
  id: "code",
  label: "Code",
  tagline: "Source-driven requirements, validation and generation",
  sections: [
    {
      id: "overview",
      label: "Overview",
      path: "overview",
      iconName: "home",
      description: "Project summary, recent activity and quick stats.",
    },
    {
      id: "sources",
      label: "Sources",
      path: "sources",
      iconName: "folder",
      description: "Upload and manage the source corpus feeding RAG.",
    },
    {
      id: "conversations",
      label: "Conversations",
      path: "conversations",
      iconName: "chat",
      description: "Chat with the platform's RAG-augmented assistant.",
    },
    {
      id: "requirements",
      label: "Requirements",
      path: "requirements",
      iconName: "document",
      description: "Browse and edit the project's specification corpus.",
    },
    {
      id: "pipeline",
      label: "Pipeline",
      path: "pipeline",
      iconName: "lightning",
      description: "Trigger a generation run and review the plan before approval.",
    },
    {
      id: "validation",
      label: "Validation",
      path: "validation",
      iconName: "shield-check",
      description: "Run validation pipelines and inspect their findings.",
    },
    {
      id: "artifacts",
      label: "Code source",
      path: "artifacts",
      iconName: "folder",
      description: "Browse generated source files run by run.",
    },
    {
      id: "settings",
      label: "Settings",
      path: "settings",
      iconName: "cog",
      description: "Project metadata, members and integrations.",
    },
  ],
};
