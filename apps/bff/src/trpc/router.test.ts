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
 *   - repository の永続化保証。ここは常に InMemory* を注入する。本番の Cosmos 実装
 *     (ADR 0030) は cosmosRepositories.test.ts が、実環境での残存は
 *     cicd/scripts/smoke-test/persistence-probe.sh が見る
 */

import { TRPCError } from "@trpc/server";
import fc from "fast-check";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ExtractError は router が instanceof で判定するので、実物を残したまま関数だけ差し替える
// (丸ごと差し替えるとクラスが undefined になり、失敗の種別判定が黙って死ぬ)。
vi.mock("../clients/aiAgentClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../clients/aiAgentClient")>()),
  sendChatMessage: vi.fn(),
  extract: vi.fn(),
  createPlan: vi.fn(),
  approve: vi.fn(),
}));

import {
  ExtractError,
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  extract as extractAiAgent,
  sendChatMessage,
} from "../clients/aiAgentClient";
import {
  THEMES,
  type ExtractedItem,
  type ExtractionResult,
  type Mention,
  type Problem,
} from "./domain";
import { InMemoryProblemRepository } from "../repositories/problemRepository";
import type { TrpcContext } from "./context";
import { appRouter } from "./router";

// ---- helpers ---------------------------------------------------------------

function makeCaller() {
  return makeCallerWithRepos().caller;
}

/** triage / seed 系で repo を直接いじりたい test 用。 */
function makeCallerWithRepos() {
  const problemRepo = new InMemoryProblemRepository();
  const ctx: TrpcContext = {
    req: new Request("http://localhost/api/trpc"),
    problemRepo,
  };
  return { caller: appRouter.createCaller(ctx), problemRepo };
}

function makeProblem(overrides: Partial<Problem> = {}): Problem {
  const mentions = overrides.mentions ?? [makeMention()];
  return {
    id: "prob-1",
    title: "転職の迷い",
    summary: "転職すべきか迷っている",
    theme: "仕事・キャリア",
    tags: ["転職"],
    status: "open",
    mentions,
    mentionCount: mentions.length,
    plans: [],
    createdAt: "2026-01-01T00:00:00.000Z",
    lastMentionedAt: "2026-01-01T00:00:00.000Z",
    resolvedAt: null,
    shelvedAt: null,
    ...overrides,
  };
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

  it.each(["", "   "])(
    "starts with an empty conversation (no AI call, no greeting) when concern is empty or whitespace (%j)",
    async (concern) => {
      // 無いと: (1) UI 仕様 (home.mdx: テーマ入力なしで直接対話開始) に反して空 concern が
      // BAD_REQUEST になり、実環境でホームから相談を開始できない退行が静かに通る (2026-08-07 に実環境で発生)。
      // 空メッセージが AI Agent に流れて 422 を引き起こす退行もここで止める。
      // (2) 撤去したはずの AI 挨拶 (#241 / dialogue-session.mdx §3.1) が復活し、
      // 毎回同じ文言の表示・読み上げが戻ってくる退行が静かに通る
      const { session } = await makeCaller().consultation.start({ concern });
      expect(session.title).toBe("相談セッション");
      expect(session.messages).toHaveLength(0);
      expect(sendChatMessage).not.toHaveBeenCalled();
    },
  );

  it("marks the reply as stubbed when aiAgentClient fell back to stub", async () => {
    // 無いと: stub フォールバックの機械判別フラグ (#146) が start 経路で落ち、
    // 「frontend は real・BFF は stub」の組み合わせで偽応答が本物のふりをする退行が静かに通る
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: '[stub] received: "仕事が辛い"',
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
      stubbed: true,
    });

    const result = await makeCaller().consultation.start({ concern: "仕事が辛い" });
    expect(result.stubbed).toBe(true);
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

  it("marks the reply as stubbed on the stub path and leaves the flag absent on the real path", async () => {
    // 無いと: stub フォールバックの機械判別フラグ (#146) が sendMessage 経路で落ちる /
    // 逆に実応答へ誤って立ち、正常時に「AI サービス未接続」バナーが出続ける退行が静かに通る
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: '[stub] received: "助けて"',
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
      stubbed: true,
    });
    const stubReply = await makeCaller().consultation.sendMessage({
      sessionId: "s1",
      message: "助けて",
    });
    expect(stubReply.stubbed).toBe(true);

    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "本物の応答",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    });
    const realReply = await makeCaller().consultation.sendMessage({
      sessionId: "s1",
      message: "助けて",
    });
    expect(realReply.stubbed).toBeUndefined();
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

