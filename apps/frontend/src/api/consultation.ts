import * as mock from "../mockApi";
import type { ApprovalRequest, AssistantReply, ChatMessage, ConsultationSession } from "./types";
import { trpc } from "../trpc/client";
import { chatStreamFetch, ttsPrefetchFetch, useMock } from "./http";
import { parseSseJsonStream } from "./sse";
import { appendStreamingReply, beginStreamingReply, clearStreamingReply } from "./streamingReply";
import { reportStubbedResponse, resetStubbedResponse } from "./stubStatus";
import { isNotFound } from "./trpcError";

const voicevoxSpeaker = Number(import.meta.env.VITE_VOICEVOX_SPEAKER || "3");

export async function startNewConsultation(concern: string): Promise<ConsultationSession> {
  // 直前セッションのストリーミング途中経過を必ず捨てる。ストアはモジュール global で
  // 「最終メッセージと同じ id が messages に現れたら隠す」方式のため、セッションが
  // 変わると id が一致しなくなり、前セッションの応答が新セッションに幽霊バブルとして
  // 出てしまう (アプリ内遷移ではリロードが挟まらないので実際に踏む)。
  clearStreamingReply();
  if (useMock) {
    const session = await mock.startNewConsultation(concern);
    // 成功後にリセット (real 経路と同じ意味論)。mock は stub フラグを立てないが、
    // ビルド切り替え等で残った状態を空の新セッションに持ち越さない。
    resetStubbedResponse();
    return session;
  }
  const { session, stubbed } = await trpc.consultation.start.mutate({ concern });
  // 前セッションの stub 状態は**新セッションの作成が成功してから**捨てる (#146)。
  // 冒頭でリセットすると、start が失敗したとき (useConsultation は旧セッションを保持して
  // 遷移しない) に旧 stub セッションへ戻ってもバナーが消えたままになる。
  // reportStubbedResponse は undefined (空 concern 開始 = AI 非呼び出し / 実応答) を
  // false と扱うのでリセットを兼ね、テーマ入力あり開始が stub ならそのまま再点灯する。
  reportStubbedResponse(stubbed);
  return session;
}

/**
 * チャット応答ストリーミング (#120 / ADR 0024) の SSE イベント。
 * wire 契約の真実は ai-agent の pydantic (ChatStreamDelta / Done / Error)。
 */
type ChatStreamEvent =
  | { type: "delta"; text: string }
  | {
      type: "done";
      response: {
        reply: string;
        requires_approval?: boolean;
        approval_request_id?: string | null;
        citations?: string[];
        /** stub 判別フラグ (#146)。BFF が合成する stub ストリームにだけ現れる。 */
        stubbed?: boolean;
      };
    }
  | { type: "error"; message: string };

function asChatStreamEvent(raw: unknown): ChatStreamEvent | null {
  if (typeof raw !== "object" || raw === null) return null;
  const event = raw as Record<string, unknown>;
  if (event.type === "delta" && typeof event.text === "string") {
    return { type: "delta", text: event.text };
  }
  if (event.type === "done" && typeof event.response === "object" && event.response !== null) {
    const response = event.response as Record<string, unknown>;
    if (typeof response.reply === "string") {
      return {
        type: "done",
        response: {
          reply: response.reply,
          requires_approval:
            typeof response.requires_approval === "boolean"
              ? response.requires_approval
              : undefined,
          approval_request_id:
            typeof response.approval_request_id === "string" ? response.approval_request_id : null,
          citations: Array.isArray(response.citations)
            ? response.citations.filter((c): c is string => typeof c === "string")
            : undefined,
          stubbed: response.stubbed === true,
        },
      };
    }
  }
  if (event.type === "error") {
    return {
      type: "error",
      message: typeof event.message === "string" ? event.message : "unknown",
    };
  }
  return null;
}

/** プリフェッチ要求の最小間隔 (ms)。delta 毎に投げると往復が過剰になるため間引く。 */
const PREFETCH_INTERVAL_MS = 400;

