import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { z } from "zod";
import { openChatStream } from "../chat/chatStream";

/**
 * POST /api/chat/stream — チャット応答の SSE ストリーミング (#120 / ADR 0022)。
 *
 * tRPC は request/response 前提でトークン逐次配信ができないため、/api/tts と同じ
 * 「非 tRPC の別経路」パターンで置く。イベント契約 (delta/done/error) の真実は
 * ai-agent の pydantic。BFF は SSE バイトを素通しするだけ。
 *
 * Azure Functions の応答ストリーミングは enableHttpStream で有効化する
 * (host 4.28+ / @azure/functions 4.3+)。未対応ホストでは body がバッファされて
 * 一括到着するだけで、機能自体は壊れない (全文一括表示 = 従来挙動)。
 */
app.setup({ enableHttpStream: true });

const ChatStreamRequestSchema = z.object({
  sessionId: z.string().min(1),
  message: z.string().min(1),
});

async function chatStreamHandler(
  req: HttpRequest,
  context: InvocationContext,
): Promise<HttpResponseInit> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return { status: 400, body: "Invalid JSON body" };
  }

  const parsed = ChatStreamRequestSchema.safeParse(body);
  if (!parsed.success) {
    return { status: 400, body: `Invalid request: ${parsed.error.message}` };
  }

  context.log(`[chatStream] sessionId=${parsed.data.sessionId}`);

  try {
    const stream = await openChatStream(parsed.data);
    return {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
      body: stream,
    };
  } catch (err) {
    context.error(`[chatStream] failed: ${(err as Error).message}`);
    return { status: 502, body: "chat stream failed" };
  }
}

app.http("chatStream", {
  methods: ["POST"],
  authLevel: "anonymous",
  route: "chat/stream",
  handler: chatStreamHandler,
});