// ---- 通しフロー ------------------------------------------------------------

describe("[L2] flow: start → sendMessage → extract → problem.createPlan → triage", () => {
  it("completes the use-case golden path (UC-01 → UC-02 → UC-05 → UC-04)", async () => {
    // 無いと: BFF を跨ぐ session / Problem の受け渡しの退行が、個別 endpoint の
    // test では捕まらず静かに通る。UC に無い旧導線 (organize → history) は ADR 0034 で
    // 撤去したので、通しフローも use_cases.md の道に合わせてある。
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: "もう少し聞かせてください",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    });
    vi.mocked(extractAiAgent).mockResolvedValue(newExtraction("prob-1"));
    vi.mocked(createPlanAiAgent).mockResolvedValue({
      title: "48 時間プラン",
      steps: ["早く帰る", "信頼できる人に話す"],
    });

    const caller = makeCaller();

    // UC-01 吐き出す → 抽出して Problem が open で立つ
    const startRes = await caller.consultation.start({ concern: "仕事が辛い" });
    const sessionId = startRes.session.id;
    await caller.consultation.sendMessage({ sessionId, message: "もう少し詳しく" });
    const extraction = await caller.consultation.extract({
      sessionId,
      messages: [{ role: "user", text: "仕事が辛い" }],
    });
    expect(extraction.newProblemCount).toBe(1);

    // UC-02 一覧に載る
    expect((await caller.problem.list()).map((p) => p.id)).toEqual(["prob-1"]);

    // UC-05 次の一歩が Problem に紐づく (状態は変わらない = 派生物)
    const planned = await caller.problem.createPlan({ problemId: "prob-1" });
    expect(planned.plans).toHaveLength(1);
    expect(planned.plans[0]?.steps).toEqual(["早く帰る", "信頼できる人に話す"]);
    expect(planned.status).toBe("open");

    // UC-04 棚卸しすると既定の一覧から外れる
    await caller.problem.triage({ action: "resolve", problemId: "prob-1" });
    expect(await caller.problem.list({ status: "open" })).toEqual([]);
    expect((await caller.problem.list()).map((p) => p.status)).toEqual(["resolved"]);
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
    expect(extractAiAgent).toHaveBeenCalledWith({
      sessionId: "s1",
      existingProblems: [],
      messages: [],
    });

    // 永続化されて problem.get で引ける
    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.title).toBe("転職の迷い");
    expect(problem.mentions).toHaveLength(1);
    expect(problem.mentionCount).toBe(1);
    expect(problem.status).toBe("open");
  });

  it("marks the extraction as stubbed on the stub path and leaves the flag absent on the real path", async () => {
    // 無いと: stub フォールバックの機械判別フラグ (#146) が extract 経路で落ち、
    // 「[stub] 困りごと」が本物の抽出結果のふりをして蓄積画面に並ぶ退行が静かに通る
    const caller = makeCaller();
    vi.mocked(extractAiAgent).mockResolvedValueOnce({
      ...newExtraction("prob-stub-1"),
      stubbed: true,
    });
    const stubResult = await caller.consultation.extract({ sessionId: "s1" });
    expect(stubResult.stubbed).toBe(true);

    vi.mocked(extractAiAgent).mockResolvedValueOnce(newExtraction("prob-2"));
    const realResult = await caller.consultation.extract({ sessionId: "s2" });
    expect(realResult.stubbed).toBeUndefined();
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
      expect.objectContaining({
        id: "prob-1",
        title: "転職の迷い",
        status: "open",
        mentionCount: 1,
      }),
    ]);

    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.mentions).toHaveLength(2);
    expect(problem.mentionCount).toBe(2);
    expect(problem.lastMentionedAt).toBe("2026-02-01T00:00:00.000Z");
  });

  // domain_model.md §4.2 は resolved / shelved の両方から open へ戻す。同じコードパスだが、
  // 片方だけ書くと「もう気にしない」側だけ落ちる変更が静かに通る。
  it.each([
    { status: "resolved" as const, stamp: { resolvedAt: "2026-01-15T00:00:00.000Z" } },
    { status: "shelved" as const, stamp: { shelvedAt: "2026-01-15T00:00:00.000Z" } },
  ])(
    "reopens a $status problem when it is mentioned again (UC-03 再燃)",
    async ({ status, stamp }) => {
      // 無いと: 抽出結果レビューが「🔁2回目 / 再燃」と出すのに、一覧の既定 (open のみ) には
      //         現れないまま棚卸し済みで埋もれる。「繰り返しに気づかせる」という芯が黙って死ぬ。
      const { caller, problemRepo } = makeCallerWithRepos();
      await problemRepo.upsert(makeProblem({ status, ...stamp }));

      vi.mocked(extractAiAgent).mockResolvedValue({
        sessionId: "s2",
        items: [
          {
            mention: makeMention({
              id: "men-2",
              sessionId: "s2",
              createdAt: "2026-02-01T00:00:00.000Z",
            }),
            grouping: {
              kind: "existing",
              problemId: "prob-1",
              problemTitle: "転職の迷い",
              problemTheme: "仕事・キャリア",
              isRecurrence: true,
              mentionCount: 2,
              reignited: true,
              groupingConfidence: 0.9,
            },
          },
        ],
        newProblemCount: 0,
        updatedProblemCount: 1,
      });

      await caller.consultation.extract({ sessionId: "s2" });

      const problem = await caller.problem.get({ id: "prob-1" });
      expect(problem.status).toBe("open");
      // 棚卸しの日時も消す (再オープンなのに解決日時が残っていると履歴が嘘になる)
      expect(problem.resolvedAt).toBeNull();
      expect(problem.shelvedAt).toBeNull();
      expect(problem.mentionCount).toBe(2);
      // 既定の一覧 (open) に戻ってくることまで見る
      expect((await caller.problem.list({ status: "open" })).map((p) => p.id)).toContain("prob-1");
    },
  );

  it("keeps mentionCount consistent with mentions even if ai-agent miscounts", async () => {
    // 無いと: ai-agent の申告値をそのまま持ち、詳細の「言及 N 回」とタイムラインの件数がズレる
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem());

    vi.mocked(extractAiAgent).mockResolvedValue({
      sessionId: "s2",
      items: [
        {
          mention: makeMention({ id: "men-2", createdAt: "2026-02-01T00:00:00.000Z" }),
          grouping: {
            kind: "existing",
            problemId: "prob-1",
            problemTitle: "転職の迷い",
            problemTheme: "仕事・キャリア",
            isRecurrence: true,
            mentionCount: 99, // ai-agent の誤カウント
            reignited: false,
            groupingConfidence: 0.9,
          },
        },
      ],
      newProblemCount: 0,
      updatedProblemCount: 1,
    });

    await caller.consultation.extract({ sessionId: "s2" });

    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.mentionCount).toBe(problem.mentions.length);
  });

  it("creates a consistent new problem when an 'existing' grouping references an unknown id", async () => {
    // 無いと: 候補集合との齟齬（existing だが repo に無い）で mentionCount=2 のまま
    //         mentions.length=1 の不整合 Problem が作られる退行が静かに通る
    const caller = makeCaller();
    vi.mocked(extractAiAgent).mockResolvedValue({
      sessionId: "s1",
      items: [
        {
          mention: makeMention({ problemId: "prob-orphan" }),
          grouping: {
            kind: "existing",
            problemId: "prob-orphan", // repo に存在しない
            problemTitle: "転職の迷い",
            problemTheme: "仕事・キャリア",
            isRecurrence: true,
            mentionCount: 2, // 既存追記前提の値。フォールバックでは採用しない
            reignited: false,
            groupingConfidence: 0.9,
          },
        },
      ],
      newProblemCount: 0,
      updatedProblemCount: 1,
    });

    await caller.consultation.extract({ sessionId: "s1" });

    const problem = await caller.problem.get({ id: "prob-orphan" });
    expect(problem.mentions).toHaveLength(1);
    expect(problem.mentionCount).toBe(1); // mentions.length と一致
    expect(problem.status).toBe("open");
  });

  it("rejects empty sessionId with zod validation", async () => {
    await expect(makeCaller().consultation.extract({ sessionId: "" })).rejects.toBeInstanceOf(
      TRPCError,
    );
    expect(extractAiAgent).not.toHaveBeenCalled();
  });
});

