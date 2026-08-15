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
import { z, ZodError } from "zod";
import { appRouter } from "../trpc/router";
import { createContext } from "../trpc/context";
import { MAX_ID_LENGTH, MAX_MESSAGE_LENGTH, MAX_TTS_TEXT_LENGTH } from "../limits";
import { openChatStream } from "../chat/chatStream";
import { planTts, prefetchTts, synthesizeTts } from "../tts/ttsService";
import { warmupDownstreams } from "../warmup/warmupService";
import { summarizeIssues } from "../schemaIssues";
import {
  describeError,
  logErrorEvent,
  logEvent,
  trackRequest,
  type TelemetryFields,
  type TelemetryLogger,
} from "../observability/telemetry";

/**
 * 入口が違っても同じログが出るように、ログ関数だけ差し込めるようにする。
 *
 * 実体はテレメトリの出口 (`observability/telemetry.ts`) と同じ型。
 * Azure Functions 側は `contextLogger(context)` を渡すので、この logger 経由の行は
 * invocation に紐づく (`console.*` だと紐づかない / #307)。
 */
export type HandlerLogger = TelemetryLogger;

const consoleLogger: HandlerLogger = {
  log: (m) => console.log(m),
  error: (m) => console.error(m),
};

/**
 * tRPC の失敗を **値を含まない種別だけ**に正規化する (#413 レビュー P1)。
 *
 * `TRPCError.message` を**そのままテレメトリに出してはいけない**。tRPC の文面は
 * 「機械生成された安全な文字列」ではなく、入力値をそのまま埋め込む経路が 2 つある:
 *
 * - アプリが組み立てる文面 — `problem.get({ id: "会社を辞めたい" })` は
 *   `Problem not found: 会社を辞めたい` になる (router.ts の `requireProblem`)。
 *   `id` は自由文字列なので、相談の本文をそのまま入れられる
 * - zod の入力検証失敗 — `TRPCError.message` は `ZodError` の JSON で、
 *   `invalid_enum_value` などは **受け取った値そのもの**を含む
 *
 * どちらも `errorMessage` という**許可された名前**で出てしまうため、
 * `ALLOWED_FIELDS` (名前しか見ない) の防壁を素通りして Application Insights に
 * 30 日焼き付く。よって出すのは `error.code` と、値を落とした要約だけにする。
 */
export function describeTrpcError(error: { code: string; cause?: unknown }): TelemetryFields {
  const cause = error.cause;
  // zod は「場所 (path) と壊れ方 (code)」だけに落とす — 値は summarizeIssues が捨てる。
  if (cause instanceof ZodError) return { errorType: error.code, reason: summarizeIssues(cause) };
  // それ以外は例外クラス名だけ (クラス名は機械生成 = 入力を含まない)。
  if (cause instanceof Error) return { errorType: error.code, reason: cause.name };
  return { errorType: error.code };
}

/** tRPC の単一エントリポイント (`/api/trpc/*`)。 */
export async function handleTrpc(
  request: Request,
  logger: HandlerLogger = consoleLogger,
): Promise<Response> {
  return await trackRequest(
    "trpc",
    logger,
    async () =>
      await fetchRequestHandler({
        endpoint: "/api/trpc",
        req: request,
        router: appRouter,
        createContext: () => createContext(request),
        onError({ path, error }) {
          // path は手続き名 (`consultation.sendMessage` 等) で入力の中身を含まない。
          // error.message は**出さない** — 理由は describeTrpcError のコメント。
          logErrorEvent("trpc.error", {
            route: path ?? "unknown",
            ...describeTrpcError(error),
          });
        },
      }),
    { method: request.method },
  );
}

// 入力の大きさの上限は `../limits.ts` に集約する (#313 C-1)。tRPC 経路 (router.ts) と
// 非 tRPC 経路 (ここ) で別々の値を持つと、片方だけが青天井のまま残る。
const ChatStreamRequestSchema = z.object({
  sessionId: z.string().min(1).max(MAX_ID_LENGTH),
  message: z.string().min(1).max(MAX_MESSAGE_LENGTH),
});

