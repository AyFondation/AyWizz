// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/preferences/page.tsx
// Description: User preferences page. Two server-backed sections :
//                - Trigram avatar (3-4 chars, default derived from
//                  the JWT name claim per the French convention).
//                - LLM user prompt (free-form instructions prepended
//                  ahead of any project/RAG content on every chat
//                  message).
//              Both fields support a 'Reset to default' affordance
//              when an override is stored. Persistence flows through
//              C2 `/api/v1/users/me/preferences` ; the trigram is
//              also write-through cached to localStorage so the
//              navbar avatar paints instantly without waiting on a
//              fetch.
// =============================================================================

"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";
import { useReadyConfig } from "@/app/providers";
import { Avatar } from "@/components/avatar";
import { ApiClient, ApiError } from "@/lib/apiClient";
import {
  defaultTrigramFromClaims,
  fullNameForTooltip,
  isValidTrigram,
  writePreferences,
} from "@/lib/preferences";
import type { UserPreferencesResponse } from "@/lib/types";
import { useAuth } from "../../auth-provider";

export default function PreferencesPage() {
  const { state } = useAuth();
  const cfg = useReadyConfig();
  const [trigram, setTrigram] = useState("");
  const [userPrompt, setUserPrompt] = useState("");
  const [userColor, setUserColor] = useState<string>("");
  const [prefs, setPrefs] = useState<UserPreferencesResponse | null>(null);
  const [defaultTrigram, setDefaultTrigram] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Default fallback hex used by the chat when no override is set.
  // Mirrors the Tailwind blue-500 the legacy Avatar palette draws
  // from — keeps the picker's "current" swatch meaningful even
  // before the user has saved anything.
  const FALLBACK_USER_COLOR = "#3b82f6";

  // Hydrate the form once auth is ready : derive the default trigram
  // from the JWT claims, then fetch the server-side prefs to learn
  // about overrides. The fetch may fail (auth-mode='none' deployments
  // don't expose the endpoint) — surface a loadError if so.
  useEffect(() => {
    if (state.status !== "authenticated") return;
    const dflt = defaultTrigramFromClaims(state.claims);
    setDefaultTrigram(dflt);
    const client = new ApiClient(cfg);
    let cancelled = false;
    client
      .getUserPreferences()
      .then((p) => {
        if (cancelled) return;
        setPrefs(p);
        setTrigram(p.trigram ?? dflt);
        setUserPrompt(p.user_prompt);
        setUserColor(p.user_color ?? FALLBACK_USER_COLOR);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiError
            ? `Failed to load preferences (${err.status})`
            : "Failed to load preferences",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [state, cfg]);

  if (state.status !== "authenticated") return null;
  const { claims } = state;
  const fullName = fullNameForTooltip(claims);

  async function saveTrigram(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setSavedMessage(null);
    setSaveError(null);
    const value = trigram.trim().toUpperCase();
    if (!isValidTrigram(value)) {
      setSaveError("Trigram must be 3 to 4 alphanumeric characters.");
      return;
    }
    setBusy(true);
    try {
      const client = new ApiClient(cfg);
      const updated = await client.updateUserPreferences({ trigram: value });
      setPrefs(updated);
      setTrigram(value);
      // Write-through to localStorage so the navbar avatar (read on
      // every render) picks up the change without a re-fetch.
      writePreferences(claims.sub, { trigram: value });
      setSavedMessage("Trigram saved.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resetTrigram(): Promise<void> {
    setSavedMessage(null);
    setSaveError(null);
    setBusy(true);
    try {
      const client = new ApiClient(cfg);
      // Empty string is the clear-override sentinel server-side.
      const updated = await client.updateUserPreferences({ trigram: "" });
      setPrefs(updated);
      setTrigram(defaultTrigram);
      writePreferences(claims.sub, { trigram: undefined });
      setSavedMessage(`Trigram reset to default (${defaultTrigram}).`);
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Reset failed (${err.status})` : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveUserPrompt(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setSavedMessage(null);
    setSaveError(null);
    const value = userPrompt;
    setBusy(true);
    try {
      const client = new ApiClient(cfg);
      const updated = await client.updateUserPreferences({ user_prompt: value });
      setPrefs(updated);
      setUserPrompt(updated.user_prompt);
      setSavedMessage("User prompt saved.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resetUserPrompt(): Promise<void> {
    setSavedMessage(null);
    setSaveError(null);
    setBusy(true);
    try {
      const client = new ApiClient(cfg);
      const updated = await client.updateUserPreferences({ user_prompt: "" });
      setPrefs(updated);
      setUserPrompt(updated.user_prompt);
      setSavedMessage("User prompt reset to default.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Reset failed (${err.status})` : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveUserColor(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setSavedMessage(null);
    setSaveError(null);
    const value = userColor.toLowerCase();
    if (!/^#[0-9a-f]{6}$/i.test(value)) {
      setSaveError("Colour must be a 7-character hex like #3b82f6.");
      return;
    }
    setBusy(true);
    try {
      const client = new ApiClient(cfg);
      const updated = await client.updateUserPreferences({ user_color: value });
      setPrefs(updated);
      setUserColor(updated.user_color ?? FALLBACK_USER_COLOR);
      setSavedMessage("Bubble colour saved.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resetUserColor(): Promise<void> {
    setSavedMessage(null);
    setSaveError(null);
    setBusy(true);
    try {
      const client = new ApiClient(cfg);
      const updated = await client.updateUserPreferences({ user_color: "" });
      setPrefs(updated);
      setUserColor(updated.user_color ?? FALLBACK_USER_COLOR);
      setSavedMessage("Bubble colour reset to default.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Reset failed (${err.status})` : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  const previewTrigram = isValidTrigram(trigram.trim().toUpperCase())
    ? trigram.trim().toUpperCase()
    : defaultTrigram;

  const userPromptIsDefault = prefs?.user_prompt_is_default ?? true;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Preferences</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Server-persisted settings — restored on every device when you sign in.
          </p>
        </div>
        <Link href="/profile" className="text-sm text-neutral-600 hover:underline">
          ← Profile
        </Link>
      </header>

      {loadError ? (
        <p className="mt-6 rounded border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          {loadError}
        </p>
      ) : null}

      <section
        className="mt-8 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="preferences-trigram"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          Trigram avatar
        </h2>
        <p className="mt-2 text-sm text-neutral-600">
          A 3-4 character identifier shown next to your messages and in the navbar. Default is
          derived from your name (e.g. <em>Jean Dupont</em> →{" "}
          <code className="rounded bg-neutral-100 px-1">DUJ</code>).
        </p>

        <div className="mt-6 flex items-center gap-4">
          <Avatar
            trigram={previewTrigram}
            fullName={fullName}
            variant="user"
            color={userColor || null}
          />
          <div className="text-sm">
            <p className="font-medium text-neutral-900">Preview</p>
            <p className="text-xs text-neutral-500">
              Hover the badge to see the tooltip (~1 s delay).
            </p>
          </div>
        </div>

        <form onSubmit={saveTrigram} className="mt-6 flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">
              Trigram (3-4 chars, alphanumeric)
            </span>
            <input
              type="text"
              value={trigram}
              onChange={(e) => {
                setTrigram(e.target.value);
                setSavedMessage(null);
              }}
              maxLength={4}
              placeholder={defaultTrigram}
              className="mt-1 block w-32 rounded-md border border-neutral-300 px-3 py-1.5 font-mono text-sm uppercase"
              data-testid="trigram-input"
              disabled={busy}
            />
          </label>
          <button
            type="submit"
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            data-testid="trigram-save"
            disabled={busy}
          >
            Save
          </button>
          {prefs?.trigram ? (
            <button
              type="button"
              onClick={resetTrigram}
              className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
              data-testid="trigram-reset"
              disabled={busy}
            >
              Reset to default ({defaultTrigram})
            </button>
          ) : null}
        </form>
      </section>

      <section
        className="mt-6 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="preferences-user-prompt"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          LLM user prompt
        </h2>
        <p className="mt-2 text-sm text-neutral-600">
          Free-form instructions prepended ahead of any project prompt and any retrieved context on
          every chat message. Useful for tone, language preference, or guardrails like &laquo;
          don&rsquo;t invent things, ask if you&rsquo;re unsure &raquo;.
        </p>

        <form onSubmit={saveUserPrompt} className="mt-4">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">
              Active user prompt {userPromptIsDefault ? "(default)" : "(your override)"}
            </span>
            <textarea
              value={userPrompt}
              onChange={(e) => {
                setUserPrompt(e.target.value);
                setSavedMessage(null);
              }}
              rows={5}
              maxLength={4000}
              className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-2 text-sm leading-relaxed"
              data-testid="user-prompt-input"
              disabled={busy}
            />
          </label>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="submit"
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              data-testid="user-prompt-save"
              disabled={busy}
            >
              Save
            </button>
            {!userPromptIsDefault ? (
              <button
                type="button"
                onClick={resetUserPrompt}
                className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
                data-testid="user-prompt-reset"
                disabled={busy}
              >
                Reset to default
              </button>
            ) : null}
            <span className="text-xs text-neutral-500">{userPrompt.length}/4000 characters</span>
          </div>
        </form>
      </section>

      <section
        className="mt-6 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="preferences-user-color"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          Bubble colour
        </h2>
        <p className="mt-2 text-sm text-neutral-600">
          Tint applied to your chat avatar and the bubble of messages you send. Used to tell
          collaborators apart in shared projects once those land.
        </p>

        <form onSubmit={saveUserColor} className="mt-4 flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">
              Hex colour {prefs?.user_color ? "(your override)" : "(default)"}
            </span>
            <div className="mt-1 flex items-center gap-2">
              <input
                type="color"
                value={userColor || FALLBACK_USER_COLOR}
                onChange={(e) => {
                  setUserColor(e.target.value);
                  setSavedMessage(null);
                }}
                className="h-9 w-12 cursor-pointer rounded-md border border-neutral-300"
                aria-label="Bubble colour swatch"
                data-testid="user-color-swatch"
                disabled={busy}
              />
              <input
                type="text"
                value={userColor}
                onChange={(e) => {
                  setUserColor(e.target.value);
                  setSavedMessage(null);
                }}
                maxLength={7}
                placeholder={FALLBACK_USER_COLOR}
                className="block w-28 rounded-md border border-neutral-300 px-3 py-1.5 font-mono text-sm uppercase"
                data-testid="user-color-input"
                disabled={busy}
              />
            </div>
          </label>
          <button
            type="submit"
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            data-testid="user-color-save"
            disabled={busy}
          >
            Save
          </button>
          {prefs?.user_color ? (
            <button
              type="button"
              onClick={resetUserColor}
              className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
              data-testid="user-color-reset"
              disabled={busy}
            >
              Reset to default
            </button>
          ) : null}
        </form>
      </section>

      {saveError ? (
        <p className="mt-4 text-sm text-red-700" role="alert" data-testid="preferences-error">
          {saveError}
        </p>
      ) : null}
      {savedMessage ? (
        <p className="mt-4 text-sm text-emerald-700" role="status" data-testid="preferences-saved">
          {savedMessage}
        </p>
      ) : null}

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-5 text-sm text-neutral-500">
        <p>Coming later :</p>
        <ul className="mt-1 list-disc pl-5">
          <li>Theme (light / dark / auto)</li>
          <li>Default landing section (overview / sources / conversations…)</li>
          <li>Notification preferences</li>
        </ul>
      </section>
    </main>
  );
}
