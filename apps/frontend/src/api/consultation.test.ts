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

import { trpc } from "../trpc/client";
import { chatStreamFetch, ttsPrefetchFetch } from "./http";
import { sendMessage, startNewConsultation } from "./consultation";
import { clearStreamingReply, getStreamingReply } from "./streamingReply";
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

    const message = await sendMessage("s1", "疲れました");

    expect(message.role).toBe("assistant");
    expect(message.text).toBe("それは大変でしたね。");
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

    const message = await sendMessage("s1", "m");

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

    const message = await sendMessage("s1", "m");

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