// ---- consultation.preview (#283 / ADR 0039 D1) ------------------------------
// 仕様: ADR 0039 D1「preview は Cosmos には一切書かない」「preview が書かないことは
// L2 テストで固定する」。契約は mockApi.previewExtraction (ADR 0004) と同形。

/** ExtractionReplySchema (output 検証) を満たすコンパクトな arbitrary 群。 */
const arbAffect = fc.record({
  label: fc.constantFrom("不安", "焦り", "安堵"),
  valence: fc.constantFrom("negative", "neutral", "positive" as const),
  intensity: fc.double({ min: 0, max: 1, noNaN: true }),
});

const arbDraftMention = fc.record({
  id: fc.uuid(),
  sessionId: fc.constant("s1"),
  dumpId: fc.option(fc.uuid(), { nil: null }),
  createdAt: fc
    .date({ min: new Date("2020-01-01"), max: new Date("2030-01-01"), noInvalidDate: true })
    .map((d) => d.toISOString()),
  statement: fc.string({ maxLength: 40 }),
  excerpt: fc.string({ maxLength: 40 }),
  affect: arbAffect,
  proposedTheme: fc.constantFrom(...THEMES),
  proposedTags: fc.array(fc.string({ maxLength: 6 }), { maxLength: 3 }),
  problemId: fc.option(fc.uuid(), { nil: null }),
  groupingConfidence: fc.option(fc.double({ min: 0, max: 1, noNaN: true }), { nil: null }),
});