/**
 * ストリーミングの途中経過テキストを BFF に渡し、確定した文を先行合成させる (#120)。
 *
 * **フロントは文の切れ目を判定しない** — 「今までに届いたテキスト」を丸ごと渡し、
 * どこまでが確定文かは BFF (`prefetchTts`) が決める。フロントと BFF は別デプロイ
 * 単位なので、両者が同じ分割アルゴリズムを持つと片方だけ再デプロイした瞬間に
 * キャッシュが全ミスして先行合成が静かに無効化される (PR #132 レビュー / ADR 0024)。
 * ここが時間間引きなのも同じ理由で、間引き方がズレても「往復が増減する」だけで
 * キャッシュヒット率には影響しない。
 */
function createSentencePrefetcher() {
  let lastSentAt = 0;

  return (accumulated: string) => {
    const now = Date.now();
    if (now - lastSentAt < PREFETCH_INTERVAL_MS) return;
    lastSentAt = now;

    void ttsPrefetchFetch(accumulated, voicevoxSpeaker).catch(() => {
      // プリフェッチはベストエフォート — 失敗しても最終合成が普通に走る
    });
  };
}

/**
 * 「承認が要る」と言われたのに承認 ID が無い応答 (#82 / PR #416 Codex P2)。
 *
 * BFF の `ChatReplySchema` はこの組み合わせを禁止していない (`approvalRequestId` は
 * nullable) ため、上流の不整合でも SSE はそのまま素通しする。**承認不要と同じ扱いに
 * しない** — 承認 API を呼ぶ手段が無いので、承認カードを出しても押せず、黙って
 * 通常応答にするとサーバだけが承認待ちで止まったまま画面に何も出ない。
 */
export class ApprovalRequestUnusable extends Error {
  constructor() {
    super("chat response requires approval but has no approvalRequestId");
    this.name = "ApprovalRequestUnusable";
  }
}

/**
 * 承認要求がもう受け付けられない (#82 / PR #416 judge major-1)。
 *
 * ai-agent の承認レコードは TTL 1 時間で失効し、in-memory 構成では再起動でも消える。
 * BFF はこれを `NOT_FOUND` で返す。**通信失敗と同じ扱いにしない** — 再試行しても
 * 永久に成功しないので、「再試行してください」を出し続けると承認カードが閉じられず
 * 会話が二度と進まなくなる (404 デッドロック)。UI 側はこれを受けてカードを閉じ、
 * 次の発話を送れる状態に戻す。
 *
 * **クラス名の "Expired" は「もう受け付けられない」の略で、「期限切れ」の断定ではない**
 * (PR #416 judge / Codex P1)。ai-agent は approved / rejected 済みの ID にも同じ 404 を
 * 返す (`Approval already processed`) ため、**この例外から「操作は実行されていない」は
 * 導けない**。ユーザーに出す文面で断定しないこと (`FAILURE_MESSAGE.approvalExpired`)。
 * 区別できるようにするには契約側の冪等化 (409 + status 返却) が要る → #82 に選択肢を記録。
 */
export class ApprovalExpired extends Error {
  constructor() {
    super("approval request no longer accepted (expired, released, or already processed)");
    this.name = "ApprovalExpired";
  }
}

/**
 * 応答の承認フラグを UI の承認要求へ写す (#82 / G1 / dialogue-session.mdx §5.9)。
 */
function toApprovalRequest(
  requiresApproval: boolean | undefined,
  approvalRequestId: string | null | undefined,
  reply: string,
): ApprovalRequest | null {
  if (requiresApproval !== true) return null;
  if (!approvalRequestId) throw new ApprovalRequestUnusable();
  return { id: approvalRequestId, description: reply };
}

async function sendMessageStreaming(
  sessionId: string,
  text: string,
  messageId: string,
): Promise<AssistantReply> {
  const res = await chatStreamFetch(sessionId, text);
  if (!res.ok || !res.body) {
    throw new Error(`chat stream unavailable: HTTP ${res.status}`);
  }

  beginStreamingReply(messageId);

  let accumulated = "";
  const prefetchSentences = createSentencePrefetcher();
  let finalReply: string | null = null;
  let approval: ApprovalRequest | null = null;

  for await (const raw of parseSseJsonStream(res.body)) {
    const event = asChatStreamEvent(raw);
    if (!event) continue;
    if (event.type === "delta") {
      accumulated += event.text;
      appendStreamingReply(event.text);
      prefetchSentences(accumulated);
    } else if (event.type === "done") {
      finalReply = event.response.reply;
      approval = toApprovalRequest(
        event.response.requires_approval,
        event.response.approval_request_id,
        event.response.reply,
      );
      // stub 応答の可視化 (#146): 実応答 (フラグ無し) なら下ろす。
      reportStubbedResponse(event.response.stubbed);
    } else {
      throw new Error(`chat stream error event: ${event.message}`);
    }
  }

  if (finalReply === null) {
    // done を受け取れないまま切れた (ネットワーク断など) → 完全な応答を取り直す
    throw new Error("chat stream ended without done event");
  }

  return {
    message: {
      id: messageId,
      role: "assistant",
      text: finalReply,
      createdAt: new Date().toISOString(),
    },
    approval,
  };
}

