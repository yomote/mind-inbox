/**
 * [L2] aiAgentClient — stub フォールバック境界の service-level test (#146)。
 *
 * 無いと何が静かに通るか: stub 応答の機械判別フラグ (`stubbed: true`) が stub 経路で
 * 落ちる / 実経路に誤って立つ退行。前者は「BFF が stub なのに本物のふりをした偽応答」
 * (#118 の実障害で発見が遅れた形) が復活し、後者は正常時に「AI サービス未接続」
 * バナーが出続ける。フラグの発生源はここ (aiAgentClient) なので、この境界で固定する。
 * router の tRPC 出力への伝搬は router.test.ts が守る。
 */

import fc from "fast-check";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockConfig = vi.hoisted(() => ({}) as { aiAgentBaseUrl?: string; aiAgentAudience?: string });

vi.mock("../config", () => ({ config: mockConfig }));

import {
  ApprovalAlreadyProcessedError,
  ApprovalNotFoundError,
  ExtractError,
  approve,
  extract,
  sendChatMessage,
} from "./aiAgentClient";
import type { ExtractionResult } from "../trpc/domain";

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

// ---- /extract 応答の検証 (#313 B-3) ----------------------------------------

/** 相談本文に見立てた文字列。ログ / エラーに漏れていないかの目印にも使う。 */
const SECRET = "会社を辞めたいと毎晩考えている";

function validExtraction(): ExtractionResult {
  return {
    sessionId: "s1",
    items: [
      {
        mention: {
          id: "men-1",
          sessionId: "s1",
          dumpId: "s1",
          createdAt: "2026-01-01T00:00:00.000Z",
          statement: SECRET,
          excerpt: SECRET,
          affect: { label: "不安", valence: "negative", intensity: 0.6 },
          proposedTheme: "仕事・キャリア",
          proposedTags: ["転職"],
          problemId: "prob-1",
          groupingConfidence: null,
        },
        grouping: {
          kind: "new",
          problemId: "prob-1",
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

/**
 * 契約の壊し方 (LLM の出力揺れ / プロンプトインジェクションで実際に起こりうる形)。
 * `apply` は valid な応答を 1 箇所だけ壊す。
 */
const corruptions: { name: string; apply: (doc: Record<string, unknown>) => void }[] = [
  {
    name: "theme が 8 分類の外",
    apply: (d) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.items[0].mention.proposedTheme = "<script>alert(1)</script>";
    },
  },
  {
    name: "intensity が 0..1 の外",
    apply: (d) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.items[0].mention.affect.intensity = 42;
    },
  },
  {
    name: "必須フィールドが欠落",
    apply: (d) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      delete d.items[0].mention.createdAt;
    },
  },
  {
    name: "型違い (statement が配列)",
    apply: (d) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.items[0].mention.statement = [SECRET];
    },
  },
  {
    name: "items が配列ですらない",
    apply: (d) => {
      d.items = SECRET;
    },
  },
];

describe("[単体] /extract の応答は返す前に検証する (poison document を作らない / #313 B-3)", () => {
  // 無いと何が静かに通るか: 型アサーションだけの素通しに戻り、契約に反する抽出結果が
  // そのまま Cosmos に upsert される。以降 problem.list が全 doc の parse で落ちて
  // **Problem 一覧が丸ごと 500 になり、画面から復旧できない**。攻撃者は要らず、
  // LLM の出力揺れだけで単一ユーザーでも踏む。
  //
  // 検証は「保存の前」= このクライアントの戻り値の時点であることが本質なので、
  // ここで throw することが router の materializeExtraction が実行されない保証になる。
  beforeEach(() => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
  });

  it("契約に反する応答は値を返さず llm-parse-failed で失敗する", async () => {
    await fc.assert(
      fc.asyncProperty(fc.constantFrom(...corruptions), async ({ apply }) => {
        const doc = validExtraction() as unknown as Record<string, unknown>;
        apply(doc);
        vi.stubGlobal(
          "fetch",
          vi.fn(async () => Response.json(doc, { status: 200 })),
        );

        const err = await extract({
          sessionId: "s1",
          existingProblems: [],
          messages: [],
        }).then(
          () => null,
          (e: unknown) => e,
        );

        expect(err).toBeInstanceOf(ExtractError);
        expect((err as ExtractError).kind).toBe("llm-parse-failed");
      }),
      { numRuns: corruptions.length * 2 },
    );
  });

  it("失敗のメッセージに応答の中身 (相談本文) を載せない", async () => {
    // 無いと: ExtractError の message は router がそのまま console.error に流し
    // Application Insights に残るので、**相談内容がログに漏れる** (監査 E-2 / 観点 S3)。
    const doc = validExtraction() as unknown as Record<string, unknown>;
    corruptions[0]!.apply(doc);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(doc, { status: 200 })),
    );

    await expect(extract({ sessionId: "s1", existingProblems: [], messages: [] })).rejects.toThrow(
      /items\[0\]\.mention\.proposedTheme/,
    );

    const err = await extract({ sessionId: "s1", existingProblems: [], messages: [] }).catch(
      (e: Error) => e,
    );
    expect(err.message).not.toContain(SECRET);
    expect(err.message).not.toContain("<script>");
  });

  it("JSON ですらない応答も llm-parse-failed に落として素通ししない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>502 Bad Gateway</html>", { status: 200 })),
    );

    const err = await extract({ sessionId: "s1", existingProblems: [], messages: [] }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ExtractError);
    expect((err as ExtractError).kind).toBe("llm-parse-failed");
  });

  it("契約どおりの応答はそのまま通す (正常系を切っていない)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(validExtraction(), { status: 200 })),
    );

    const res = await extract({ sessionId: "s1", existingProblems: [], messages: [] });
    expect(res.items[0]?.mention.statement).toBe(SECRET);
    expect(res.newProblemCount).toBe(1);
  });
});

