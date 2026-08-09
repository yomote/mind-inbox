import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { handleTrpc } from "../http/handlers";
import { contextLogger, toFetchRequest, toHttpResponseInit } from "../http/azureAdapter";

/**
 * Azure Functions v4 — tRPC の単一 HTTP エントリポイント。
 * /api/trpc/{trpcPath} に対するすべてのリクエストを tRPC に委譲する。
 *
 * 応答の中身は `http/handlers.ts` が決める (ローカル配信サーバと共有するため)。
 * ここは Azure Functions の型との変換だけを行う。
 */
async function trpcHandler(
  req: HttpRequest,
  context: InvocationContext,
): Promise<HttpResponseInit> {
  context.log(`[trpcHandler] ${req.method} ${req.url}`);

  const response = await handleTrpc(await toFetchRequest(req), contextLogger(context));
  return await toHttpResponseInit(response);
}

app.http("trpc", {
  methods: ["GET", "POST"],
  authLevel: "anonymous",
  route: "trpc/{*trpcPath}",
  handler: trpcHandler,
});
