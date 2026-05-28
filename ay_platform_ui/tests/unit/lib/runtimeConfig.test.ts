// =============================================================================
// File: runtimeConfig.test.ts
// Version: 1
// Path: ay_platform_ui/tests/unit/lib/runtimeConfig.test.ts
// Description: Unit tests for the bootstrap loader. We mock `fetch`
//              directly with vi.fn — the real wire format (relative
//              vs absolute URL) is what we want to pin, not actual
//              network behaviour.
// =============================================================================

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bootstrapConfig, ConfigError, loadRuntimeConfig, loadUxConfig } from "@/lib/runtimeConfig";

const RUNTIME_CONFIG_BODY = {
  apiBaseUrl: "https://api.example.com",
  publicBaseUrl: "https://app.example.com",
};

const UX_CONFIG_BODY = {
  api_version: "v1",
  auth_mode: "local",
  brand: {
    name: "AyWizz Platform",
    short_name: "AyWizz",
    accent_color_hex: "#3b82f6",
  },
  features: {
    chat_enabled: true,
    kg_enabled: true,
    cross_tenant_enabled: false,
    file_download_enabled: true,
  },
};

function mockFetchOK(body: unknown): ReturnType<typeof vi.fn> {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// loadRuntimeConfig — Stage 1
// ---------------------------------------------------------------------------

describe("loadRuntimeConfig", () => {
  it("fetches the static config from /runtime-config.json with no-cache", async () => {
    const fetchMock = mockFetchOK(RUNTIME_CONFIG_BODY);
    vi.stubGlobal("fetch", fetchMock);

    const cfg = await loadRuntimeConfig();

    expect(cfg).toEqual(RUNTIME_CONFIG_BODY);
    expect(fetchMock).toHaveBeenCalledWith(
      "/runtime-config.json",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("throws ConfigError on HTTP 404", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: () => Promise.reject(new Error("should not be called")),
      }),
    );

    await expect(loadRuntimeConfig()).rejects.toBeInstanceOf(ConfigError);
    await expect(loadRuntimeConfig()).rejects.toThrow(/HTTP 404/);
  });

  it("throws ConfigError on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await expect(loadRuntimeConfig()).rejects.toBeInstanceOf(ConfigError);
    await expect(loadRuntimeConfig()).rejects.toThrow(/network error/);
  });

  it("throws ConfigError on malformed JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.reject(new SyntaxError("invalid JSON")),
      }),
    );

    await expect(loadRuntimeConfig()).rejects.toBeInstanceOf(ConfigError);
    await expect(loadRuntimeConfig()).rejects.toThrow(/invalid JSON/);
  });

  it("includes the stage label in the error message for debuggability", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));

    await expect(loadRuntimeConfig()).rejects.toThrow(/stage=runtime/);
  });
});

// ---------------------------------------------------------------------------
// loadUxConfig — Stage 2
// ---------------------------------------------------------------------------

describe("loadUxConfig", () => {
  it("uses a relative URL when apiBaseUrl is empty (same-origin)", async () => {
    const fetchMock = mockFetchOK(UX_CONFIG_BODY);
    vi.stubGlobal("fetch", fetchMock);

    await loadUxConfig("");

    expect(fetchMock).toHaveBeenCalledWith(
      "/ux/config",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("builds an absolute URL when apiBaseUrl is set (cross-origin)", async () => {
    const fetchMock = mockFetchOK(UX_CONFIG_BODY);
    vi.stubGlobal("fetch", fetchMock);

    await loadUxConfig("https://api.example.com");

    expect(fetchMock).toHaveBeenCalledWith("https://api.example.com/ux/config", expect.anything());
  });

  it("propagates the stage label `ux` on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));

    await expect(loadUxConfig("")).rejects.toThrow(/stage=ux/);
  });

  it("returns the parsed UX config on success", async () => {
    vi.stubGlobal("fetch", mockFetchOK(UX_CONFIG_BODY));

    const ux = await loadUxConfig("");

    expect(ux.brand.name).toBe("AyWizz Platform");
    expect(ux.features.chat_enabled).toBe(true);
    expect(ux.auth_mode).toBe("local");
  });
});

// ---------------------------------------------------------------------------
// bootstrapConfig — Stage 1 + Stage 2 wired
// ---------------------------------------------------------------------------

describe("bootstrapConfig", () => {
  it("runs both stages and returns the combined PlatformConfig", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        call += 1;
        if (call === 1) {
          // Stage 1 : runtime config
          expect(url).toBe("/runtime-config.json");
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(RUNTIME_CONFIG_BODY),
          });
        }
        // Stage 2 : ux config — must use the apiBaseUrl from stage 1
        expect(url).toBe("https://api.example.com/ux/config");
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(UX_CONFIG_BODY),
        });
      }),
    );

    const cfg = await bootstrapConfig();

    expect(cfg.runtime).toEqual(RUNTIME_CONFIG_BODY);
    expect(cfg.ux.api_version).toBe("v1");
    expect(call).toBe(2);
  });

  it("propagates Stage 1 failure without calling Stage 2", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: false, status: 404 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapConfig()).rejects.toBeInstanceOf(ConfigError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("propagates Stage 2 failure as a ConfigError", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        call += 1;
        if (call === 1) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(RUNTIME_CONFIG_BODY),
          });
        }
        return Promise.resolve({ ok: false, status: 500 });
      }),
    );

    await expect(bootstrapConfig()).rejects.toThrow(/stage=ux/);
  });
});
