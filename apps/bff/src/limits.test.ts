/**
 * [単体] 外部入力の大きさの上限 (`limits.ts`) が **入口ごとに実際に効いている**ことを固定する。
 *
 * 無いと何が静かに通るか:
 *   上限を消しても / 新しい入口を上限なしで足しても、テストは緑のまま通る。そして
 *   巨大な入力がそのまま LLM (Azure OpenAI) と VOICEVOX へ転送され、**1 リクエストで
 *   青天井の課金と CPU 占有が起きる** — 気づけるのは数時間〜1 日後の予算アラートだけ
 *   (#313 C-1)。上限は「値が大きいか小さいか」ではなく「下流を呼ぶ前に弾いたか」が本質なので、
 *   どのケースでも **下流クライアントが呼ばれていないこと**まで見る。
 *
 * 性質で書く (strategy.md §3): 「境界を 1 文字でも超えたら必ず拒否」「境界以下なら必ず受理」
 * の 2 つを長さ全域で確かめる。例を 1 個だけ置くと、境界がずれても片側しか落ちない。
 *
 * ここで test しないこと:
 *   - 上限値そのものの妥当性 (根拠は limits.ts のコメント / PO 判断)
 *   - 下流サービスの挙動 (aiAgentClient / ttsService のテスト範疇)
 */

import fc from "fast-check";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./clients/aiAgentClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./clients/aiAgentClient")>()),
  sendChatMessage: vi.fn(),
  extract: vi.fn(),
  createPlan: vi.fn(),
  approve: vi.fn(),
}));
vi.mock("./chat/chatStream", () => ({ openChatStream: vi.fn() }));
vi.mock("./tts/ttsService", () => ({
  planTts: vi.fn(),
  prefetchTts: vi.fn(),
  synthesizeTts: vi.fn(),
}));
vi.mock("./warmup/warmupService", () => ({ warmupDownstreams: vi.fn() }));

import { extract as extractAiAgent, sendChatMessage } from "./clients/aiAgentClient";
import { openChatStream } from "./chat/chatStream";
import { synthesizeTts } from "./tts/ttsService";
import { handleChatStream, handleTts } from "./http/handlers";
import { InMemoryProblemRepository } from "./repositories/problemRepository";
import { appRouter } from "./trpc/router";
import type { TrpcContext } from "./trpc/context";
import {
  MAX_CONVERSATION_MESSAGES,
  MAX_CONVERSATION_TOTAL_CHARS,
  MAX_MESSAGE_LENGTH,
  MAX_TTS_TEXT_LENGTH,
} from "./limits";

function makeCaller() {
  const ctx: TrpcContext = {
    req: new Request("http://localhost/api/trpc"),
    problemRepo: new InMemoryProblemRepository(),
  };
  return appRouter.createCaller(ctx);
}

/** 長さだけが意味を持つのでダミー文字で埋める (日本語 1 文字 = 1 code point)。 */
const filler = (length: number) => "あ".repeat(length);

const silent = { log: () => {}, error: () => {} };

const postJson = (url: string, body: unknown) =>
  new Request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** 上限を「1 文字だけ超える」〜「大きく超える」まで。実際に燃えるのは超過側。 */
const overLimit = (max: number) => fc.integer({ min: max + 1, max: max + 1_000 });
const withinLimit = (max: number) => fc.integer({ min: 1, max });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(sendChatMessage).mockResolvedValue({
    reply: "ふむ",
    requiresApproval: false,
    approvalRequestId: null,
    citations: [],
  });
  vi.mocked(extractAiAgent).mockResolvedValue({
    sessionId: "s1",
    items: [],
    newProblemCount: 0,
    updatedProblemCount: 0,
  });
  vi.mocked(synthesizeTts).mockResolvedValue(null);
});

