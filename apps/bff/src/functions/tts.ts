import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { handleTts } from "../http/handlers";
import { contextLogger, toFetchRequest, toHttpResponseInit } from "../http/azureAdapter";

/**
 * POST /api/tts — テキスト → audio/wav バイナリ。
 *
 * tRPC は JSON シリアライズ前提のため、TTS は別経路で扱う。
 * VOICEVOX_BASE_URL 未設定（stub）時は 204 を返し、
 * フロントは Web SpeechSynthesis にフォールバックする。
 *
 * #120: 合成は文単位の分割 + 並行 + キャッシュ (ttsService)。`prefetch: true` は
 * ストリーミング中のフロントが確定文を先行合成させるための呼び方で、音声は返さず
 * 202 (キャッシュ済み) を返す。status の決定は http/handlers.ts に集約している。
 */
async function ttsHandler(req: HttpRequest, context: InvocationContext): Promise<HttpResponseInit> {
  const response = await handleTts(await toFetchRequest(req), contextLogger(context));
  return await toHttpResponseInit(response);
}

app.http("tts", {
  methods: ["POST"],
  authLevel: "anonymous",
  route: "tts",
  handler: ttsHandler,
});
