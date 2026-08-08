/**
 * チャット応答ストリーミング (#120 / ADR 0022) の BFF 側サービス。
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
import { serviceHeaders } from "../clients/serviceToken";
import { sendChatMessage, type ChatRequest, type ChatResponse } from "../clients/aiAgentClient";

const encoder = new TextEncoder();

/** SSE の 1 イベント (`data: {...}\n\n`) にエンコードする。 */
export function encodeSseEvent(payload: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(payload)}\n\n`);
}

/** ai-agent の ChatResponse を done イベントの wire 形 (snake_case) に写す。 */
function toDoneEvent(res: ChatResponse): unknown {
  return {
    type: "done",
    response: {
      reply: res.reply,
      requires_approval: res.requiresApproval,
      approval_request_id: res.approvalRequestId,
      citations: res.citations,
    },
  };
}

/** 非ストリーミングの応答を delta + done の合成 SSE ストリームにする。 */
function syntheticStream(res: ChatResponse): ReadableStream<Uint8Array> {
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
        encodeSseEvent({
          type: "done",
          response: {
            reply,
            requires_approval: false,
            approval_request_id: null,
            citations: [],
          },
        }),
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
    console.log("[chatStream] AI_AGENT_BASE_URL not set — using stub stream");
    return stubStream(req);
  }

  const url = `${config.aiAgentBaseUrl}/chat/stream`;
  console.log(`[chatStream] POST ${url}`);

  let upstream: Response | null = null;
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
    console.warn(`[chatStream] upstream fetch failed: ${(err as Error).message}`);
  }

  if (!upstream || !upstream.ok || !upstream.body) {
    // 旧版 ai-agent (エンドポイント未実装 404 等) や一時失敗 → 非ストリーミングで成立させる
    console.warn(
      `[chatStream] upstream not streamable (status=${upstream?.status ?? "n/a"}) — falling back to POST /chat`,
    );
    return syntheticStream(await sendChatMessage(req));
  }

  return upstream.body;
}