// ---- /approve の 404 (#82 / PR #416 judge major-1) ---------------------------

describe("[単体] /approve の 404 は「承認レコードがもう無い」として区別する", () => {
  // 無いと何が静かに通るか: 失効した承認 (TTL 1h / ai-agent 再起動) への応答が
  // 汎用 Error のまま上がり、router が 500 相当で返す → フロントは「通信状況を確認して
  // 再試行」を出し続ける。再試行は**決して成功しない**ので承認カードが閉じられず、
  // その会話は承認も却下も次の発話もできない行き止まりになる (404 デッドロック)。
  // 種別が立つ場所はここ (HTTP status を見ている唯一の層) なので、この境界で固定する。
  beforeEach(() => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
  });

  // ai-agent が承認レコードについて返す 404 の detail (workflow.resume_after_approval の
  // ValueError そのまま)。3 本とも同じ「もう受け付けられない」に落ちる。
  it.each([
    { name: "未知 ID / TTL 失効", detail: "Approval not found: 'appr-1'" },
    { name: "checkpoint が消えた", detail: "Approval checkpoint not found: 'appr-1'" },
  ])("$name の 404 は ApprovalNotFoundError で失敗する", async ({ detail }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail }, { status: 404 })),
    );

    const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(ApprovalNotFoundError);
  });

  // 承認レコードについて何も言っていない 404 (PR #416 Codex 3 巡目 P2)。
  //
  // 無いと何が静かに通るか: ルート未配備 / プロキシの経路不整合 / ベース URL 違いの
  // 汎用 404 まで「もう受け付けられません」に化け、フロントは**生きている checkpoint を
  // 持つ承認カードを閉じる**。サーバは承認待ちのまま、ユーザーは承認も却下も再試行も
  // できない (デプロイ事故が「処理済み」に見えるので、原因にも辿り着けない)。
  it.each([
    { name: "FastAPI の既定 404 (ルート未配備)", body: { detail: "Not Found" } },
    { name: "別リソースの 404", body: { detail: "Session not found: 's1'" } },
    { name: "detail が文字列でない", body: { detail: { code: 404 } } },
    { name: "detail が無い", body: { error: "not found" } },
  ])("$name は ApprovalNotFoundError にしない (上流障害として扱う)", async ({ body }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body, { status: 404 })),
    );

    const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ApprovalNotFoundError);
  });

  it("JSON ですらない 404 本文 (プロキシの HTML) も ApprovalNotFoundError にしない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>404 Not Found</html>", { status: 404 })),
    );

    const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
      (e: unknown) => e,
    );

    expect(err).not.toBeInstanceOf(ApprovalNotFoundError);
    // 上流本文を例外文に転記しない (#313 B-3)。
    expect((err as Error).message).not.toContain("<html>");
  });

  it("404 以外の失敗は ApprovalNotFoundError にしない (上流障害と混ぜない)", async () => {
    // 上流障害まで「期限切れ」に写すと、復帰できる失敗 (再試行で直る) のカードまで
    // 閉じてしまい、承認待ちがサーバに残ったまま画面から消える。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("boom", { status: 503, statusText: "Service Unavailable" })),
    );

    const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ApprovalNotFoundError);
  });

  it("200 はそのまま応答を返す (正常系を切っていない)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ reply: "実行しました" }, { status: 200 })),
    );

    const res = await approve({ approvalRequestId: "appr-1", approved: true });
    expect(res.reply).toBe("実行しました");
  });
});

