import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { z } from "zod";
import { prefetchTts, synthesizeTts } from "../tts/ttsService";

/**
 * POST /api/tts — テキスト → audio/wav バイナリ。
 *
 * tRPC は JSON シリアライズ前提のため、TTS は別経路で扱う。
 * VOICEVOX_BASE_URL 未設定（stub）時は 204 を返し、
 * フロントは Web SpeechSynthesis にフォールバックする。
 *
 * #120: 合成は文単位の分割 + 並行 + キャッシュ (ttsService)。`prefetch: true` は
 * ストリーミング中のフロントが確定文を先行合成させるための呼び方で、音声は返さず
 * 202 (キャッシュ済み) を返す。
 */

const TtsRequestSchema = z.object({
  text: z.string().min(1),
  speaker: z.number().int().nonnegative().optional(),
  prefetch: z.boolean().optional(),
});

async function ttsHandler(req: HttpRequest, context: InvocationContext): Promise<HttpResponseInit> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return { status: 400, body: "Invalid JSON body" };
  }

  const parsed = TtsRequestSchema.safeParse(body);
  if (!parsed.success) {
    return {
      status: 400,
      body: `Invalid request: ${parsed.error.message}`,
    };
  }

  const { text, speaker, prefetch } = parsed.data;
  context.log(`[tts] text(len)=${text.length} speaker=${speaker ?? 3} prefetch=${Boolean(prefetch)}`);

  try {
    if (prefetch) {
      const result = await prefetchTts({ text, speakerId: speaker });
      // stub (VOICEVOX 未構成) でも 204 で返す — プリフェッチは fire-and-forget なので
      // フロントは結果を使わないが、区別できるようにはしておく。
      return result === "stub" ? { status: 204 } : { status: 202 };
    }

    const audio = await synthesizeTts({ text, speakerId: speaker });

    if (!audio) {
      return { status: 204 };
    }

    return {
      status: 200,
      headers: { "Content-Type": "audio/wav" },
      body: Buffer.from(audio),
    };
  } catch (err) {
    context.error(`[tts] synthesize failed: ${(err as Error).message}`);
    return { status: 502, body: "TTS synthesis failed" };
  }
}

app.http("tts", {
  methods: ["POST"],
  authLevel: "anonymous",
  route: "tts",
  handler: ttsHandler,
});
