/**
 * [L2] sendMessage のストリーミング経路 (real 分岐) の service-level test。
 *
 * 無いと何が静かに通るか: SSE → ストア反映 → done で最終メッセージ、の配線か
 * tRPC フォールバックのどちらかが壊れても、もう片方が動いていれば UI 上は
 * 「返事が出る」ため、逐次表示の全滅や二重応答が緑のまま通る。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../trpc/client", () => ({
  trpc: {
    consultation: {
      start: { mutate: vi.fn() },
      sendMessage: { mutate: vi.fn() },
      approve: { mutate: vi.fn() },
      organize: { mutate: vi.fn() },
      createPlan: { mutate: vi.fn() },
    },
  },
}));

vi.mock("./http", () => ({
  chatStreamFetch: vi.fn(),
  ttsPrefetchFetch: vi.fn(async () => new Response(null, { status: 202 })),
  // useMock は http.ts に一元化された (#137)。このテストは real 経路を検証する
  useMock: false,
}));

import { TRPCClientError } from "@trpc/client";
import { APPROVAL_NOT_FOUND_TOKEN } from "../../../bff/src/trpc/errorTokens";
import { trpc } from "../trpc/client";
import { chatStreamFetch, ttsPrefetchFetch } from "./http";
import {
  ApprovalExpired,
  ApprovalRequestUnusable,
  respondToApproval,
  sendMessage,
  startNewConsultation,
} from "./consultation";
import { clearStreamingReply, getStreamingReply } from "./streamingReply";
import { resetSpeedScaleForTest, setSpeedScale } from "../voice/speedScale";
import { getStubbedResponse, reportStubbedResponse, resetStubbedResponse } from "./stubStatus";

function sseResponse(events: object[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  clearStreamingReply();
  resetStubbedResponse();
  // モジュールのストアはテスト間で持ち越される (#242)。
  resetSpeedScaleForTest();
});

describe("[L2] sendMessage — ストリーミング経路", () => {
  it("delta を受けてストアが伸び、done の reply が最終メッセージになる", async () => {
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        { type: "delta", text: "それは" },
        { type: "delta", text: "大変でしたね。" },
        {
          type: "done",
          response: { reply: "それは大変でしたね。", requires_approval: false, citations: [] },
        },
      ]),
    );

    const { message, approval } = await sendMessage("s1", "疲れました");

    expect(message.role).toBe("assistant");
    expect(message.text).toBe("それは大変でしたね。");
    // 承認不要の応答で承認要求を作らない (常時承認カードが出る実装を弾く)
    expect(approval).toBeNull();
    // ストアは done では消さない (ちらつき防止) — 最終メッセージと同じ id で残る
    const streaming = getStreamingReply();
    expect(streaming?.id).toBe(message.id);
    expect(streaming?.text).toBe("それは大変でしたね。");
    expect(trpc.consultation.sendMessage.mutate).not.toHaveBeenCalled();
  });

  it("プリフェッチには途中経過テキストをそのまま渡す (文分割は BFF の責務)", async () => {
    // 無いと: フロント側に文分割を持たせる実装へ逆戻りしても気づけない。フロントと BFF は
    // 別デプロイ単位なので、分割が両側にあるとズレた瞬間にキャッシュ全ミスで先行合成が死ぬ
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        { type: "delta", text: "一つ目の文はこれです。二つ目" },
        { type: "delta", text: "の文はこちらです。" },
        {
          type: "done",
          response: { reply: "一つ目の文はこれです。二つ目の文はこちらです。" },
        },
      ]),
    );

    await sendMessage("s1", "m");

    const sentTexts = vi.mocked(ttsPrefetchFetch).mock.calls.map(([text]) => text);
    // 送るのは「今までに届いた累積テキスト」— 文の切れ目で切ったものではない
    expect(sentTexts.length).toBeGreaterThan(0);
    for (const sent of sentTexts) {
      expect("一つ目の文はこれです。二つ目の文はこちらです。").toContain(sent);
    }
    expect(sentTexts[0]).toBe("一つ目の文はこれです。二つ目");
  });

  it("ストリーム不可 (HTTP 404) は tRPC mutation にフォールバックする", async () => {
    vi.mocked(chatStreamFetch).mockResolvedValue(new Response("nf", { status: 404 }));
    vi.mocked(trpc.consultation.sendMessage.mutate).mockResolvedValue({
      reply: "全文応答",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    } as never);

    const { message } = await sendMessage("s1", "m");

    expect(message.text).toBe("全文応答");
    expect(trpc.consultation.sendMessage.mutate).toHaveBeenCalledWith({
      sessionId: "s1",
      message: "m",
    });
  });

  it("error イベント / done 欠落もフォールバックし、途中経過バブルは消す", async () => {
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        { type: "delta", text: "途中" },
        { type: "error", message: "LLM connection lost" },
      ]),
    );
    vi.mocked(trpc.consultation.sendMessage.mutate).mockResolvedValue({
      reply: "取り直した全文",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    } as never);

    const { message } = await sendMessage("s1", "m");

    expect(message.text).toBe("取り直した全文");
    // 中途半端な「途中」バブルが残らない
    expect(getStreamingReply()).toBeNull();
  });

  it("新しい相談の開始で前セッションの途中経過を捨てる (幽霊バブル防止)", async () => {
    // 無いと: ストアはモジュール global で「同じ id の最終メッセージが messages に
    // 現れたら隠す」方式のため、セッションが変わると id が一致せず前セッションの応答が
    // 新セッションに幽霊バブル (キャレット付き) として表示される。
    // アプリ内遷移ではリロードが挟まらないので実際に踏む (ブラウザで再現確認済み)。
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        { type: "delta", text: "前セッションの応答" },
        { type: "done", response: { reply: "前セッションの応答" } },
      ]),
    );
    await sendMessage("s1", "m");
    expect(getStreamingReply()).not.toBeNull();

    vi.mocked(trpc.consultation.start.mutate).mockResolvedValue({
      session: { id: "s2", title: "相談セッション", messages: [] },
    } as never);
    await startNewConsultation("");

    expect(getStreamingReply()).toBeNull();
  });

  it("新しい相談の開始で前セッションの stub 状態を持ち越さない (#146)", async () => {
    // 無いと: stub 応答を受けた後にホームへ戻って新しい相談を始めると、空 concern の
    // start は stubbed を返さないため module-global の状態が true のまま残り、
    // AI 応答が 1 つも無い新セッションに「AI サービス未接続」バナーが出続ける退行が静かに通る。
    reportStubbedResponse(true);
    vi.mocked(trpc.consultation.start.mutate).mockResolvedValue({
      session: { id: "s2", title: "相談セッション", messages: [] },
    } as never);

    await startNewConsultation("");

    expect(getStubbedResponse()).toBe(false);
  });

  it("start が失敗したら前セッションの stub 状態を保持する (#146)", async () => {
    // 無いと: 開始リクエストの失敗時 (useConsultation は旧セッションを保持して遷移しない)
    // に先走りのリセットが走り、旧 stub セッションに戻ったのにバナーだけが消える —
    // 「stub 応答が本物のふりをする」#146 の症状が開始失敗の裏道から復活する退行が静かに通る。
    reportStubbedResponse(true);
    vi.mocked(trpc.consultation.start.mutate).mockRejectedValue(new Error("boom"));

    await expect(startNewConsultation("")).rejects.toThrow("boom");

    expect(getStubbedResponse()).toBe(true);
  });

  it("テーマ入力ありの開始が stub 応答なら、リセット後にバナー状態が再点灯する (#146)", async () => {
    // 無いと: 開始時リセットが「開始の応答そのものが stub」のケースまで潰し、
    // テーマ入力で始めた stub セッションの最初の応答が本物のふりをする退行が静かに通る。
    vi.mocked(trpc.consultation.start.mutate).mockResolvedValue({
      session: { id: "s3", title: "仕事が辛い", messages: [] },
      stubbed: true,
    } as never);

    await startNewConsultation("仕事が辛い");

    expect(getStubbedResponse()).toBe(true);
  });
});

describe("[単体] 先行合成の読み上げ速度 (#242)", () => {
  it("プリフェッチは現在の読み上げ速度で焼かせる", async () => {
    // 無いと: 先行合成だけ等倍で焼き、本合成が設定速度を要求して BFF のキャッシュを
    // 全ミスする。音は普通に鳴るので、体感の待ち時間が #185 以前へ戻ったことしか
    // 症状に出ない (どのテストも E2E も緑のまま)。
    setSpeedScale(1.35);
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        { type: "delta", text: "一つ目の文はこれです。二つ目" },
        { type: "delta", text: "の文はこちらです。" },
        { type: "done", response: { reply: "一つ目の文はこれです。二つ目の文はこちらです。" } },
      ]),
    );

    await sendMessage("s1", "m");

    const speeds = vi.mocked(ttsPrefetchFetch).mock.calls.map((call) => call[2]);
    expect(speeds.length).toBeGreaterThan(0);
    expect(new Set(speeds)).toEqual(new Set([1.35]));
  });
});

describe("[単体] 副作用ツールの承認要求 (#82 / G1)", () => {
  it("done の requires_approval / approval_request_id を承認要求として返す", async () => {
    // 無いと: 承認が要る応答をフロントが「ただの返事」として扱っても画面は普通に
    // 描画されるため、承認カードが一度も出ないまま (= G1 が画面から消えたまま)
    // サーバだけが承認待ちで止まっている状態が静かに通る。
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        { type: "delta", text: "「send_reply」を実行するには承認が必要です。" },
        {
          type: "done",
          response: {
            reply: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
            requires_approval: true,
            approval_request_id: "appr-1",
            citations: [],
          },
        },
      ]),
    );

    const { approval } = await sendMessage("s1", "この件、返信しておいて");

    expect(approval).toEqual({
      id: "appr-1",
      // カードは単体で「何を実行しようとしているか」が読める必要がある (§5.9)
      description: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
    });
  });

  it("approval_request_id が無い応答はエラーにする (承認不要と混同しない)", async () => {
    // 無いと: 承認 ID の無い「承認が要る」応答を**普通の返事**として表示してしまう。
    // 承認 API を呼ぶ手段が無いので画面には何の要求も出ず、サーバだけが承認待ちで
    // 止まったままになる (BFF の zod もこの組み合わせを禁止していないので実際に届きうる)。
    // ここが null 返しに戻ると、カードもエラーも出ないまま静かに通る。
    vi.mocked(chatStreamFetch).mockResolvedValue(
      sseResponse([
        {
          type: "done",
          response: { reply: "承認が必要です", requires_approval: true, citations: [] },
        },
      ]),
    );

    await expect(sendMessage("s1", "m")).rejects.toBeInstanceOf(ApprovalRequestUnusable);
    // 契約違反は転送の失敗ではないので、tRPC で取り直して隠さない
    expect(trpc.consultation.sendMessage.mutate).not.toHaveBeenCalled();
    // 途中経過バブルは残さない
    expect(getStreamingReply()).toBeNull();
  });

  it("tRPC フォールバック経路でも承認要求を落とさない", async () => {
    // 無いと: ストリーミングが使えない環境 (BFF 旧版 / 通信断) でだけ承認カードが
    // 消え、副作用ツールが「承認なしで実行できるように見える」状態が静かに通る。
    vi.mocked(chatStreamFetch).mockResolvedValue(new Response("nf", { status: 404 }));
    vi.mocked(trpc.consultation.sendMessage.mutate).mockResolvedValue({
      reply: "「archive_message」を実行するには承認が必要です。実行してよろしいですか？",
      requiresApproval: true,
      approvalRequestId: "appr-2",
      citations: [],
    } as never);

    const { approval } = await sendMessage("s1", "m");

    expect(approval?.id).toBe("appr-2");
  });

  it("承認 / 却下は approved をそのまま BFF へ渡し、応答を発話にする", async () => {
    // 無いと: approved の真偽反転 (承認したのに却下される / 却下したのに実行される)
    // が静かに通る。どちらも「返事が返ってくる」ので画面上は区別が付かない。
    vi.mocked(trpc.consultation.approve.mutate).mockResolvedValue({
      reply: "操作はキャンセルされました。他にご用件はありますか？",
    } as never);

    const rejected = await respondToApproval("appr-1", false);

    expect(trpc.consultation.approve.mutate).toHaveBeenCalledWith({
      approvalRequestId: "appr-1",
      approved: false,
    });
    expect(rejected.role).toBe("assistant");
    expect(rejected.text).toBe("操作はキャンセルされました。他にご用件はありますか？");

    vi.mocked(trpc.consultation.approve.mutate).mockResolvedValue({
      reply: "[stub] Reply sent to team@example.com.",
    } as never);

    await respondToApproval("appr-1", true);

    expect(trpc.consultation.approve.mutate).toHaveBeenLastCalledWith({
      approvalRequestId: "appr-1",
      approved: true,
    });
  });

  /** BFF が返す tRPC エラーの形を作る (message + data.code)。 */
  function trpcError(message: string, code: string) {
    const err = new TRPCClientError(message);
    Object.defineProperty(err, "data", { value: { code } });
    return err;
  }

  it("NOT_FOUND + approval-not-found token は ApprovalExpired に写す", async () => {
    // 無いと: 失効した承認 (ai-agent の TTL 1h / 再起動) や処理済みの承認への応答が通信失敗と同じ
    // 汎用エラーになり、UI は「再試行してください」しか出せない。再試行は決して
    // 成功しないので承認カードが閉じられず、その会話は永久に詰む (PR #416 judge major-1)。
    // token は BFF の errorTokens.ts と同じ値を import して使う (書き写さない)。
    vi.mocked(trpc.consultation.approve.mutate).mockRejectedValue(
      trpcError(APPROVAL_NOT_FOUND_TOKEN, "NOT_FOUND"),
    );

    await expect(respondToApproval("appr-1", true)).rejects.toBeInstanceOf(ApprovalExpired);
  });

  it("token の無い NOT_FOUND (procedure 未配備) は ApprovalExpired にしない", async () => {
    // 無いと: tRPC 自身が返す NOT_FOUND (`No procedure found on path ...` = frontend と
    // BFF の版ずれ / BFF 単独配備 / ルーティング事故) まで「もう受け付けられない」に化け、
    // **生きている checkpoint を持つ承認カードが閉じる**。サーバは承認待ちのまま、画面
    // からは消えるので、承認も却下も再試行もできない (Codex 4 巡目 P2)。
    vi.mocked(trpc.consultation.approve.mutate).mockRejectedValue(
      trpcError('No procedure found on path "consultation.approve"', "NOT_FOUND"),
    );

    const err = await respondToApproval("appr-1", true).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ApprovalExpired);
  });

  it("token だけ一致していてもコードが NOT_FOUND でなければ ApprovalExpired にしない", async () => {
    // 無いと: 判定が message だけに退化しても気づけない (上流障害の文面が偶然一致した
    // ときにカードを閉じてしまう)。
    vi.mocked(trpc.consultation.approve.mutate).mockRejectedValue(
      trpcError(APPROVAL_NOT_FOUND_TOKEN, "INTERNAL_SERVER_ERROR"),
    );

    const err = await respondToApproval("appr-1", true).catch((e: unknown) => e);

    expect(err).not.toBeInstanceOf(ApprovalExpired);
  });

  it("NOT_FOUND 以外の失敗は ApprovalExpired にしない (再試行で直る失敗を消さない)", async () => {
    // 無いと: 上流障害まで「もう受け付けられない」に化けてカードが閉じ、サーバには承認待ちが
    // 残ったまま画面から消える。
    vi.mocked(trpc.consultation.approve.mutate).mockRejectedValue(new TRPCClientError("boom"));

    const err = await respondToApproval("appr-1", true).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ApprovalExpired);
  });
});
