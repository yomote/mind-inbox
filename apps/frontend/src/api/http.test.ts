import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * [L1] BFF 直叩きヘルパーの結線テスト。
 *
 * 無いと何が静かに通るか: fetch が相対パス (SWA オリジン) に退行しても、mock e2e は
 * BFF を呼ばず live E2E はデプロイ後にしか走らないため、通常 CI が緑のまま
 * 「ずんだもんが鳴らない」(2026-08-08 実事象) が出荷される。
 */

vi.mock("../auth/msal", () => ({
  authEnabled: true,
  getAccessToken: vi.fn(async () => "test-token"),
}));

describe("[L1] ttsFetch", () => {
  const fetchSpy = vi.fn(async () => new Response(null, { status: 200 }));

  beforeEach(() => {
    // isolate:false では module registry がワーカー内で共有される。#137 以降 api 各モジュールが
    // ./http を import するため、msal を mock しない他テストが先に走ると実 msal に束縛された
    // http.ts がキャッシュされ、下の動的 import がそれを掴む (CI で実際に発生した順序依存 fail)。
    // レジストリを破棄してから import し、vi.mock("../auth/msal") が必ず効く状態にする。
    vi.resetModules();
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubEnv("VITE_BFF_BASE_URL", "https://bff.example.com");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    fetchSpy.mockClear();
  });

  it("BFF ベース URL を前置し、Authorization を付けて /api/tts を呼ぶ", async () => {
    const { ttsFetch } = await import("./http");
    await ttsFetch("テストなのだ", 3);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    // 相対 /api/tts への退行 (SWA オリジン行き) をここで止める
    expect(url).toBe("https://bff.example.com/api/tts");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer test-token");
    expect(JSON.parse(String(init.body))).toEqual({ text: "テストなのだ", speaker: 3 });
  });
});
