// =============================================================================
// File: selection-context.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/selection-context.tsx
// Description: The ONE selection shared by every workbench region —
//              500-SPEC R-500-016, realising 310-SPEC R-310-221.
//
//              WHY THIS IS A REQUIREMENT AND NOT A PREFERENCE. Independent
//              per-panel state is what a React tree produces by default:
//              each panel keeps its own `useState` and the user re-finds the
//              same object five times. R-310-221's rationale is the cost of
//              that — "the user spends the interaction re-designating in
//              words what is already on screen". So the selection lives
//              above all five regions, and a region that wants to know what
//              is selected has no other place to look.
//
//              SELECTING AN OBJECT IMPLIES ITS CONTAINER. A panel that
//              selects an object without switching the container would leave
//              the document region showing a different document than the one
//              holding the selection — the split-brain this context exists
//              to prevent. `selectObject` therefore takes the container and
//              sets both.
//
//              THE CONVERSATION READS THE SAME CONTEXT. `asPromptContext`
//              is what the composer submits alongside the message so the
//              user never restates an identifier already on screen. It is a
//              method here rather than assembled in the composer so there is
//              one definition of "what the agent is being pointed at".
//
// @relation implements:R-310-221
// @relation implements:R-500-016
// =============================================================================

"use client";

import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

/** What the workbench is currently pointed at. Every field is nullable:
 *  the surface is usable before anything is chosen, and a region must
 *  render an empty state rather than assume a selection. */
export interface WorkbenchSelection {
  container: string | null;
  objectId: string | null;
  requirementId: string | null;
}

export interface SelectionContextValue extends WorkbenchSelection {
  /** Open a container. Clears the object selection, because an object id
   *  from the previous container does not exist in this one — keeping it
   *  would render a stale selection as a live one. */
  selectContainer: (container: string | null) => void;
  /** Select an object AND the container holding it (see header). */
  selectObject: (container: string, objectId: string) => void;
  /** Select a requirement. Does not clear the object: reading an object and
   *  the requirement it answers side by side is the normal case. */
  selectRequirement: (requirementId: string | null) => void;
  /** Select everything a finding points at in one call, so acting on a
   *  finding never leaves the regions disagreeing. */
  selectFinding: (subject: string, container?: string) => void;
  clear: () => void;
  /** True when nothing is selected — regions use it to render their empty
   *  state instead of a blank panel. */
  isEmpty: boolean;
  /** The selection as the conversation composer submits it (`R-500-016`). */
  asPromptContext: () => string;
}

const EMPTY: WorkbenchSelection = {
  container: null,
  objectId: null,
  requirementId: null,
};

const Ctx = createContext<SelectionContextValue | null>(null);

export function SelectionProvider({
  children,
  initial,
}: {
  children: ReactNode;
  initial?: Partial<WorkbenchSelection>;
}) {
  const [selection, setSelection] = useState<WorkbenchSelection>({
    ...EMPTY,
    ...initial,
  });

  const selectContainer = useCallback((container: string | null) => {
    setSelection((prev) => ({ ...prev, container, objectId: null }));
  }, []);

  const selectObject = useCallback((container: string, objectId: string) => {
    setSelection((prev) => ({ ...prev, container, objectId }));
  }, []);

  const selectRequirement = useCallback((requirementId: string | null) => {
    setSelection((prev) => ({ ...prev, requirementId }));
  }, []);

  const selectFinding = useCallback((subject: string, container?: string) => {
    setSelection((prev) => {
      // A finding's subject is either an object or a requirement. An object
      // id arrives with its container; a requirement id does not. Deciding
      // from the presence of the container rather than from the shape of the
      // identifier keeps this free of id-format guessing, which would break
      // the first time a project numbers its objects differently.
      if (container) {
        return { ...prev, container, objectId: subject };
      }
      return { ...prev, requirementId: subject };
    });
  }, []);

  const clear = useCallback(() => setSelection(EMPTY), []);

  const asPromptContext = useCallback(() => {
    const parts: string[] = [];
    if (selection.container) parts.push(`container ${selection.container}`);
    if (selection.objectId) parts.push(`object ${selection.objectId}`);
    if (selection.requirementId) {
      parts.push(`requirement ${selection.requirementId}`);
    }
    return parts.join(", ");
  }, [selection]);

  const value = useMemo<SelectionContextValue>(
    () => ({
      ...selection,
      selectContainer,
      selectObject,
      selectRequirement,
      selectFinding,
      clear,
      isEmpty: !selection.container && !selection.objectId && !selection.requirementId,
      asPromptContext,
    }),
    [
      selection,
      selectContainer,
      selectObject,
      selectRequirement,
      selectFinding,
      clear,
      asPromptContext,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Read the shared selection.
 *
 * Throws outside a provider rather than returning a default. A region
 * rendered outside the workbench with a silent empty selection would look
 * like it works and would never share anything — exactly the per-panel
 * state `R-500-016` exists to forbid, reintroduced invisibly.
 */
export function useSelection(): SelectionContextValue {
  const value = useContext(Ctx);
  if (value === null) {
    throw new Error(
      "useSelection must be used inside <SelectionProvider>. A workbench " +
        "region outside the provider would keep private state, which is " +
        "what R-500-016 forbids.",
    );
  }
  return value;
}