/** mock でもストリーミングの体感 (伸びるバブル) を再現する (ADR 0004: データの真実は mock 側)。 */
async function streamMockReply(sessionId: string, text: string): Promise<AssistantReply> {
  const reply = await mock.sendMessage(sessionId, text);
  beginStreamingReply(reply.message.id);
  const step = 4;
  for (let i = 0; i < reply.message.text.length; i += step) {
    appendStreamingReply(reply.message.text.slice(i, i + step));
    await new Promise((resolve) => setTimeout(resolve, 45));
  }
  return reply;
}

export async function sendMessage(sessionId: string, text: string): Promise<AssistantReply> {
  if (useMock) return streamMockReply(sessionId, text);

  const messageId = crypto.randomUUID();
  try {
    return await sendMessageStreaming(sessionId, text, messageId);
  } catch (err) {
    if (err instanceof ApprovalRequestUnusable) {
      // 上流の契約違反は**転送の失敗ではない** — 取り直しても同じものが返るだけなので
      // フォールバックで隠さず、そのまま UI のエラー表示へ流す (承認不要と区別する)。
      clearStreamingReply();
      throw err;
    }
    // ストリーミングは強化であって依存にしない (ADR 0024) — 失敗したら従来の
    // tRPC mutation で全文を取り直す。途中経過バブルはここで消す。
    console.warn("[sendMessage] streaming failed — falling back to tRPC mutation", err);
    clearStreamingReply();
    const res = await trpc.consultation.sendMessage.mutate({
      sessionId,
      message: text,
    });
    // stub 応答の可視化 (#146)。ストリーミング不能時のフォールバック経路でも見落とさない。
    reportStubbedResponse(res.stubbed);
    return {
      message: {
        id: messageId,
        role: "assistant",
        text: res.reply,
        createdAt: new Date().toISOString(),
      },
      // フォールバック経路でも承認要求は落とさない (#82)。ここを落とすと
      // 「ストリーミングが死んでいる環境でだけ副作用ツールが承認なしに見える」になる。
      approval: toApprovalRequest(res.requiresApproval, res.approvalRequestId, res.reply),
    };
  }
}

/**
 * 承認要求への応答 (承認 = 実行 / 却下 = キャンセル) を返し、続きの応答を受け取る
 * (#82 / G1 / dialogue-session.mdx §5.9)。
 *
 * **却下も送る**: 送らないとサーバ側の承認待ち (checkpoint) が宙に浮いたままになる。
 *
 * @throws {ApprovalExpired} 承認レコードが失効している (BFF の `NOT_FOUND`)。
 *         再試行では回復しないので、通信失敗と区別して投げる。
 */
export async function respondToApproval(
  approvalRequestId: string,
  approved: boolean,
): Promise<ChatMessage> {
  if (useMock) return mock.respondToApproval(approvalRequestId, approved);

  let res: { reply: string };
  try {
    res = await trpc.consultation.approve.mutate({ approvalRequestId, approved });
  } catch (err) {
    // 承認レコードがもう受け付けられない: 期限切れ (TTL 1h) / ai-agent 再起動で消えた、
    // または**すでに approved / rejected 済み**。ai-agent はこの 3 つを区別せず 404 を返す。
    // ここで種別を立てないと UI は「通信失敗 → 再試行」しか出せず、
    // 何度押しても同じ 404 に当たり続ける (#82 / PR #416 judge major-1)。
    if (isNotFound(err)) throw new ApprovalExpired();
    throw err;
  }
  return {
    id: crypto.randomUUID(),
    role: "assistant",
    text: res.reply,
    createdAt: new Date().toISOString(),
  };
}
