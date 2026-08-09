#!/usr/bin/env node
/**
 * BFF をローカルで配信する素の node サーバ (Azure Functions Core Tools 不要)。
 *
 * 用途は 2 つ:
 *   1. ローカル開発 — `func start` を入れずにフロントから実 BFF を叩く
 *   2. **UC 受け入れ E2E** (`apps/frontend/e2e-uc`) のバックエンド
 *
 * ルーティングと応答の中身は本番の Functions と**同じ `src/http/handlers.ts`** を呼ぶ。
 * ここが持つのは「node の http ↔ Web 標準 Request/Response」の変換と CORS だけ。
 *
 * 事前に `npm run build` が必要 (dist を読む)。
 *
 * env:
 *   PORT              — 待ち受けポート (既定 7071 / Functions Core Tools と同じ)
 *   AI_AGENT_BASE_URL — 未設定なら BFF の stub 応答になる
 *   VOICEVOX_BASE_URL — 未設定なら /api/tts は 204 (フロントはブラウザ読み上げへ縮退)
 */

import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const { handleChatStream, handleTrpc, handleTts, handleWarmup } = await import(
  path.join(here, "../dist/src/http/handlers.js")
).catch((err) => {
  console.error(
    "[local-server] dist を読めませんでした。先に `npm run build` を実行してください。\n" + err,
  );
  process.exit(1);
});

const PORT = Number(process.env.PORT || 7071);

/**
 * ローカルではフロント (vite preview) が別ポートに居るため CORS を開ける。
 * 本番は SWA→Functions の直叩きで、門は EasyAuth 側にある (#69) — ここは
 * ローカル配信専用の緩和であって、本番の設定を模していない。
 */
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "content-type,authorization",
};

/** node の IncomingMessage → Web 標準 Request。 */
async function toRequest(req, origin) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  return new Request(new URL(req.url, origin), {
    method: req.method,
    headers: Object.entries(req.headers).flatMap(([k, v]) =>
      v === undefined ? [] : [[k, Array.isArray(v) ? v.join(",") : v]],
    ),
    body: hasBody && chunks.length > 0 ? Buffer.concat(chunks) : undefined,
  });
}

/** Web 標準 Response → node の ServerResponse。SSE はストリームのまま流す。 */
async function writeResponse(res, response) {
  const headers = { ...CORS_HEADERS, ...Object.fromEntries(response.headers.entries()) };
  res.writeHead(response.status, headers);

  if (!response.body) {
    res.end();
    return;
  }

  // SSE は逐次配信できないと意味がない (#120) ので、reader で流し込む。
  const reader = response.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    res.write(Buffer.from(value));
  }
  res.end();
}

const server = createServer((req, res) => {
  void (async () => {
    const origin = `http://127.0.0.1:${PORT}`;

    if (req.method === "OPTIONS") {
      res.writeHead(204, CORS_HEADERS);
      res.end();
      return;
    }

    const { pathname } = new URL(req.url, origin);

    try {
      if (pathname.startsWith("/api/trpc")) {
        await writeResponse(res, await handleTrpc(await toRequest(req, origin)));
        return;
      }
      if (pathname === "/api/chat/stream") {
        await writeResponse(res, await handleChatStream(await toRequest(req, origin)));
        return;
      }
      if (pathname === "/api/tts") {
        await writeResponse(res, await handleTts(await toRequest(req, origin)));
        return;
      }
      if (pathname === "/api/warmup") {
        await writeResponse(res, await handleWarmup());
        return;
      }

      res.writeHead(404, CORS_HEADERS);
      res.end("not found");
    } catch (err) {
      // 落ちても Playwright の webServer を道連れにしない (次のテストが読める形で失敗する)。
      console.error(`[local-server] ${req.method} ${pathname} failed:`, err);
      if (!res.headersSent) res.writeHead(500, CORS_HEADERS);
      res.end("internal error");
    }
  })();
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[local-server] BFF listening on http://127.0.0.1:${PORT}`);
  console.log(`[local-server] AI_AGENT_BASE_URL=${process.env.AI_AGENT_BASE_URL ?? "(stub)"}`);
  console.log(`[local-server] VOICEVOX_BASE_URL=${process.env.VOICEVOX_BASE_URL ?? "(stub/204)"}`);
});
