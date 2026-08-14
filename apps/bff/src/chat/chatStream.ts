/**
 * チャット応答ストリーミング (#120 / ADR 0024) の BFF 側サービス。
 *
 * ai-agent の POST /chat/stream (SSE) をバイトストリームのまま素通しする。
 * イベント契約の真実は ai-agent の pydantic (ChatStreamDelta / Done / Error) で、
 * BFF は中身を解釈しない (解釈するのはフロントのパーサ)。
 *
 * 縮退:
 *   - AI_AGENT_BASE_URL 未設定 → stub の逐次イベントを生成 (ローカル/未結線でも動く)
 *   - upstream が 200 以外 / body なし → 非ストリーミングの sendChatMessage に切り替え、
 *     結果を delta 1 発 + done の合成 SSE にして返す (フロントは差を意識しない)
 */

import { config } from "../config";
import { describeError, logEvent } from "../observability/telemetry";
import { serviceHeaders } from "../clients/serviceToken";
import {
  sendChatMessage,
  type ChatRequest,
  type ChatResponse,
  type StubMarked,
} from "../clients/aiAgentClient";

const encoder = new TextEncoder();

/** SSE の 1 イベント (`data: {...}\n\n`) にエンコードする。 */
export function encodeSseEvent(payload: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(payload)}\n\n`);
}

/**
 * ai-agent の ChatResponse を done イベントの wire 形 (snake_case) に写す。
 * フィールド集合の真実は pydantic の ChatStreamDone / ChatResponse で、
 * BFF 側の鏡 (`clients/aiAgentContracts.ts` の ChatStreamDoneSchema) を L0 契約テストが
 * 突き合わせている。stub もフォールバックもここを通す (手書きコピーを 1 箇所に閉じる)。
 */
function toDoneEvent(res: StubMarked<ChatResponse>): unknown {
  return {
    type: "done",
    response: {
      reply: res.reply,
      requires_approval: res.requiresApproval,
      approval_request_id: res.approvalRequestId,
      citations: res.citations,
      // stub 判別フラグ (#146)。BFF が合成するストリーム (stub / フォールバック) にだけ
      // 現れうる注釈で、実 ai-agent は返さない — よって pydantic の wire 契約とその鏡
      // (aiAgentContracts.ts) には含めない。tRPC 側の真実は router.ts の ChatReplySchema。
      ...(res.stubbed ? { stubbed: true } : {}),
    },
  };
}

/** 非ストリーミングの応答を delta + done の合成 SSE ストリームにする。 */
function syntheticStream(res: StubMarked<ChatResponse>): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      if (res.reply) {
        controller.enqueue(encodeSseEvent({ type: "delta", text: res.reply }));
      }
      controller.enqueue(encodeSseEvent(toDoneEvent(res)));
      controller.close();
    },
  });
}

/** AI_AGENT_BASE_URL 未設定時の stub — 逐次配信の体感をローカルでも再現する。 */
function stubStream(req: ChatRequest): ReadableStream<Uint8Array> {
  const reply = `[stub] received: "${req.message}"`;
  const chunkSize = 6;
  const chunks: string[] = [];
  for (let i = 0; i < reply.length; i += chunkSize) {
    chunks.push(reply.slice(i, i + chunkSize));
  }

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encodeSseEvent({ type: "delta", text: chunk }));
        await new Promise((resolve) => setTimeout(resolve, 30));
      }
      controller.enqueue(
        encodeSseEvent(
          toDoneEvent({
            reply,
            requiresApproval: false,
            approvalRequestId: null,
            citations: [],
            stubbed: true,
          }),
        ),
      );
      controller.close();
    },
  });
}

/**
 * チャット 1 ターン分の SSE バイトストリームを開く。
 * 返り値はそのまま HTTP レスポンス body に流せる。
 */
export async function openChatStream(req: ChatRequest): Promise<ReadableStream<Uint8Array>> {
  if (!config.aiAgentBaseUrl) {
    logEvent("dependency.skipped", {
      target: "ai-agent",
      operation: "POST /chat/stream",
      reason: "base-url-unset",
      sessionId: req.sessionId,
    });
    return stubStream(req);
  }

  const url = `${config.aiAgentBaseUrl}/chat/stream`;

  // ここは #293 の本丸なので `trackDependency` ではなく手で書く — この呼び出しは
  // **ヘッダが返った時点で終わり**で、本体のバイトはこの後に流れる。共通ラッパの
  // 「終了 = 完了」に混ぜると「流し切った」と読める行になってしまう。
  // outcome は `headers-received` (ヘッダまで) と明示して嘘をつかないようにする。
  const startedAt = Date.now();
  logEvent("dependency.start", {
    target: "ai-agent",
    operation: "POST /chat/stream",
    url,
    sessionId: req.sessionId,
    chars: req.message.length,
  });

  let upstream: Response | null = null;
  let failure: unknown = null;
  try {
    upstream = await fetch(url, {
      method: "POST",
      headers: await serviceHeaders(config.aiAgentAudience, {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify({ session_id: req.sessionId, message: req.message }),
    });
  } catch (err) {
    failure = err;
  }

  if (!upstream || !upstream.ok || !upstream.body) {
    // 旧版 ai-agent (エンドポイント未実装 404 等) や一時失敗 → 非ストリーミングで成立させる。
    // **縮退したことを黙らせない** — 「SSE で流れた」と「/chat に落ちた」は画面上ほぼ
    // 同じに見えるので、ログで区別できないと #293 と同じ誤診に戻る。
    logEvent("dependency.end", {
      target: "ai-agent",
      operation: "POST /chat/stream",
      sessionId: req.sessionId,
      outcome: "fallback-to-post-chat",
      upstreamStatus: upstream?.status ?? null,
      ms: Date.now() - startedAt,
      ...(failure ? describeError(failure) : {}),
    });
    return syntheticStream(await sendChatMessage(req));
  }

  logEvent("dependency.end", {
    target: "ai-agent",
    operation: "POST /chat/stream",
    sessionId: req.sessionId,
    outcome: "headers-received",
    upstreamStatus: upstream.status,
    ms: Date.now() - startedAt,
  });

  return upstream.body;
}
