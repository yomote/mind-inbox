/**
 * [単体] voicevoxClient — wrapper へ渡すリクエスト body の形。
 *
 * 無いと何が静かに通るか: wrapper (FastAPI + pydantic) の `SynthesizeRequest` は
 * **知らないフィールドを黙って捨てる**。`speed_scale` を `speedScale` (camelCase) で
 * 送る / 送り忘れると、422 にもならず等倍の WAV が普通に返る — 設定画面のスライダーを
 * 動かしても音だけが変わらない状態になり、BFF 側の service テスト (client は mock) では
 * 絶対に見つからない。**BFF と wrapper の名前の約束をここで固定する。**
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockConfig = vi.hoisted(
  () => ({}) as { voicevoxBaseUrl?: string; voicevoxAudience?: string },
);

vi.mock("../config", () => ({ config: mockConfig }));

import { isConfigured, synthesize } from "./voicevoxClient";

/** 直近の fetch 呼び出しの body を JSON として読む。 */
function sentBody(fetchSpy: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

function stubWrapper(): ReturnType<typeof vi.fn> {
  const fetchSpy = vi.fn(async () => new Response(new Uint8Array([1, 2]), { status: 200 }));
  vi.stubGlobal("fetch", fetchSpy);
  return fetchSpy;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  delete mockConfig.voicevoxBaseUrl;
  delete mockConfig.voicevoxAudience;
});

describe("[単体] voicevoxClient — speed_scale の受け渡し (#242)", () => {
  it("speedScale は wrapper の snake_case (speed_scale) で送る", async () => {
    mockConfig.voicevoxBaseUrl = "http://voicevox.example";
    const fetchSpy = stubWrapper();

    await synthesize({ text: "あ", speakerId: 3, speedScale: 1.4 });

    expect(sentBody(fetchSpy)).toEqual({ text: "あ", speaker: 3, speed_scale: 1.4 });
  });

  it("速度未指定なら speed_scale を送らない (wrapper の既定に委ねる)", async () => {
    mockConfig.voicevoxBaseUrl = "http://voicevox.example";
    const fetchSpy = stubWrapper();

    await synthesize({ text: "あ", speakerId: 3 });

    expect(sentBody(fetchSpy)).toEqual({ text: "あ", speaker: 3 });
  });

  it("VOICEVOX_BASE_URL 未設定なら叩かずに null (stub fallback を壊さない)", async () => {
    const fetchSpy = stubWrapper();

    expect(isConfigured()).toBe(false);
    expect(await synthesize({ text: "あ", speedScale: 1.4 })).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