const arbGrouping = fc.record({
  kind: fc.constantFrom("new", "existing" as const),
  problemId: fc.uuid(),
  problemTitle: fc.string({ minLength: 1, maxLength: 26 }),
  problemTheme: fc.constantFrom(...THEMES),
  isRecurrence: fc.boolean(),
  mentionCount: fc.integer({ min: 1, max: 99 }),
  reignited: fc.boolean(),
  groupingConfidence: fc.option(fc.double({ min: 0, max: 1, noNaN: true }), { nil: null }),
});

/**
 * ExtractedItem[]。mention.id / grouping.problemId は index で一意化する
 * (shrink で同値に潰れると「同 id の new が上書き合戦する」別問題を踏んで flaky になる)。
 */
const arbExtractedItems: fc.Arbitrary<ExtractedItem[]> = fc
  .array(fc.record({ mention: arbDraftMention, grouping: arbGrouping }), { maxLength: 4 })
  .map((items) =>
    items.map((item, i) => ({
      mention: { ...item.mention, id: `men-${i}-${item.mention.id}` },
      grouping: { ...item.grouping, problemId: `prob-${i}-${item.grouping.problemId}` },
    })),
  );

function extractionOf(items: ExtractedItem[]): ExtractionResult {
  return {
    sessionId: "s1",
    items,
    newProblemCount: items.filter((i) => i.grouping.kind === "new").length,
    updatedProblemCount: items.filter((i) => i.grouping.kind === "existing").length,
  };
}

