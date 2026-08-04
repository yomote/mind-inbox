/**
 * [L2] tRPC router の service-level test。
 *
 * パターン: appRouter.createCaller(ctx) で HTTP レイヤをバイパスし、
 * 外部依存 (aiAgentClient) は vi.mock で stub する。
 *
 * ここで test しないこと:
 *   - aiAgentClient 内部の HTTP 通信 / fetch の挙動 (それは aiAgentClient の test 範疇)
 *   - LLM 出力の妥当性 (それは ai-agent 側 L1)
 *   - UI 描画 / Azure Functions の HTTP wrapping (それは L3 / L4)
 *   - historyRepository の永続化保証 (本番は Cosmos DB に置き換わる)
 */

import type { HttpRequest } from "@azure/functions";
import { TRPCError } from "@trpc/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../clients/aiAgentClient", () => ({
  sendChatMessage: vi.fn(),
  organize: vi.fn(),
  extract: vi.fn(),
  createPlan: vi.fn(),
  approve: vi.fn(),
}));

import {
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  extract as extractAiAgent,
  organize as organizeAiAgent,
  sendChatMessage,
} from "../clients/aiAgentClient";
import type { ExtractionResult, Mention } from "./domain";
import { InMemoryHistoryRepository } from "../repositories/historyRepository";
import { InMemoryProblemRepository } from "../repositories/problemRepository";
import type { TrpcContext } from "./context";
import { appRouter } from "./router";

// ---- helpers ---------------------------------------------------------------

function makeCaller() {
  // 各 repo は test 毎に fresh InMemory を渡して isolation を担保する。
  const ctx: TrpcContext = {
    req: {} as HttpRequest,
    historyRepo: new InMemoryHistoryRepository(),
    problemRepo: new InMemoryProblemRepository(),
  };
  return appRouter.createCaller(ctx);
}

// ---- extract fixtures ------------------------------------------------------

function makeMention(overrides: Partial<Mention> = {}): Mention {
  return {
    id: "men-1",
    sessionId: "s1",
    dumpId: "s1",
    createdAt: "2026-01-01T00:00:00.000Z",
    statement: "転職すべきか迷っている",
    excerpt: "転職しようか迷ってて",
    affect: { label: "不安", valence: "negative", intensity: 0.6 },
    proposedTheme: "仕事・キャリア",
    proposedTags: ["転職"],
    problemId: "prob-1",
    groupingConfidence: null,
    ...overrides,
  };
}

function newExtraction(problemId = "prob-1"): ExtractionResult {
  return {
    sessionId: "s1",
    items: [
      {
        mention: makeMention({ problemId }),
        grouping: {
          kind: "new",
          problemId,
          problemTitle: "転職の迷い",
          problemTheme: "仕事・キャリア",
          isRecurrence: false,
          mentionCount: 1,
          reignited: false,
          groupingConfidence: null,
        },
      },
    ],
    newProblemCount: 1,
    updatedProblemCount: 0,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---- consultation.start ----------------------------------------------------

describe("[L2] consultation.start", () => {
  it("returns session with initial assistant reply when concern is provided", async () => {
    // 無いと: BFF が AI Agent の reply を session.messages に組み込まない退行が静かに通る
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: "どんなところが辛いですか？",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    });

    const result = await makeCaller().consultation.start({
      concern: "仕事が辛い",
    });

    expect(result.session.title).toBe("仕事が辛い");
    expect(result.session.messages).toHaveLength(2);
    expect(result.session.messages[0]).toMatchObject({
      role: "user",
      text: "仕事が辛い",
    });
    expect(result.session.messages[1]).toMatchObject({
      role: "assistant",
      text: "どんなところが辛いですか？",
    });
    expect(sendChatMessage).toHaveBeenCalledWith({
      sessionId: result.session.id,
      message: "仕事が辛い",
    });
  });

  it("rejects empty concern with zod validation", async () => {
    // 無いと: empty concern が AI Agent に流れて 500 を引き起こす退行が静かに通る
    await expect(makeCaller().consultation.start({ concern: "" })).rejects.toBeInstanceOf(
      TRPCError,
    );
    expect(sendChatMessage).not.toHaveBeenCalled();
  });
});

