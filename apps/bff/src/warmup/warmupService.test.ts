/**
 * [L2] warmup サービスの service-level test。
 *
 * 無いと何が静かに通るか: warmup が下流 /health を叩かない・失敗を投げる退行が
 * 起きても、フロントは fire-and-forget なので誰も気づかず「コールドスタートが
 * 直らない」まま緑で通る。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockConfig = vi.hoisted(
  () =>
    ({}) as {
      aiAgentBaseUrl?: string;
      voicevoxBaseUrl?: string;
      aiAgentAudience?: string;
      voicevoxAudience?: string;
    },
);

vi.mock("../config", () => ({ config: mockConfig }));

import { warmupDownstreams } from "./warmupService";

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  delete mockConfig.aiAgentBaseUrl;
  delete mockConfig.voicevoxBaseUrl;
});

describe("[L2] warmupDownstreams", () => {
  it("URL 未設定 (ローカル/未結線) は stub — fetch を撃たない", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    const result = await warmupDownstreams();

    expect(result.aiAgent).toEqual({ status: "stub", ms: null, httpStatus: null });
    expect(result.voicevox).toEqual({ status: "stub", ms: null, httpStatus: null });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("両下流の /health を並行で叩き、実測 ms を返す", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai.example";
    mockConfig.voicevoxBaseUrl = "http://vv.example";
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const result = await warmupDownstreams();

    expect(fetchSpy.mock.calls.map((c) => c[0])).toEqual(
      expect.arrayContaining(["http://ai.example/health", "http://vv.example/health"]),
    );
    expect(result.aiAgent.status).toBe("ok");
    expect(result.aiAgent.httpStatus).toBe(200);
    expect(typeof result.aiAgent.ms).toBe("number");
    expect(result.voicevox.status).toBe("ok");
  });

  it("片方が失敗しても throw せず error として報告する (もう片方は温まる)", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai.example";
    mockConfig.voicevoxBaseUrl = "http://vv.example";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).startsWith("http://ai.example")) throw new Error("cold start timeout");
        return new Response("{}", { status: 200 });
      }),
    );

    const result = await warmupDownstreams();

    expect(result.aiAgent.status).toBe("error");
    expect(result.voicevox.status).toBe("ok");
  });
});