describe("[単体] consultation.preview (#283 / ADR 0039 D1)", () => {
  it("returns the extraction result and passes candidates + conversation to ai-agent", async () => {
    // 無いと: preview が既存 Problem 候補や会話全文を ai-agent に渡さない退行が静かに通り、
    //         下書きのグルーピング (🔁 既存に追加) が実環境でだけ壊れる (#183 と同型)
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem());
    vi.mocked(extractAiAgent).mockResolvedValue(newExtraction("prob-2"));

    const result = await caller.consultation.preview({
      sessionId: "s1",
      messages: [
        { role: "assistant", text: "どうしましたか" },
        { role: "user", text: "眠れない" },
      ],
    });

    expect(result.newProblemCount).toBe(1);
    expect(extractAiAgent).toHaveBeenCalledWith({
      sessionId: "s1",
      existingProblems: [
        expect.objectContaining({ id: "prob-1", title: "転職の迷い", mentionCount: 1 }),
      ],
      messages: [
        { role: "assistant", text: "どうしましたか" },
        { role: "user", text: "眠れない" },
      ],
    });
  });

  it("never writes to the problem repository, whatever ai-agent extracts", async () => {
    // 無いと: preview が Problem を書いてしまう退行が静かに通る。例外は出ず、
    //         「確定していない下書き」が蓄積データを汚す (ADR 0039 の核が黙って死ぬ)。
    //         ADR 0039「preview が書かないことは L2 テストで固定する」の固定先。
    await fc.assert(
      fc.asyncProperty(arbExtractedItems, async (items) => {
        const { caller, problemRepo } = makeCallerWithRepos();
        await problemRepo.upsert(makeProblem());
        const before = await problemRepo.list();
        vi.mocked(extractAiAgent).mockResolvedValue(extractionOf(items));

        await caller.consultation.preview({
          sessionId: "s1",
          messages: [{ role: "user", text: "眠れない" }],
        });

        expect(await problemRepo.list()).toEqual(before);
      }),
    );
  });

  it("marks the preview as stubbed on the stub path and leaves the flag absent on the real path", async () => {
    // 無いと: stub フォールバックの機械判別フラグ (#146 / ADR 0039 D6) が preview 経路で
    //         落ち、「[stub] 困りごと」の下書きが本物のふりをして右ペインに並ぶ退行が静かに通る
    const caller = makeCaller();
    vi.mocked(extractAiAgent).mockResolvedValueOnce({
      ...newExtraction("prob-stub-1"),
      stubbed: true,
    });
    const stubResult = await caller.consultation.preview({ sessionId: "s1", messages: [] });
    expect(stubResult.stubbed).toBe(true);

    vi.mocked(extractAiAgent).mockResolvedValueOnce(newExtraction("prob-2"));
    const realResult = await caller.consultation.preview({ sessionId: "s1", messages: [] });
    expect(realResult.stubbed).toBeUndefined();
  });

  it.each([
    ["session-missing", "NOT_FOUND"],
    ["llm-parse-failed", "BAD_GATEWAY"],
    ["upstream-failed", "INTERNAL_SERVER_ERROR"],
  ])("%s は %s として種別つきでクライアントへ返す (#183 と同じ翻訳)", async (kind, code) => {
    // 無いと: preview の失敗理由が潰れ、右ペインは「何が起きたか分からないエラー」しか
    //         出せない。extract と翻訳がズレると復帰導線の出し分けも壊れる。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(extractAiAgent).mockRejectedValue(new ExtractError(kind as never, "boom"));

    await expect(
      makeCaller().consultation.preview({ sessionId: "s1", messages: [] }),
    ).rejects.toMatchObject({ code, message: kind });
  });

  it("rejects empty sessionId with zod validation", async () => {
    await expect(
      makeCaller().consultation.preview({ sessionId: "", messages: [] }),
    ).rejects.toBeInstanceOf(TRPCError);
    expect(extractAiAgent).not.toHaveBeenCalled();
  });
});

// ---- consultation.extract — draft commit (#283 / ADR 0039 D1/D3) ------------

