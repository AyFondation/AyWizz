// =============================================================================
// File: quota-indicator.tsx
// Version: 2
// Path: ay_platform_ui/components/quota-indicator.tsx
// Description: Always-on personal quota pill in the navbar. v2 makes it
//              OPERATIONAL: the pill shows the SESSION window framed as
//              "X% left" or "X% used" (per the user's `quotaFraming`
//              preference) plus the rolling-window reset countdown
//              ("resets in 1h35"). The popover breaks every window down into
//              consumed AND remaining (cost + tokens). Polls `GET
//              /api/v1/quota/me` every minute. Visible to every authenticated
//              user (a tenant shares its quota).
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useConfigState } from "@/app/providers";
import { ApiClient } from "@/lib/apiClient";
import { getQuotaFraming, type QuotaFraming } from "@/lib/preferences";
import type { QuotaStatus, QuotaWindowStatus } from "@/lib/types";

const POLL_MS = 60_000;

/** The most constrained dimension of a window, or null when it has no limit. */
function windowPct(w: QuotaWindowStatus): number | null {
  const ps = [w.cost_pct, w.tokens_pct].filter((p): p is number => p !== null);
  return ps.length ? Math.max(...ps) : null;
}

/** Seconds → compact human duration: "1h35", "25m", "<1m". */
function fmtDuration(seconds: number): string {
  if (seconds < 60) return "<1m";
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h${String(rem).padStart(2, "0")}` : `${h}h`;
}

/** The window the pill summarises: the explicit "session" window, else the
 *  shortest-duration one (the most immediate operational horizon). */
function pillWindow(windows: QuotaWindowStatus[]): QuotaWindowStatus | null {
  if (!windows.length) return null;
  return (
    windows.find((w) => w.key === "session") ??
    [...windows].sort((a, b) => a.duration_seconds - b.duration_seconds)[0]
  );
}

export function QuotaIndicator() {
  const configState = useConfigState();
  const { state: authState } = useAuth();
  const [status, setStatus] = useState<QuotaStatus | null>(null);
  const [open, setOpen] = useState(false);

  const apiClient = useMemo(
    () => (configState.status === "ready" ? new ApiClient(configState.config) : null),
    [configState],
  );
  const authed = authState.status === "authenticated";
  const sub = authState.status === "authenticated" ? authState.claims.sub : "";
  const framing: QuotaFraming = useMemo(() => (sub ? getQuotaFraming(sub) : "remaining"), [sub]);

  const refresh = useCallback(() => {
    if (!apiClient) return;
    apiClient
      .getMyQuota()
      .then(setStatus)
      .catch(() => {
        /* best-effort: a transient failure just leaves the last value */
      });
  }, [apiClient]);

  useEffect(() => {
    if (!authed || !apiClient) return;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [authed, apiClient, refresh]);

  if (!authed || status === null) return null;

  const session = pillWindow(status.windows);
  const sessionPct = session ? windowPct(session) : null;

  // Pill label: framing-aware % on the session window, with the reset countdown.
  let label: string;
  let pillCls: string;
  if (status.blocked) {
    label = "Quota exceeded";
    pillCls = "border-red-200 bg-red-50 text-red-700";
  } else if (session && sessionPct !== null) {
    const shown = framing === "remaining" ? Math.max(0, 100 - sessionPct) : sessionPct;
    const verb = framing === "remaining" ? "left" : "used";
    const reset =
      typeof session.seconds_until_reset === "number"
        ? ` · resets in ${fmtDuration(session.seconds_until_reset)}`
        : "";
    label = `${Math.round(shown)}% ${verb}${reset}`;
    pillCls = status.warned
      ? "border-amber-200 bg-amber-50 text-amber-700"
      : "border-emerald-200 bg-emerald-50 text-emerald-700";
  } else {
    // No limit configured → show the session window's raw spend (informational).
    label = session ? `$${session.usage_cost_usd.toFixed(2)} used` : "Usage";
    pillCls = "border-neutral-200 bg-neutral-50 text-neutral-600";
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`rounded-full border px-3 py-1 text-xs font-medium ${pillCls}`}
        data-testid="quota-indicator"
        aria-label="Your LLM quota usage"
      >
        {label} ▾
      </button>
      {open && (
        <div
          className="absolute right-0 z-20 mt-1 w-80 rounded-md border border-neutral-200 bg-white p-3 shadow-lg"
          data-testid="quota-popover"
        >
          <p className="text-xs font-semibold text-neutral-700">Your tenant usage</p>
          <ul className="mt-2 space-y-3">
            {status.windows.map((w) => (
              <QuotaWindowRow key={w.key} w={w} />
            ))}
          </ul>
          <p className="mt-2 border-t border-neutral-100 pt-2 text-[11px] text-neutral-400">
            Framing ({framing === "remaining" ? "remaining" : "consumed"}) is set in Preferences.
            Everyone in your tenant shares this quota.
          </p>
        </div>
      )}
    </div>
  );
}

function QuotaWindowRow({ w }: { w: QuotaWindowStatus }) {
  const p = windowPct(w);
  const barCls =
    w.state === "exceeded" ? "bg-red-500" : w.state === "warn" ? "bg-amber-500" : "bg-emerald-500";
  const costLeft = w.max_cost_usd !== null ? Math.max(0, w.max_cost_usd - w.usage_cost_usd) : null;
  const tokLeft = w.max_tokens !== null ? Math.max(0, w.max_tokens - w.usage_tokens) : null;
  return (
    <li data-testid={`quota-pop-${w.key}`}>
      <div className="flex justify-between text-xs text-neutral-600">
        <span className="font-medium">{w.label}</span>
        <span>
          {p !== null ? `${Math.round(p)}% used` : "no limit"}
          {typeof w.seconds_until_reset === "number" && (
            <span className="text-neutral-400">
              {" "}
              · resets in {fmtDuration(w.seconds_until_reset)}
            </span>
          )}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded bg-neutral-100">
        {p !== null && (
          <div className={`h-1.5 rounded ${barCls}`} style={{ width: `${Math.min(100, p)}%` }} />
        )}
      </div>
      {/* Consumed AND remaining, on both dimensions. */}
      <div className="mt-1 grid grid-cols-2 gap-x-3 text-[11px] text-neutral-500">
        <span data-testid={`quota-consumed-${w.key}`}>
          Used ${w.usage_cost_usd.toFixed(2)}
          {w.max_cost_usd !== null ? ` / $${w.max_cost_usd}` : ""} · {w.usage_tokens} tok
        </span>
        <span className="text-right" data-testid={`quota-remaining-${w.key}`}>
          {costLeft !== null || tokLeft !== null
            ? `Left ${costLeft !== null ? `$${costLeft.toFixed(2)}` : "—"}${
                tokLeft !== null ? ` · ${tokLeft} tok` : ""
              }`
            : "no limit set"}
        </span>
      </div>
      {/* Per-level breakdown (global / tenant / project / you). */}
      {w.levels.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-neutral-400">
          {w.levels.map((lv) => {
            const lp = Math.max(
              ...[lv.cost_pct, lv.tokens_pct].filter((x): x is number => x !== null),
              -1,
            );
            return (
              <span key={lv.level} data-testid={`quota-level-${w.key}-${lv.level}`}>
                {LEVEL_LABEL[lv.level]}{" "}
                {lp >= 0 ? `${Math.round(lp)}%` : `$${lv.usage_cost_usd.toFixed(2)}`}
              </span>
            );
          })}
        </div>
      )}
    </li>
  );
}

const LEVEL_LABEL: Record<string, string> = {
  global: "Platform",
  tenant: "Tenant",
  project: "Project",
  user: "You",
};