// ---- /approve の 409 (二重送信 / #82 / PO 裁定 2026-08-15 B 案) ---------------

describe("[単体] /approve の 409 は「すでに処理済み」として 404 と分ける", () => {
  // 無いと何が静かに通るか: 二重送信が「もう無い」(ApprovalNotFoundError) に混ざり、
  // フロントは**副作用が実行されたかどうかを言えなくなる** (承認 status の保存は
  // resume 実行の前なので、実行済みの再送も同じ応答になっていた)。
  // 「実行されたか分かりません」と案内された利用者は、送信済みのメールを
  // もう一度送る判断をしうる。種別と結果が立つ場所はここ (HTTP status を見る唯一の層)。
  beforeEach(() => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
  });

  it.each([
    { status: "approved" as const, processedAt: "2026-08-15T02:00:00+00:00" },
    { status: "rejected" as const, processedAt: "2026-08-15T02:00:00+00:00" },
    // 時刻を持たない古いレコード。**時刻の欠落で「未処理」に落とさない**
    { status: "approved" as const, processedAt: null },
  ])(
    "status=$status の 409 は ApprovalAlreadyProcessedError に写す (結果も運ぶ)",
    async ({ status, processedAt }) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json(
            {
              detail: `Approval already processed: '${status}'`,
              status,
              processed_at: processedAt,
            },
            { status: 409 },
          ),
        ),
      );

      const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
        (e: unknown) => e,
      );

      expect(err).toBeInstanceOf(ApprovalAlreadyProcessedError);
      expect((err as ApprovalAlreadyProcessedError).status).toBe(status);
      expect((err as ApprovalAlreadyProcessedError).processedAt).toBe(processedAt);
      // 404 側と混ざっていないこと (混ざると呼び出し側の分岐順で静かに元の挙動へ戻る)
      expect(err).not.toBeInstanceOf(ApprovalNotFoundError);
    },
  );

  // 承認レコードについて何も言っていない 409。404 側と同じ規律で、**形が合うものだけ**写す。
  //
  // 無いと何が静かに通るか: プロキシや別レイヤが返す汎用 409 まで「すでに承認済みです」に
  // 化け、生きている承認カードが**実行済みの顔をして閉じる** (サーバは承認待ちのまま)。
  it.each([
    { name: "status が無い", body: { detail: "Conflict" } },
    { name: "status が未知の値", body: { detail: "x", status: "pending", processed_at: null } },
    { name: "detail が無い", body: { status: "approved", processed_at: null } },
    { name: "body が配列", body: [] },
  ])("$name の 409 は ApprovalAlreadyProcessedError にしない", async ({ body }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body, { status: 409 })),
    );

    const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ApprovalAlreadyProcessedError);
  });

  it("JSON ですらない 409 本文 (プロキシの HTML) も写さず、本文を例外文に転記しない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>409 Conflict</html>", { status: 409 })),
    );

    const err = await approve({ approvalRequestId: "appr-1", approved: true }).catch(
      (e: unknown) => e,
    );

    expect(err).not.toBeInstanceOf(ApprovalAlreadyProcessedError);
    // 上流本文を例外文に転記しない (#313 B-3)
    expect((err as Error).message).not.toContain("<html>");
  });
});