// ---- consultation.sendMessage ---------------------------------------------

describe("[L2] consultation.sendMessage", () => {
  it("passes through reply / requiresApproval / approvalRequestId / citations", async () => {
    // 無いと: response field の rename / 欠落が BFF→Frontend で deserialize 失敗を引き起こす退行が静かに通る
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: "考えを整理しましょう",
      requiresApproval: true,
      approvalRequestId: "appr-1",
      citations: ["doc-a", "doc-b"],
    });

    const result = await makeCaller().consultation.sendMessage({
      sessionId: "s1",
      message: "助けて",
    });

    expect(result).toEqual({
      reply: "考えを整理しましょう",
      requiresApproval: true,
      approvalRequestId: "appr-1",
      citations: ["doc-a", "doc-b"],
    });
  });

  it("propagates aiAgentClient errors as TRPCError", async () => {
    // 無いと: 依存先障害時に generic error として握りつぶす退行が静かに通る
    vi.mocked(sendChatMessage).mockRejectedValue(new Error("upstream 503"));

    await expect(
      makeCaller().consultation.sendMessage({
        sessionId: "s1",
        message: "テスト",
      }),
    ).rejects.toThrow("upstream 503");
  });
});

// ---- consultation.organize ------------------------------------------------

describe("[L2] consultation.organize", () => {
  it("returns OrganizeResponse from aiAgentClient pass-through", async () => {
    // 無いと: organize 結果の構造変更が BFF→Frontend で deserialize 失敗を引き起こす退行が静かに通る
    vi.mocked(organizeAiAgent).mockResolvedValue({
      summary: "仕事のストレス",
      emotions: ["疲労"],
      priorities: ["休息"],
    });

    const result = await makeCaller().consultation.organize({
      sessionId: "s1",
    });

    expect(result).toEqual({
      summary: "仕事のストレス",
      emotions: ["疲労"],
      priorities: ["休息"],
    });
    expect(organizeAiAgent).toHaveBeenCalledWith({ sessionId: "s1" });
  });

  it("rejects empty sessionId with zod validation", async () => {
    await expect(makeCaller().consultation.organize({ sessionId: "" })).rejects.toBeInstanceOf(
      TRPCError,
    );
    expect(organizeAiAgent).not.toHaveBeenCalled();
  });
});

// ---- consultation.createPlan ----------------------------------------------

describe("[L2] consultation.createPlan", () => {
  it("plucks summary/emotions/priorities from input.result and forwards to aiAgentClient", async () => {
    // 無いと: input mapping のミス (例: result.summary を渡し忘れ) が空プランを生む退行が静かに通る
    vi.mocked(createPlanAiAgent).mockResolvedValue({
      title: "48 時間プラン",
      steps: ["休む", "話す"],
    });

    const result = await makeCaller().consultation.createPlan({
      result: {
        summary: "仕事のストレス",
        emotions: ["疲労"],
        priorities: ["休息"],
      },
    });

    expect(result).toEqual({ title: "48 時間プラン", steps: ["休む", "話す"] });
    expect(createPlanAiAgent).toHaveBeenCalledWith({
      summary: "仕事のストレス",
      emotions: ["疲労"],
      priorities: ["休息"],
    });
  });

  it("rejects malformed organized result with zod validation", async () => {
    // 無いと: schema 不整合な input が AI Agent に流れて 500 を引き起こす退行が静かに通る
    await expect(
      // @ts-expect-error: missing required fields, intentional
      makeCaller().consultation.createPlan({ result: { summary: "x" } }),
    ).rejects.toBeInstanceOf(TRPCError);
    expect(createPlanAiAgent).not.toHaveBeenCalled();
  });
});

// ---- consultation.approve --------------------------------------------------

