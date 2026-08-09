import type { HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import type { HandlerLogger } from "./handlers";

/**
 * Azure Functions の型 ↔ Web 標準 Request/Response の変換だけを担う層。
 *
 * ここに**分岐やビジネス判断を置かないこと**。status / ヘッダ / 縮退の判断は
 * `handlers.ts` が単独で持つ (入口が 2 つあるため / handlers.ts の冒頭コメント参照)。
 */

export function toFetchRequest(req: HttpRequest): Promise<Request> {
  const headers = new Headers();
  for (const [key, value] of req.headers.entries()) {
    headers.set(key, value);
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  return (async () =>
    new Request(req.url, {
      method: req.method,
      headers,
      body: hasBody ? await req.text() : undefined,
    }))();
}

/**
 * Response → HttpResponseInit。
 *
 * body の運び方を content-type で変える必要がある:
 *   - SSE は **ストリームのまま**渡す (text 化すると逐次配信が死ぬ / #120)
 *   - 音声は Buffer で渡す (text 化するとバイト列が壊れる)
 *   - それ以外は text
 */
export async function toHttpResponseInit(response: Response): Promise<HttpResponseInit> {
  const headers = Object.fromEntries(response.headers.entries());
  const contentType = response.headers.get("content-type") ?? "";

  if (!response.body) {
    return { status: response.status, headers };
  }

  if (contentType.includes("text/event-stream")) {
    return { status: response.status, headers, body: response.body };
  }

  if (contentType.startsWith("audio/")) {
    return { status: response.status, headers, body: Buffer.from(await response.arrayBuffer()) };
  }

  return { status: response.status, headers, body: await response.text() };
}

/** InvocationContext を handlers.ts が使うロガー形に合わせる。 */
export function contextLogger(context: InvocationContext): HandlerLogger {
  return {
    log: (message) => context.log(message),
    error: (message) => context.error(message),
  };
}
