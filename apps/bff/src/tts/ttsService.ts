/**
 * TTS の文単位分割合成 + 文キャッシュ (#120)。
 *
 * ねらい (体感レイテンシ):
 *   - 全文を 1 リクエストで合成すると合成時間はテキスト長に比例する。文単位に割って
 *     並行合成すれば、音声を返し始めるまでの壁時計時間が縮む
 *   - さらにフロントはチャット応答の**ストリーミング中に確定した文を先行プリフェッチ**する
 *     (prefetch=true)。LLM 生成時間と TTS 合成時間が重なり、応答完了時にはほぼ合成済みになる
 *
 * キャッシュはインスタンス内メモリ (TTL + 上限)。Consumption のマルチインスタンスでは
 * ヒットしないことがあるが、その場合は合成し直すだけで壊れない (ベストエフォート)。
 */

import { synthesize } from "../clients/voicevoxClient";
import { splitTtsSentences } from "../audio/sentences";
import { concatWavs } from "../audio/wav";

const CACHE_TTL_MS = 5 * 60_000;
const CACHE_MAX_ENTRIES = 64;
/** VOICEVOX engine は CPU バウンドなので並行数は絞る (詰まらせると全文一括より遅くなる)。 */
const SYNTH_CONCURRENCY = 2;

type CacheEntry = { wav: ArrayBuffer; at: number };

const sentenceCache = new Map<string, CacheEntry>();

/** テスト用: キャッシュを空にする。 */
export function resetTtsCache(): void {
  sentenceCache.clear();
}

function cacheKey(speakerId: number | undefined, text: string): string {
  return `${speakerId ?? 3}:${text}`;
}

function cacheGet(key: string): ArrayBuffer | null {
  const entry = sentenceCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.at > CACHE_TTL_MS) {
    sentenceCache.delete(key);
    return null;
  }
  return entry.wav;
}

function cacheSet(key: string, wav: ArrayBuffer): void {
  sentenceCache.set(key, { wav, at: Date.now() });
  while (sentenceCache.size > CACHE_MAX_ENTRIES) {
    const oldest = sentenceCache.keys().next().value;
    if (oldest === undefined) break;
    sentenceCache.delete(oldest);
  }
}

async function synthesizeCached(
  text: string,
  speakerId: number | undefined,
): Promise<ArrayBuffer | null> {
  const key = cacheKey(speakerId, text);
  const hit = cacheGet(key);
  if (hit) return hit;

  const wav = await synthesize({ text, speakerId });
  if (wav === null) return null; // VOICEVOX_BASE_URL 未設定 (stub)
  cacheSet(key, wav);
  return wav;
}

/** 順序を保ったまま並行数を制限して map する。 */
async function mapWithLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await fn(items[i]);
    }
  });
  await Promise.all(workers);
  return results;
}

export type TtsSynthesisRequest = {
  text: string;
  speakerId?: number;
};

/**
 * 文単位プリフェッチ (フロントがストリーミング中に確定文を送ってくる)。
 * 合成してキャッシュに置くだけで音声は返さない。
 *
 * @returns "cached" = 合成済み / "stub" = VOICEVOX 未構成
 */
export async function prefetchTts(req: TtsSynthesisRequest): Promise<"cached" | "stub"> {
  const wav = await synthesizeCached(req.text, req.speakerId);
  return wav === null ? "stub" : "cached";
}

/**
 * テキスト全体を合成して 1 本の WAV を返す。複数文なら文単位 (キャッシュ利用 + 並行) で
 * 合成して結合する。分割合成に失敗した場合は従来どおり全文一括合成へフォールバックする。
 *
 * @returns WAV バイナリ。VOICEVOX 未構成 (stub) の場合は null。
 */
export async function synthesizeTts(req: TtsSynthesisRequest): Promise<ArrayBuffer | null> {
  const sentences = splitTtsSentences(req.text);
  if (sentences.length <= 1) {
    return await synthesizeCached(req.text, req.speakerId);
  }

  try {
    const wavs = await mapWithLimit(sentences, SYNTH_CONCURRENCY, (sentence) =>
      synthesizeCached(sentence, req.speakerId),
    );
    if (wavs.some((w) => w === null)) return null; // stub
    return concatWavs(wavs as ArrayBuffer[]);
  } catch (err) {
    // 分割合成 (結合含む) の失敗で TTS 全体を落とさない — 全文一括で 1 回だけ再試行
    console.warn(
      `[ttsService] split synthesis failed — falling back to single-shot: ${(err as Error).message}`,
    );
    return await synthesize({ text: req.text, speakerId: req.speakerId });
  }
}