describe("[L2] consultation.approve", () => {
  it.each([
    { approved: true, reply: "承認しました" },
    { approved: false, reply: "キャンセルしました" },
  ])("passes approved=$approved through to aiAgentClient", async ({ approved, reply }) => {
    // 無いと: approved boolean の意味反転 (true/false 取り違え) が静かに通る
    vi.mocked(approveAiAgent).mockResolvedValue({ reply });

    const result = await makeCaller().consultation.approve({
      approvalRequestId: "appr-1",
      approved,
    });

    expect(result).toEqual({ reply });
    expect(approveAiAgent).toHaveBeenCalledWith({
      approvalRequestId: "appr-1",
      approved,
    });
  });
});

// ---- history --------------------------------------------------------------

describe("[L2] history", () => {
  it("save then list returns the saved item with generated id and createdAt", async () => {
    // 無いと: history.save の zod schema 検証や id/createdAt 自動付与の退行が静かに通る
    // makeCaller() ごとに fresh historyRepo が作られるため、save+list は同一 caller で実行する
    const caller = makeCaller();

    const saved = await caller.history.save({
      sessionId: "s1",
      title: "仕事のストレス",
      result: {
        summary: "summary",
        emotions: ["疲労"],
        priorities: ["休息"],
      },
      plan: { title: "プラン", steps: ["step1"] },
    });

    expect(saved.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(saved.title).toBe("仕事のストレス");
    expect(saved.createdAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    const list = await caller.history.list();
    expect(list).toEqual([saved]);
  });
});

// ---- 通しフロー ------------------------------------------------------------

describe("[L2] flow: start → sendMessage → organize → createPlan → save", () => {
  it("completes the full consultation workflow with all aiAgentClient mocks wired", async () => {
    // 無いと: BFF を跨ぐ session の受け渡し or schema 連携の退行が
    // 個別 endpoint の test では捕まらず静かに通る
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: "整理を始めましょう",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    });
    vi.mocked(organizeAiAgent).mockResolvedValue({
      summary: "仕事のストレス",
      emotions: ["疲労"],
      priorities: ["休息"],
    });
    vi.mocked(createPlanAiAgent).mockResolvedValue({
      title: "48 時間プラン",
      steps: ["早く帰る", "信頼できる人に話す"],
    });

    const caller = makeCaller();

    const startRes = await caller.consultation.start({ concern: "仕事が辛い" });
    const sessionId = startRes.session.id;

    await caller.consultation.sendMessage({
      sessionId,
      message: "もう少し詳しく",
    });

    const organized = await caller.consultation.organize({ sessionId });
    const plan = await caller.consultation.createPlan({ result: organized });

    const saved = await caller.history.save({
      sessionId,
      title: startRes.session.title,
      result: organized,
      plan,
    });

    const list = await caller.history.list();
    expect(list).toHaveLength(1);
    expect(list[0]).toEqual(saved);
    expect(list[0].title).toBe("仕事が辛い");
    expect(list[0].plan.steps).toEqual(["早く帰る", "信頼できる人に話す"]);
  });
});

// ---- consultation.extract --------------------------------------------------

