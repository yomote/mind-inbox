import * as mock from "../mockApi";
import type { ApprovalRequest, AssistantReply, ChatMessage, ConsultationSession } from "./types";
import { trpc } from "../trpc/client";
import { chatStreamFetch, ttsPrefetchFetch, useMock } from "./http";
import { parseSseJsonStream } from "./sse";
import { appendStreamingReply, beginStreamingReply, clearStreamingReply } from "./streamingReply";
import { reportStubbedResponse, resetStubbedResponse } from "./stubStatus";

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
 * 応答の承認フラグを UI の承認要求へ写す (#82 / G1 / dialogue-session.mdx §5.9)。
 *
 * `requiresApproval` が立っていても id が無ければ承認要求を作らない — 承認 API を
 * 呼べない要求カードは「押しても何も起きないボタン」にしかならないため。
 */
function toApprovalRequest(
  requiresApproval: boolean | undefined,
  approvalRequestId: string | null | undefined,
  reply: string,
): ApprovalRequest | null {
  if (requiresApproval !== true || !approvalRequestId) return null;
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
 */
export async function respondToApproval(
  approvalRequestId: string,
  approved: boolean,
): Promise<ChatMessage> {
  if (useMock) return mock.respondToApproval(approvalRequestId, approved);

  const res = await trpc.consultation.approve.mutate({ approvalRequestId, approved });
  return {
    id: crypto.randomUUID(),
    role: "assistant",
    text: res.reply,
    createdAt: new Date().toISOString(),
  };
}
