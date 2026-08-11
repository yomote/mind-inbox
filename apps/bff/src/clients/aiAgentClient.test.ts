/**
 * [L2] aiAgentClient — stub フォールバック境界の service-level test (#146)。
 *
 * 無いと何が静かに通るか: stub 応答の機械判別フラグ (`stubbed: true`) が stub 経路で
 * 落ちる / 実経路に誤って立つ退行。前者は「BFF が stub なのに本物のふりをした偽応答」
 * (#118 の実障害で発見が遅れた形) が復活し、後者は正常時に「AI サービス未接続」
 * バナーが出続ける。フラグの発生源はここ (aiAgentClient) なので、この境界で固定する。
 * router の tRPC 出力への伝搬は router.test.ts が守る。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockConfig = vi.hoisted(() => ({}) as { aiAgentBaseUrl?: string; aiAgentAudience?: string });

vi.mock("../config", () => ({ config: mockConfig }));

import { extract, sendChatMessage } from "./aiAgentClient";

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  delete mockConfig.aiAgentBaseUrl;
  delete mockConfig.aiAgentAudience;
});

describe("[L2] aiAgentClient stub 判別フラグ (#146)", () => {
  it("AI_AGENT_BASE_URL 未設定: /chat の stub 応答に stubbed: true が立つ", async () => {
    const res = await sendChatMessage({ sessionId: "s1", message: "こんにちは" });
    expect(res.stubbed).toBe(true);
    expect(res.reply).toContain("[stub]");
  });

  it("AI_AGENT_BASE_URL 設定済み: 実 /chat 応答に stubbed が立たない", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ reply: "本物の応答" }, { status: 200 })),
    );

    const res = await sendChatMessage({ sessionId: "s1", message: "こんにちは" });
    expect(res.reply).toBe("本物の応答");
    expect(res.stubbed).toBeUndefined();
  });

  it("AI_AGENT_BASE_URL 未設定: /extract の stub 応答に stubbed: true が立つ", async () => {
    const res = await extract({ sessionId: "s1", existingProblems: [], messages: [] });
    expect(res.stubbed).toBe(true);
    expect(res.items).toHaveLength(1);
  });

  it("AI_AGENT_BASE_URL 設定済み: 実 /extract 応答に stubbed が立たない", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { sessionId: "s1", items: [], newProblemCount: 0, updatedProblemCount: 0 },
          { status: 200 },
        ),
      ),
    );

    const res = await extract({ sessionId: "s1", existingProblems: [], messages: [] });
    expect(res.newProblemCount).toBe(0);
    expect(res.stubbed).toBeUndefined();
  });
});
