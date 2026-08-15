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
  MAX_DRAFT_TOTAL_CHARS,
  MAX_EXTRACTED_ITEMS,
  MAX_EXTRACTED_TEXT_LENGTH,
  MAX_MESSAGE_LENGTH,
  MAX_TTS_TEXT_LENGTH,
} from "./limits";

function makeCallerWithRepo() {
  const problemRepo = new InMemoryProblemRepository();
  const ctx: TrpcContext = {
    req: new Request("http://localhost/api/trpc"),
    problemRepo,
  };
  return { caller: appRouter.createCaller(ctx), problemRepo };
}

function makeCaller() {
  return makeCallerWithRepo().caller;
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
    choices: [],
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
          choices: [],
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

// ---- draft commit 経路 (#283 / ADR 0039 D1/D3) の上限 ------------------------
//
// この経路は **ai-agent を呼ばない** ので、抽出側 (LLM) の上限では一切守れない。
// クライアントが送った items がそのまま Cosmos への書き込みになる = ここが唯一の門。

/** 実際に画面から確定される形の draft item (1 件)。 */
function draftItem(index: number) {
  return {
    mention: {
      id: `men-${index}`,
      sessionId: "s1",
      dumpId: "s1",
      createdAt: "2026-01-01T00:00:00.000Z",
      statement: "転職すべきか迷っている",
      excerpt: "転職しようか迷ってて",
      affect: { label: "不安", valence: "negative" as const, intensity: 0.6 },
      proposedTheme: "仕事・キャリア" as const,
      proposedTags: ["転職"],
      problemId: `prob-${index}`,
      groupingConfidence: null,
    },
    grouping: {
      kind: "new" as const,
      problemId: `prob-${index}`,
      problemTitle: "転職の迷い",
      problemTheme: "仕事・キャリア" as const,
      isRecurrence: false,
      mentionCount: 1,
      reignited: false,
      groupingConfidence: null,
    },
  };
}

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

/** `obj` の中の文字列 leaf / 配列すべての位置。**新しく増えたフィールドも自動で拾う**。 */
function paths(value: Json, kind: "string" | "array", prefix: string[] = []): string[][] {
  if (Array.isArray(value)) {
    return [
      ...(kind === "array" ? [prefix] : []),
      ...value.flatMap((v, i) => paths(v, kind, [...prefix, String(i)])),
    ];
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).flatMap(([k, v]) => paths(v, kind, [...prefix, k]));
  }
  return kind === "string" && typeof value === "string" ? [prefix] : [];
}

function mutate(root: Json, path: string[], next: (current: Json) => Json): Json {
  const clone = structuredClone(root) as Record<string, Json>;
  let cursor: Record<string, Json> = clone;
  for (const key of path.slice(0, -1)) cursor = cursor[key] as Record<string, Json>;
  const last = path[path.length - 1];
  cursor[last] = next(cursor[last]);
  return clone;
}

describe("[単体] consultation.extract の draft — 件数だけでなく中身も締まっている", () => {
  it("どの文字列フィールドを巨大にしても、Problem を 1 件も書かずに拒否する", async () => {
    // 無いと: 件数 200 の上限は残ったまま、1 item に巨大な statement / excerpt /
    // problemTitle / 感情ラベルを詰めてリクエストサイズと書き込み量の上限を迂回できる
    // (PR #324 Codex 指摘 P2)。**item を走査して全 string leaf を試す**ので、
    // 上限のない新しいフィールドが domain に増えた場合もここで落ちる。
    const item = draftItem(1) as unknown as Json;
    const stringPaths = paths(item, "string");
    expect(stringPaths.length).toBeGreaterThan(5); // 走査が空振りしていないこと

    for (const path of stringPaths) {
      const { caller, problemRepo } = makeCallerWithRepo();
      const huge = mutate(item, path, () => filler(50_000));

      await expect(
        caller.consultation.extract({
          sessionId: "s1",
          draft: { items: [huge] } as never,
        }),
      ).rejects.toThrow();
      expect(await problemRepo.list()).toHaveLength(0);
    }
  });

  it("どの配列フィールドを巨大にしても、Problem を 1 件も書かずに拒否する", async () => {
    // 無いと: タグを 1 item に何万個も詰める経路が残る (文字列長を締めても件数で抜ける)。
    const item = draftItem(1) as unknown as Json;
    const arrayPaths = paths(item, "array");
    expect(arrayPaths.length).toBeGreaterThan(0);

    for (const path of arrayPaths) {
      const { caller, problemRepo } = makeCallerWithRepo();
      const huge = mutate(item, path, (current) =>
        Array.from({ length: 10_000 }, () => (current as Json[])[0] ?? "x"),
      );

      await expect(
        caller.consultation.extract({
          sessionId: "s1",
          draft: { items: [huge] } as never,
        }),
      ).rejects.toThrow();
      expect(await problemRepo.list()).toHaveLength(0);
    }
  });

  it("件数も 1 フィールドの長さも上限内でも、draft 全体の合計が上限を超えたら拒否する", async () => {
    // 無いと: 「1 件 2,000 字 × 200 件」の積 (40 万字) がそのまま 1 回の書き込みとして
    // 通り、フィールド単位の上限を入れたのに 1 リクエストの書き込み量は実質青天井のまま。
    // 会話全文で採ったのと同じ考え方 (合計を別に締める)。
    const perItem = MAX_EXTRACTED_TEXT_LENGTH;
    const count = Math.floor(MAX_DRAFT_TOTAL_CHARS / perItem) + 1;
    expect(count).toBeLessThanOrEqual(MAX_EXTRACTED_ITEMS); // 件数上限では止まらない構成

    const items = Array.from({ length: count }, (_, i) => ({
      ...draftItem(i),
      mention: { ...draftItem(i).mention, statement: filler(perItem) },
    }));
    const { caller, problemRepo } = makeCallerWithRepo();

    await expect(
      caller.consultation.extract({ sessionId: "s1", draft: { items } }),
    ).rejects.toThrow();
    expect(await problemRepo.list()).toHaveLength(0);
  });

  it("実際に画面から確定される規模の draft (10 件) は今までどおり書ける", async () => {
    // 無いと: 上限を厳しくしすぎて「この内容で確定」が壊れても気づけない
    // (上限の役目は正常な操作を切らずに青天井を潰すこと)。
    const items = Array.from({ length: 10 }, (_, i) => draftItem(i));
    const { caller, problemRepo } = makeCallerWithRepo();

    const result = await caller.consultation.extract({ sessionId: "s1", draft: { items } });

    expect(result.items).toHaveLength(10);
    expect(await problemRepo.list()).toHaveLength(10);
    expect(extractAiAgent).not.toHaveBeenCalled(); // draft 経路は再抽出しない
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