describe("[単体] 発話 1 通の長さ上限 — tRPC / SSE の両入口", () => {
  it("上限を超える message は、ai-agent を呼ぶ前に必ず拒否される", async () => {
    await fc.assert(
      fc.asyncProperty(overLimit(MAX_MESSAGE_LENGTH), async (length) => {
        vi.clearAllMocks();
        await expect(
          makeCaller().consultation.sendMessage({ sessionId: "s1", message: filler(length) }),
        ).rejects.toThrow();
        expect(sendChatMessage).not.toHaveBeenCalled();
      }),
      { numRuns: 20 },
    );
  });

  it("上限以下の message は今までどおり通る (正常な操作を切っていない)", async () => {
    await fc.assert(
      fc.asyncProperty(withinLimit(MAX_MESSAGE_LENGTH), async (length) => {
        vi.clearAllMocks();
        vi.mocked(sendChatMessage).mockResolvedValue({
          reply: "ふむ",
          requiresApproval: false,
          approvalRequestId: null,
          citations: [],
        });
        await expect(
          makeCaller().consultation.sendMessage({ sessionId: "s1", message: filler(length) }),
        ).resolves.toMatchObject({ reply: "ふむ" });
      }),
      { numRuns: 20 },
    );
  });

  it("SSE 入口 (POST /api/chat/stream) も同じ上限で 400 を返し、ストリームを開かない", async () => {
    // 無いと: tRPC 側だけ締めて非 tRPC 入口が青天井のまま残る (入口が 2 つある構造の罠)。
    await fc.assert(
      fc.asyncProperty(overLimit(MAX_MESSAGE_LENGTH), async (length) => {
        vi.clearAllMocks();
        const res = await handleChatStream(
          postJson("http://x/api/chat/stream", { sessionId: "s1", message: filler(length) }),
          silent,
        );
        expect(res.status).toBe(400);
        expect(openChatStream).not.toHaveBeenCalled();
      }),
      { numRuns: 10 },
    );
  });
});

describe("[単体] consultation.extract — 会話全文の上限は件数と合計文字数の両方で効く", () => {
  it("件数が上限を超えたら ai-agent を呼ばない", async () => {
    await fc.assert(
      fc.asyncProperty(overLimit(MAX_CONVERSATION_MESSAGES), async (count) => {
        vi.clearAllMocks();
        const messages = Array.from({ length: count }, () => ({
          role: "user" as const,
          text: "あ",
        }));
        await expect(
          makeCaller().consultation.extract({ sessionId: "s1", messages }),
        ).rejects.toThrow();
        expect(extractAiAgent).not.toHaveBeenCalled();
      }),
      { numRuns: 10 },
    );
  });

  it("件数も 1 通の長さも上限内でも、合計文字数が上限を超えたら拒否する", async () => {
    // 無いと: 「1 通 8,000 字 × 200 通」= 160 万字が全部プロンプトに載る経路が残り、
    // 個別の上限を入れたのに 1 リクエストの課金は実質青天井のまま (合計の締めが本命)。
    const perMessage = MAX_MESSAGE_LENGTH;
    const count = Math.floor(MAX_CONVERSATION_TOTAL_CHARS / perMessage) + 1;
    expect(count).toBeLessThanOrEqual(MAX_CONVERSATION_MESSAGES); // 件数上限では止まらない構成
    const messages = Array.from({ length: count }, () => ({
      role: "user" as const,
      text: filler(perMessage),
    }));

    await expect(
      makeCaller().consultation.extract({ sessionId: "s1", messages }),
    ).rejects.toThrow();
    expect(extractAiAgent).not.toHaveBeenCalled();
  });

  it("実セッション相当 (30 往復 / 各 500 字) は通る", async () => {
    const messages = Array.from({ length: 60 }, () => ({
      role: "user" as const,
      text: filler(500),
    }));

    await expect(
      makeCaller().consultation.extract({ sessionId: "s1", messages }),
    ).resolves.toMatchObject({ newProblemCount: 0 });
    expect(extractAiAgent).toHaveBeenCalledTimes(1);
  });
});

describe("[単体] TTS テキストの上限", () => {
  it("上限を超える text は VOICEVOX を呼ぶ前に 400 で弾く", async () => {
    // 無いと: 1 リクエストが VOICEVOX (CPU バウンド / 合成回数 = 文数) を無制限に占有できる。
    await fc.assert(
      fc.asyncProperty(overLimit(MAX_TTS_TEXT_LENGTH), async (length) => {
        vi.clearAllMocks();
        const res = await handleTts(postJson("http://x/api/tts", { text: filler(length) }), silent);
        expect(res.status).toBe(400);
        expect(synthesizeTts).not.toHaveBeenCalled();
      }),
      { numRuns: 10 },
    );
  });

  it("上限以下の text は今までどおり合成へ渡る", async () => {
    await fc.assert(
      fc.asyncProperty(withinLimit(MAX_TTS_TEXT_LENGTH), async (length) => {
        vi.clearAllMocks();
        vi.mocked(synthesizeTts).mockResolvedValue(null);
        const res = await handleTts(postJson("http://x/api/tts", { text: filler(length) }), silent);
        expect(res.status).toBe(204); // VOICEVOX 未構成の既定経路
        expect(synthesizeTts).toHaveBeenCalledTimes(1);
      }),
      { numRuns: 10 },
    );
  });
});
