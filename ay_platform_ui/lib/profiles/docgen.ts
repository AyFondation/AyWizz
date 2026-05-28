// =============================================================================
// File: docgen.ts
// Version: 2
// Path: ay_platform_ui/lib/profiles/docgen.ts
// Description: The `docgen` production profile — second profile shipped
//              after `code`. Targets the document-generation use case
//              (markdown, text, slide decks, spreadsheets) where the
//              pipeline runs through Conversations rather than an
//              explicit Pipeline page.
//
//              Sidebar layout : Overview, Sources, Conversations,
//              Working area, Documents, Settings. NO Pipeline /
//              Validation / Requirements — those belong to the `code`
//              profile.
//
//              Two distinct activities live on this profile :
//                - Documents : browse + (future) Gitea versioning.
//                - Working area : 3-pane (tree / viewer / chat) where
//                  the operator iterates with the assistant to create
//                  / modify documents. Same tree as Documents, same
//                  conversations as the Conversations sidebar entry.
//
//              v2 (2026-05-14) : adds the `working-area` section.
// =============================================================================

import type { ProfileDefinition } from "./types";

export const DOCGEN_PROFILE: ProfileDefinition = {
  id: "docgen",
  label: "DocGen",
  tagline: "Source-driven document generation through conversation",
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
      description: "Chat with the assistant to draft, refine or generate documents.",
    },
    {
      id: "working-area",
      label: "Working area",
      path: "working-area",
      iconName: "lightning",
      description: "3-pane workspace : browse the tree, view a document, chat to refine it.",
    },
    {
      id: "documents",
      // `path` deliberately reuses the `artifacts` route so the
      // existing surface (R-200-131 transparent backend) serves
      // DocGen with zero plumbing changes. Only the sidebar label
      // and the page header differ.
      label: "Documents",
      path: "artifacts",
      iconName: "document",
      description: "Browse generated documents run by run.",
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
