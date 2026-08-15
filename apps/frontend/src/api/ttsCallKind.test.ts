import { describe, expect, it, vi } from "vitest";

import { isTtsSynthesisBody, ttsCallKind } from "./ttsCallKind";

/**
 * [L1] `/api/tts` の呼び分け判定 — L4 が「音声が返ったか」を見る土台。
 *
 * 無いと何が静かに通るか: `/api/tts` に新しい呼び方が増えたとき、L4 の実環境シナリオが
 * その呼び出しを本合成と誤認し、`audio/wav` を期待して落ちる。実際 #132 (prefetch) と
 * #185 (plan) で 2 回起き、2 回目は deploy 3 連敗・毎朝の実環境チェック 2 晩の赤に
 * なった。「本合成はちょうど 1 つ」を実クライアントの出力で固定して再発を止める。
 */

describe("[L1] ttsCallKind", () => {
  it("本合成 / plan / prefetch を呼び分ける", () => {
    expect(ttsCallKind(JSON.stringify({ text: "a", speaker: 3, speedScale: 1 }))).toBe("synthesis");
    expect(ttsCallKind(JSON.stringify({ text: "a", speaker: 3, plan: true }))).toBe("plan");
    expect(
      ttsCallKind(JSON.stringify({ text: "a", speaker: 3, speedScale: 1, prefetch: true })),
    ).toBe("prefetch");
  });

  it("body が無い / 壊れていれば判定しない (誤って本合成に倒さない)", () => {
    expect(ttsCallKind(null)).toBeNull();
    expect(ttsCallKind("not json")).toBeNull();
    expect(isTtsSynthesisBody(null)).toBe(false);
  });
});

describe("[L1] 実クライアントの呼び方", () => {
  /** 3 種の fetch ヘルパーを実際に呼び、送信 body を集める。 */
  async function captureBodies(): Promise<string[]> {
    const bodies: string[] = [];
    const fetchSpy = vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(String(init?.body ?? ""));
      return new Response(null, { status: 204 });
    });
    vi.resetModules();
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubEnv("VITE_BFF_BASE_URL", "https://bff.example.com");
    vi.doMock("../auth/msal", () => ({ authEnabled: false, getAccessToken: vi.fn() }));
    const http = await import("./http");
    await http.ttsFetch("あ", 3, 1);
    await http.ttsPlanFetch("あ", 3);
    await http.ttsPrefetchFetch("あ", 3, 1);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    return bodies;
  }

  it("本合成と判定されるのはちょうど 1 つ", async () => {
    const bodies = await captureBodies();
    expect(bodies).toHaveLength(3);
    const synthesis = bodies.filter(isTtsSynthesisBody);
    expect(
      synthesis,
      "呼び方が増えたか、既存の呼び方が本合成と区別できなくなっている " +
        "(L4 が別の呼び出しを掴んで毎朝赤くなる)",
    ).toHaveLength(1);
  });

  it("3 種がそれぞれ別の呼び方として出る", async () => {
    const kinds = (await captureBodies()).map(ttsCallKind);
    expect(new Set(kinds)).toEqual(new Set(["synthesis", "plan", "prefetch"]));
  });
});
