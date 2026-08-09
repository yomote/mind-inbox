import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { handleChatStream } from "../http/handlers";
import { contextLogger, toFetchRequest, toHttpResponseInit } from "../http/azureAdapter";

/**
 * POST /api/chat/stream — チャット応答の SSE ストリーミング (#120 / ADR 0024)。
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

async function chatStreamHandler(
  req: HttpRequest,
  context: InvocationContext,
): Promise<HttpResponseInit> {
  const response = await handleChatStream(await toFetchRequest(req), contextLogger(context));
  return await toHttpResponseInit(response);
}

app.http("chatStream", {
  methods: ["POST"],
  authLevel: "anonymous",
  route: "chat/stream",
  handler: chatStreamHandler,
});