describe("[単体] consultation.extract — draft commit (#283 / ADR 0039 D1/D3)", () => {
  it("persists the given draft as-is without calling ai-agent (確定時に再抽出しない)", async () => {
    // 無いと: 確定時に再抽出する実装に戻る退行が静かに通る。抽出は非決定的なので、
    //         画面で確認した「この内容」と違う Problem が書かれうる (PR #240 Codex 指摘の再発)。
    const caller = makeCaller();
    const draft = newExtraction("prob-1");

    const result = await caller.consultation.extract({
      sessionId: "s1",
      draft: { items: draft.items },
    });

    expect(extractAiAgent).not.toHaveBeenCalled();
    expect(result.items).toEqual(draft.items);

    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.title).toBe("転職の迷い");
    expect(problem.mentions.map((m) => m.id)).toEqual(["men-1"]);
  });

  it("derives counts from draft items and persists every draft mention", async () => {
    // 無いと: newProblemCount / updatedProblemCount をクライアント申告のまま返す・
    //         draft の一部 item を取りこぼして書く、のどちらも例外なしに通る (静かに間違う)
    await fc.assert(
      fc.asyncProperty(arbExtractedItems, async (items) => {
        const { caller, problemRepo } = makeCallerWithRepos();

        const result = await caller.consultation.extract({ sessionId: "s1", draft: { items } });

        expect(extractAiAgent).not.toHaveBeenCalled();
        expect(result.newProblemCount).toBe(items.filter((i) => i.grouping.kind === "new").length);
        expect(result.updatedProblemCount).toBe(
          items.filter((i) => i.grouping.kind === "existing").length,
        );
        const storedMentionIds = (await problemRepo.list()).flatMap((p) =>
          p.mentions.map((m) => m.id),
        );
        for (const item of items) {
          expect(storedMentionIds).toContain(item.mention.id);
        }
      }),
    );
  });

  it("appends a draft 'existing' item to the stored problem (🔁 再出現の確定)", async () => {
    // 無いと: draft 経由の確定だけ既存への追記 (mentionCount / lastMentionedAt 更新・
    //         再燃 reopen) を通らず、再抽出経路と挙動が割れる退行が静かに通る
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(
      makeProblem({ status: "resolved", resolvedAt: "2026-01-15T00:00:00.000Z" }),
    );

    await caller.consultation.extract({
      sessionId: "s2",
      draft: {
        items: [
          {
            mention: makeMention({
              id: "men-2",
              sessionId: "s2",
              createdAt: "2026-02-01T00:00:00.000Z",
            }),
            grouping: {
              kind: "existing",
              problemId: "prob-1",
              problemTitle: "転職の迷い",
              problemTheme: "仕事・キャリア",
              isRecurrence: true,
              mentionCount: 2,
              reignited: true,
              groupingConfidence: 0.9,
            },
          },
        ],
      },
    });

    const problem = await caller.problem.get({ id: "prob-1" });
    expect(problem.mentions).toHaveLength(2);
    expect(problem.mentionCount).toBe(2);
    expect(problem.status).toBe("open"); // 再燃で open に戻る (UC-03)
    expect(problem.lastMentionedAt).toBe("2026-02-01T00:00:00.000Z");
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

// ---- problem.triage --------------------------------------------------------

describe("[L2] problem.triage — 状態遷移", () => {
  it.each([
    { action: "resolve", status: "resolved" },
    { action: "shelve", status: "shelved" },
  ] as const)("$action moves status to $status", async ({ action, status }) => {
    // 無いと: 状態遷移や resolvedAt/shelvedAt の記録漏れが静かに通る
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem({ id: "p1" }));

    const { problems } = await caller.problem.triage({ action, problemId: "p1" });
    expect(problems[0].status).toBe(status);
    if (action === "resolve") expect(problems[0].resolvedAt).not.toBeNull();
    if (action === "shelve") expect(problems[0].shelvedAt).not.toBeNull();
  });

  it("reopen clears resolvedAt/shelvedAt and sets open", async () => {
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(
      makeProblem({ id: "p1", status: "resolved", resolvedAt: "2026-03-01T00:00:00.000Z" }),
    );

    const { problems } = await caller.problem.triage({ action: "reopen", problemId: "p1" });
    expect(problems[0].status).toBe("open");
    expect(problems[0].resolvedAt).toBeNull();
    expect(problems[0].shelvedAt).toBeNull();
  });
});

describe("[L2] problem.triage — 編集", () => {
  it("editTheme / editTitle update the field", async () => {
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem({ id: "p1" }));

    const t = await caller.problem.triage({
      action: "editTheme",
      problemId: "p1",
      theme: "心と体",
    });
    expect(t.problems[0].theme).toBe("心と体");

    const t2 = await caller.problem.triage({
      action: "editTitle",
      problemId: "p1",
      title: "新しいタイトル",
    });
    expect(t2.problems[0].title).toBe("新しいタイトル");
  });
});

describe("[L2] problem.triage — dismiss", () => {
  it("removes the problem", async () => {
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem({ id: "p1" }));

    const { problems } = await caller.problem.triage({ action: "dismiss", problemId: "p1" });
    expect(problems).toEqual([]);
    await expect(caller.problem.get({ id: "p1" })).rejects.toMatchObject({ code: "NOT_FOUND" });
  });
});