describe("[L2] consultation.extract", () => {
  it("persists a new problem and returns the extraction result", async () => {
    // 無いと: 抽出結果を Problem リポジトリに反映し損ねる / 戻り値の構造変更が静かに通る
    vi.mocked(extractAiAgent).mockResolvedValue(newExtraction("prob-1"));
    const caller = makeCaller();

    const result = await caller.consultation.extract({ sessionId: "s1" });
    expect(result.newProblemCount).toBe(1);
    expect(result.items[0].grouping.kind).toBe("new");
    // 初回は既存候補が空で ai-agent を呼ぶ
    expect(extractAiAgent).toHaveBeenCalledWith({ sessionId: "s1", existingProblems: [] });

    // 永続化されて problem.get で引ける
    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.title).toBe("転職の迷い");
    expect(problem.mentions).toHaveLength(1);
    expect(problem.mentionCount).toBe(1);
    expect(problem.status).toBe("open");
  });

  it("appends to an existing problem and passes candidates to ai-agent", async () => {
    // 無いと: 再出現が既存に束ねられず mentionCount / mentions / lastMentionedAt が更新されない退行が静かに通る
    const caller = makeCaller();
    vi.mocked(extractAiAgent).mockResolvedValueOnce(newExtraction("prob-1"));
    await caller.consultation.extract({ sessionId: "s1" });

    vi.mocked(extractAiAgent).mockResolvedValueOnce({
      sessionId: "s2",
      items: [
        {
          mention: makeMention({
            id: "men-2",
            sessionId: "s2",
            dumpId: "s2",
            problemId: "prob-1",
            statement: "やっぱり転職したい",
            createdAt: "2026-02-01T00:00:00.000Z",
          }),
          grouping: {
            kind: "existing",
            problemId: "prob-1",
            problemTitle: "転職の迷い",
            problemTheme: "仕事・キャリア",
            isRecurrence: true,
            mentionCount: 2,
            reignited: false,
            groupingConfidence: 0.9,
          },
        },
      ],
      newProblemCount: 0,
      updatedProblemCount: 1,
    });
    await caller.consultation.extract({ sessionId: "s2" });

    // 2回目は既存候補 prob-1 を ai-agent に渡している（ADR 0012）
    expect(vi.mocked(extractAiAgent).mock.calls[1]?.[0].existingProblems).toEqual([
      expect.objectContaining({ id: "prob-1", title: "転職の迷い", status: "open", mentionCount: 1 }),
    ]);

    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.mentions).toHaveLength(2);
    expect(problem.mentionCount).toBe(2);
    expect(problem.lastMentionedAt).toBe("2026-02-01T00:00:00.000Z");
  });

  it("rejects empty sessionId with zod validation", async () => {
    await expect(makeCaller().consultation.extract({ sessionId: "" })).rejects.toBeInstanceOf(
      TRPCError,
    );
    expect(extractAiAgent).not.toHaveBeenCalled();
  });
});

// ---- problem.list / get ----------------------------------------------------

describe("[L2] problem.list / get", () => {
  it("get throws NOT_FOUND for unknown id", async () => {
    // 無いと: 存在しない Problem に対して undefined を返し、詳細画面が壊れる退行が静かに通る
    await expect(makeCaller().problem.get({ id: "nope" })).rejects.toMatchObject({
      code: "NOT_FOUND",
    });
  });

  it("list filters by status", async () => {
    // 無いと: status フィルタが効かず解決済みが一覧に混ざる退行が静かに通る
    const caller = makeCaller();
    vi.mocked(extractAiAgent).mockResolvedValue(newExtraction("prob-1"));
    await caller.consultation.extract({ sessionId: "s1" });

    expect(await caller.problem.list({ status: "open" })).toHaveLength(1);
    expect(await caller.problem.list({ status: "resolved" })).toHaveLength(0);
  });
});

// ---- problem.createPlan ----------------------------------------------------

describe("[L2] problem.createPlan", () => {
  it("appends a plan derived from the problem without changing status", async () => {
    // 無いと: プランが Problem に紐づかない / 派生物のはずが status を変えてしまう退行が静かに通る
    const caller = makeCaller();
    vi.mocked(extractAiAgent).mockResolvedValue(newExtraction("prob-1"));
    await caller.consultation.extract({ sessionId: "s1" });

    vi.mocked(createPlanAiAgent).mockResolvedValue({ title: "48時間プラン", steps: ["休む"] });
    const updated = await caller.problem.createPlan({ problemId: "prob-1" });

    expect(updated.plans).toEqual([{ title: "48時間プラン", steps: ["休む"] }]);
    expect(updated.status).toBe("open");
    expect(createPlanAiAgent).toHaveBeenCalledWith({
      summary: "転職すべきか迷っている",
      emotions: ["不安"],
      priorities: ["転職"],
    });
  });

  it("throws NOT_FOUND for unknown problemId", async () => {
    await expect(makeCaller().problem.createPlan({ problemId: "nope" })).rejects.toMatchObject({
      code: "NOT_FOUND",
    });
  });
});
