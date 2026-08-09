/**
 * [L2] speech.issueToken (ADR 0023) の service-level test。
 *
 * パターン: appRouter.createCaller(ctx) で HTTP レイヤをバイパスし、
 * 外部依存 (config / MSI トークン取得) は vi.mock で stub する。
 *
 * ここで test しないこと:
 *   - MSI エンドポイントとの実 HTTP (serviceToken.test.ts の範疇)
 *   - Speech SDK がトークンを受理するか (実 Azure でしか検証できない — PR の動作検証欄参照)
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockConfig = {
  speechResourceId: undefined as string | undefined,
  speechRegion: undefined as string | undefined,
};

vi.mock("../config", () => ({
  get config() {
    return mockConfig;
  },
}));

vi.mock("../clients/serviceToken", () => ({
  getServiceToken: vi.fn(),
}));

import { getServiceToken } from "../clients/serviceToken";
import { InMemoryHistoryRepository } from "../repositories/historyRepository";
import { InMemoryProblemRepository } from "../repositories/problemRepository";
import type { TrpcContext } from "./context";
import { appRouter } from "./router";

function makeCaller() {
  const ctx: TrpcContext = {
    req: new Request("http://localhost/api/trpc"),
    historyRepo: new InMemoryHistoryRepository(),
    problemRepo: new InMemoryProblemRepository(),
  };
  return appRouter.createCaller(ctx);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockConfig.speechResourceId = undefined;
  mockConfig.speechRegion = undefined;
});

describe("[L2] speech.issueToken", () => {
  it("returns available:false without touching MSI when Speech is unprovisioned", async () => {
    // 無いと: Speech 未設定環境で MSI を叩きに行って例外になり、
    // 「Web Speech へのフォールバック」というシグナル (available:false) が返らない退行が静かに通る
    const result = await makeCaller().speech.issueToken();

    expect(result).toEqual({ available: false, authToken: null, region: null });
    expect(getServiceToken).not.toHaveBeenCalled();
  });

  it("returns aad#resourceId#entraToken with region when configured and MSI yields a token", async () => {
    // 無いと: "aad#{resourceId}#{token}" の組み立てミス (区切り・順序・欠落) が
    // Speech SDK 側の分かりにくい認証エラーとしてしか現れない退行が静かに通る
    mockConfig.speechResourceId =
      "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/spch-dev-mindbox";
    mockConfig.speechRegion = "japaneast";
    vi.mocked(getServiceToken).mockResolvedValue("entra-token-xyz");

    const result = await makeCaller().speech.issueToken();

    expect(result).toEqual({
      available: true,
      authToken: `aad#${mockConfig.speechResourceId}#entra-token-xyz`,
      region: "japaneast",
    });
    // audience は Cognitive Services 共通リソース
    expect(getServiceToken).toHaveBeenCalledWith("https://cognitiveservices.azure.com");
  });

  it("returns available:false when MSI is absent (local dev bypass)", async () => {
    // 無いと: ローカルで "aad#...#null" のような壊れたトークンが配られ、
    // Web Speech フォールバックが働かず音声入力ごと壊れる退行が静かに通る
    mockConfig.speechResourceId = "/subscriptions/sub/.../spch-dev-mindbox";
    mockConfig.speechRegion = "japaneast";
    vi.mocked(getServiceToken).mockResolvedValue(null);

    const result = await makeCaller().speech.issueToken();

    expect(result).toEqual({ available: false, authToken: null, region: null });
  });

  it("propagates MSI failures instead of degrading silently", async () => {
    // 無いと: 本番で MI の権限剥がれ等によるトークン取得失敗が available:false に化けて
    // 静かに Web Speech 品質へ劣化し、原因調査の手がかりが消える (serviceToken と同方針)
    mockConfig.speechResourceId = "/subscriptions/sub/.../spch-dev-mindbox";
    mockConfig.speechRegion = "japaneast";
    vi.mocked(getServiceToken).mockRejectedValue(new Error("MSI 500"));

    await expect(makeCaller().speech.issueToken()).rejects.toThrow("MSI 500");
  });
});