/** チャット応答の SSE ストリーミング (`POST /api/chat/stream`, #120 / ADR 0024)。 */
export async function handleChatStream(
  request: Request,
  logger: HandlerLogger = consoleLogger,
): Promise<Response> {
  return await trackRequest("chat/stream", logger, async () => {
    const body = await readJson(request);
    if (!body.ok) return INVALID_JSON();

    const parsed = ChatStreamRequestSchema.safeParse(body.value);
    if (!parsed.success) {
      return new Response(`Invalid request: ${parsed.error.message}`, { status: 400 });
    }

    // sessionId は**行にはハッシュ (`sessionHash=`) として出る** — このスキーマは長さしか
    // 見ておらず、クライアントは本文をそのまま sessionId に入れられるため (#413 レビュー指摘)。
    // ハッシュ化は出口 (telemetry.ts の HASHED_FIELDS) が強制するので、ここでは生を渡す。
    // message は**長さだけ** — 本文はテレメトリに載せない (telemetry.ts の不変条件)。
    logEvent("chat.request", {
      sessionId: parsed.data.sessionId,
      chars: parsed.data.message.length,
    });

    try {
      const stream = await openChatStream(parsed.data);
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
      });
    } catch (err) {
      logErrorEvent("chat.failed", { sessionId: parsed.data.sessionId, ...describeError(err) });
      return new Response("chat stream failed", { status: 502 });
    }
  });
}

const TtsRequestSchema = z.object({
  text: z.string().min(1).max(MAX_TTS_TEXT_LENGTH),
  speaker: z.number().int().nonnegative().optional(),
  prefetch: z.boolean().optional(),
  /** #185: 合成せず読み上げ単位の文の並びだけ返す (フロントの逐次再生用)。 */
  plan: z.boolean().optional(),
  /**
   * #242: 読み上げ速度 (VOICEVOX の speedScale)。**optional = 省略で従来どおり等倍**。
   *
   * optional なのは後方互換のため — フロントと BFF は別デプロイ単位なので、
   * 必須にすると BFF を先に出した瞬間に旧フロントの読み上げが 400 で全滅する。
   * 範囲は voicevox-wrapper の `speed_scale` (0.5〜2.0) に合わせる。ここで弾かないと
   * 範囲外は wrapper が 422 を返し、フロントには「合成失敗」としか見えない。
   */
  speedScale: z.number().min(0.5).max(2).optional(),
});

/** VOICEVOX 合成 (`POST /api/tts`)。未構成なら 204 でフロントに縮退を促す。 */
export async function handleTts(
  request: Request,
  logger: HandlerLogger = consoleLogger,
): Promise<Response> {
  return await trackRequest("tts", logger, async () => {
    const body = await readJson(request);
    if (!body.ok) return INVALID_JSON();

    const parsed = TtsRequestSchema.safeParse(body.value);
    if (!parsed.success) {
      return new Response(`Invalid request: ${parsed.error.message}`, { status: 400 });
    }

    const { text, speaker, prefetch, plan, speedScale } = parsed.data;
    // text は読み上げ対象 = AI の応答本文。**長さだけ**残す。
    logEvent("tts.request", {
      chars: text.length,
      speaker: speaker ?? 3,
      prefetch: Boolean(prefetch),
      plan: Boolean(plan),
    });

    try {
      if (plan) {
        // 文分割の真実は BFF が単独で持つ (ADR 0024)。フロントはこの並びをそのまま
        // 1 文ずつ投げ返して逐次再生する (#185)。
        const result = planTts({ text, speakerId: speaker });
        if (result.status === "stub") return new Response(null, { status: 204 });
        logEvent("tts.plan", { count: result.sentences.length });
        return Response.json({ sentences: result.sentences }, { status: 200 });
      }

      if (prefetch) {
        // text は「ストリーミングで今までに届いた途中経過」。どこが文の切れ目かの判断は
        // BFF 側 (prefetchTts) が単独で持つ — フロントに分割ロジックを置かない (ADR 0024)。
        const result = await prefetchTts({ text, speakerId: speaker, speedScale });
        logEvent("tts.prefetch", { status: result.status, count: result.sentences });
        // stub (VOICEVOX 未構成) でも 204 で返す — プリフェッチは fire-and-forget なので
        // フロントは結果を使わないが、区別できるようにはしておく。
        return new Response(null, { status: result.status === "stub" ? 204 : 202 });
      }

      const audio = await synthesizeTts({ text, speakerId: speaker, speedScale });
      if (!audio) return new Response(null, { status: 204 });

      return new Response(audio, { status: 200, headers: { "Content-Type": "audio/wav" } });
    } catch (err) {
      logErrorEvent("tts.failed", describeError(err));
      return new Response("TTS synthesis failed", { status: 502 });
    }
  });
}

/** scale-to-zero の下流を温める (`GET /api/warmup`, #120)。 */
export async function handleWarmup(logger: HandlerLogger = consoleLogger): Promise<Response> {
  return await trackRequest("warmup", logger, async () => {
    const result = await warmupDownstreams();
    logEvent("warmup.result", {
      target: "ai-agent",
      status: result.aiAgent.status,
      ms: result.aiAgent.ms,
    });
    logEvent("warmup.result", {
      target: "voicevox",
      status: result.voicevox.status,
      ms: result.voicevox.ms,
    });
    return Response.json(result, { status: 200 });
  });
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
