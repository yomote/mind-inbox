/**
 * BFF の HTTP ハンドラ本体 — **Web 標準の Request/Response だけで書く**。
 *
 * なぜ Azure Functions のシグネチャで書かないか:
 *
 * BFF には入口が 2 つある。本番の Azure Functions (`src/functions/*.ts`) と、
 * ローカル / E2E 用の素の node サーバ (`scripts/local-server.mjs`) で、
 * 「同じ URL に同じものを投げたら同じ応答が返る」ことが前提になっている。
 * 入口ごとに status やヘッダを書くと、**片方だけ直したときに静かにズレる** —
 * TTS のプリフェッチが 202 を返すようになったのに L4 が 200 を期待し続けて
 * 落ちた事故 (2026-08-08) と同じクラスの問題を、構造で潰しておく。
 *
 * よってルーティングと status/ヘッダの決定はすべてここに集約し、入口側は
 * 「フレームワークの型 ↔ Web 標準型」の変換しかしない。
 */

import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { z } from "zod";
import { appRouter } from "../trpc/router";
import { createContext } from "../trpc/context";
import { openChatStream } from "../chat/chatStream";
import { prefetchTts, synthesizeTts } from "../tts/ttsService";
import { warmupDownstreams } from "../warmup/warmupService";

/** 入口が違っても同じログが出るように、ログ関数だけ差し込めるようにする。 */
export type HandlerLogger = {
  log: (message: string) => void;
  error: (message: string) => void;
};

const consoleLogger: HandlerLogger = {
  log: (m) => console.log(m),
  error: (m) => console.error(m),
};

/** tRPC の単一エントリポイント (`/api/trpc/*`)。 */
export async function handleTrpc(
  request: Request,
  logger: HandlerLogger = consoleLogger,
): Promise<Response> {
  return await fetchRequestHandler({
    endpoint: "/api/trpc",
    req: request,
    router: appRouter,
    createContext: () => createContext(request),
    onError({ path, error }) {
      logger.error(`[tRPC error] path=${path ?? "unknown"} message=${error.message}`);
    },
  });
}

const ChatStreamRequestSchema = z.object({
  sessionId: z.string().min(1),
  message: z.string().min(1),
});

/** チャット応答の SSE ストリーミング (`POST /api/chat/stream`, #120 / ADR 0024)。 */
export async function handleChatStream(
  request: Request,
  logger: HandlerLogger = consoleLogger,
): Promise<Response> {
  const body = await readJson(request);
  if (!body.ok) return INVALID_JSON();

  const parsed = ChatStreamRequestSchema.safeParse(body.value);
  if (!parsed.success) {
    return new Response(`Invalid request: ${parsed.error.message}`, { status: 400 });
  }

  logger.log(`[chatStream] sessionId=${parsed.data.sessionId}`);

  try {
    const stream = await openChatStream(parsed.data);
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
    });
  } catch (err) {
    logger.error(`[chatStream] failed: ${(err as Error).message}`);
    return new Response("chat stream failed", { status: 502 });
  }
}

const TtsRequestSchema = z.object({
  text: z.string().min(1),
  speaker: z.number().int().nonnegative().optional(),
  prefetch: z.boolean().optional(),
});

/** VOICEVOX 合成 (`POST /api/tts`)。未構成なら 204 でフロントに縮退を促す。 */
export async function handleTts(
  request: Request,
  logger: HandlerLogger = consoleLogger,
): Promise<Response> {
  const body = await readJson(request);
  if (!body.ok) return INVALID_JSON();

  const parsed = TtsRequestSchema.safeParse(body.value);
  if (!parsed.success) {
    return new Response(`Invalid request: ${parsed.error.message}`, { status: 400 });
  }

  const { text, speaker, prefetch } = parsed.data;
  logger.log(
    `[tts] text(len)=${text.length} speaker=${speaker ?? 3} prefetch=${Boolean(prefetch)}`,
  );

  try {
    if (prefetch) {
      // text は「ストリーミングで今までに届いた途中経過」。どこが文の切れ目かの判断は
      // BFF 側 (prefetchTts) が単独で持つ — フロントに分割ロジックを置かない (ADR 0024)。
      const result = await prefetchTts({ text, speakerId: speaker });
      logger.log(`[tts] prefetch status=${result.status} sentences=${result.sentences}`);
      // stub (VOICEVOX 未構成) でも 204 で返す — プリフェッチは fire-and-forget なので
      // フロントは結果を使わないが、区別できるようにはしておく。
      return new Response(null, { status: result.status === "stub" ? 204 : 202 });
    }

    const audio = await synthesizeTts({ text, speakerId: speaker });
    if (!audio) return new Response(null, { status: 204 });

    return new Response(audio, { status: 200, headers: { "Content-Type": "audio/wav" } });
  } catch (err) {
    logger.error(`[tts] synthesize failed: ${(err as Error).message}`);
    return new Response("TTS synthesis failed", { status: 502 });
  }
}

/** scale-to-zero の下流を温める (`GET /api/warmup`, #120)。 */
export async function handleWarmup(logger: HandlerLogger = consoleLogger): Promise<Response> {
  const result = await warmupDownstreams();
  logger.log(
    `[warmup] aiAgent=${result.aiAgent.status}(${result.aiAgent.ms}ms) ` +
      `voicevox=${result.voicevox.status}(${result.voicevox.ms}ms)`,
  );
  return Response.json(result, { status: 200 });
}

/**
 * 本文の JSON パース。**パース失敗とスキーマ違反を区別する**。
 *
 * 両方を zod の汎用エラーに潰すと「body が壊れている」と「フィールドが足りない」が
 * 同じメッセージになり、切り分けの手掛かりが消える。
 */
type JsonBody = { ok: true; value: unknown } | { ok: false };

async function readJson(request: Request): Promise<JsonBody> {
  try {
    return { ok: true, value: await request.json() };
  } catch {
    return { ok: false };
  }
}

const INVALID_JSON = () => new Response("Invalid JSON body", { status: 400 });
