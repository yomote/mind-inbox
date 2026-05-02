import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { appRouter } from "../trpc/router";
import { createContext } from "../trpc/context";

/**
 * Azure Functions v4 — tRPC の単一 HTTP エントリポイント。
 * /api/trpc/{trpcPath} に対するすべてのリクエストを tRPC に委譲する。
 *
 * Azure Functions の HttpRequest は web 標準の Request と互換ではないため、
 * tRPC fetch アダプタが期待する Request オブジェクトに変換する。
 */
async function toFetchRequest(req: HttpRequest): Promise<Request> {
  const headers = new Headers();
  for (const [key, value] of req.headers.entries()) {
    headers.set(key, value);
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const body = hasBody ? await req.text() : undefined;

  return new Request(req.url, {
    method: req.method,
    headers,
    body,
  });
}

async function trpcHandler(
  req: HttpRequest,
  context: InvocationContext,
): Promise<HttpResponseInit> {
  context.log(`[trpcHandler] ${req.method} ${req.url}`);

  const fetchReq = await toFetchRequest(req);

  const response = await fetchRequestHandler({
    endpoint: "/api/trpc",
    req: fetchReq,
    router: appRouter,
    createContext: () => createContext(req),
    onError({ path, error }) {
      context.error(`[tRPC error] path=${path ?? "unknown"} message=${error.message}`);
    },
  });

  const body = await response.text();

  return {
    status: response.status,
    headers: Object.fromEntries(response.headers.entries()),
    body,
  };
}

app.http("trpc", {
  methods: ["GET", "POST"],
  authLevel: "anonymous",
  route: "trpc/{*trpcPath}",
  handler: trpcHandler,
});