describe("[L2] problem.triage — relink", () => {
  it("moves a mention to another problem and recomputes both counts", async () => {
    // 無いと: Mention 移動で from/to の mentionCount / problemId が更新されず不整合になる退行が静かに通る
    const { caller, problemRepo } = makeCallerWithRepos();
    const mA = makeMention({ id: "men-a", problemId: "p1" });
    const mB = makeMention({
      id: "men-b",
      problemId: "p1",
      createdAt: "2026-02-01T00:00:00.000Z",
    });
    await problemRepo.upsert(makeProblem({ id: "p1", mentions: [mA, mB] }));
    await problemRepo.upsert(
      makeProblem({
        id: "p2",
        title: "別の悩み",
        mentions: [makeMention({ id: "men-c", problemId: "p2" })],
      }),
    );

    await caller.problem.triage({
      action: "relink",
      mentionId: "men-b",
      fromProblemId: "p1",
      toProblemId: "p2",
    });

    const from = await caller.problem.get({ id: "p1" });
    const to = await caller.problem.get({ id: "p2" });
    expect(from.mentions.map((m) => m.id)).toEqual(["men-a"]);
    expect(from.mentionCount).toBe(1);
    expect(to.mentions.map((m) => m.id)).toEqual(["men-c", "men-b"]);
    expect(to.mentionCount).toBe(2);
    expect(to.mentions.find((m) => m.id === "men-b")?.problemId).toBe("p2");
  });

  it("rejects relinking within the same problem", async () => {
    // 無いと: from===to で in-memory の同一参照が stale 上書きされ Mention が重複する退行が静かに通る
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(
      makeProblem({ id: "p1", mentions: [makeMention({ id: "men-a", problemId: "p1" })] }),
    );
    await expect(
      caller.problem.triage({
        action: "relink",
        mentionId: "men-a",
        fromProblemId: "p1",
        toProblemId: "p1",
      }),
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("removes the source problem when its last mention is relinked away", async () => {
    // 無いと: 種の Mention が抜けて mentions=[] の Problem が残り ProblemSchema.min(1) を破る退行が静かに通る
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(
      makeProblem({ id: "p1", mentions: [makeMention({ id: "men-only", problemId: "p1" })] }),
    );
    await problemRepo.upsert(
      makeProblem({ id: "p2", mentions: [makeMention({ id: "men-c", problemId: "p2" })] }),
    );

    await caller.problem.triage({
      action: "relink",
      mentionId: "men-only",
      fromProblemId: "p1",
      toProblemId: "p2",
    });

    await expect(caller.problem.get({ id: "p1" })).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect((await caller.problem.get({ id: "p2" })).mentionCount).toBe(2);
  });
});

describe("[L2] problem.triage — merge", () => {
  it("merges source mentions/tags/plans into target and removes source", async () => {
    // 無いと: 統合で mentions/tags/plans の取りこぼしや source 残留、count 不整合が静かに通る
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(
      makeProblem({
        id: "p1",
        tags: ["転職"],
        mentions: [makeMention({ id: "men-a", problemId: "p1" })],
      }),
    );
    await problemRepo.upsert(
      makeProblem({
        id: "p2",
        title: "似た悩み",
        tags: ["キャリア"],
        plans: [{ title: "既存プラン", steps: ["x"] }],
        mentions: [
          makeMention({ id: "men-c", problemId: "p2", createdAt: "2026-02-01T00:00:00.000Z" }),
        ],
      }),
    );

    const { problems } = await caller.problem.triage({
      action: "merge",
      sourceProblemId: "p1",
      targetProblemId: "p2",
    });

    expect(problems).toHaveLength(1);
    const target = problems[0];
    expect(target.id).toBe("p2");
    expect(target.mentions.map((m) => m.id).sort()).toEqual(["men-a", "men-c"]);
    expect(target.mentionCount).toBe(2);
    expect(target.tags).toEqual(["キャリア", "転職"]); // dedupe 後（target→source 順）
    await expect(caller.problem.get({ id: "p1" })).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("rejects merging a problem into itself", async () => {
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem({ id: "p1" }));
    await expect(
      caller.problem.triage({ action: "merge", sourceProblemId: "p1", targetProblemId: "p1" }),
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });
});

describe("[L2] problem.triage — エラー", () => {
  it("throws NOT_FOUND for an unknown problem", async () => {
    await expect(
      makeCaller().problem.triage({ action: "resolve", problemId: "nope" }),
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("throws NOT_FOUND for an unknown mention in relink", async () => {
    const { caller, problemRepo } = makeCallerWithRepos();
    await problemRepo.upsert(makeProblem({ id: "p1" }));
    await problemRepo.upsert(makeProblem({ id: "p2" }));
    await expect(
      caller.problem.triage({
        action: "relink",
        mentionId: "ghost",
        fromProblemId: "p1",
        toProblemId: "p2",
      }),
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });
});

describe("[L2] consultation.extract — 失敗の伝え方 (#183)", () => {
  it("会話をそのまま ai-agent へ渡す", async () => {
    // 無いと: 会話を送らない実装に戻り、ai-agent のプロセスメモリ頼みになる。
    //         メモリが生きている間は動くので、ローカルでも CI でも緑のまま通り、
    //         scale-to-zero した実環境でだけ 404 になる (#183 の元症状)。
    vi.mocked(extractAiAgent).mockResolvedValue(newExtraction("prob-1"));
    const caller = makeCaller();

    await caller.consultation.extract({
      sessionId: "s1",
      messages: [
        { role: "assistant", text: "どうしましたか" },
        { role: "user", text: "眠れない" },
      ],
    });

    expect(extractAiAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [
          { role: "assistant", text: "どうしましたか" },
          { role: "user", text: "眠れない" },
        ],
      }),
    );
  });

  it.each([
    ["session-missing", "NOT_FOUND"],
    ["llm-parse-failed", "BAD_GATEWAY"],
    ["upstream-failed", "INTERNAL_SERVER_ERROR"],
  ])("%s は %s として種別つきでクライアントへ返す", async (kind, code) => {
    // 無いと: 失敗の理由が message に潰れてクライアントへ届かず、画面は
    //         「何が起きたか分からないエラー」しか出せない。原因の切り分けが
    //         ユーザーにも開発者にもできない状態に戻る (#183)。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(extractAiAgent).mockRejectedValue(new ExtractError(kind as never, "boom"));
    const caller = makeCaller();

    await expect(caller.consultation.extract({ sessionId: "s1" })).rejects.toMatchObject({
      code,
      message: kind,
    });
  });

  it("種別のない例外はそのまま上げる (握り潰さない)", async () => {
    // 無いと: 想定外の失敗まで extract 専用の分類に丸められ、本当の原因が消える
    vi.mocked(extractAiAgent).mockRejectedValue(new Error("想定外"));
    const caller = makeCaller();

    await expect(caller.consultation.extract({ sessionId: "s1" })).rejects.toThrow("想定外");
  });
});
