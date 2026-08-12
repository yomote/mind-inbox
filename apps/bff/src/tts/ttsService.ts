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

import { isConfigured, synthesize } from "../clients/voicevoxClient";
import { splitTtsSentences } from "../domain/sentences";
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

export type TtsPrefetchResult = {
  /** "cached" = 1 文以上を合成/ヒット済み / "pending" = 確定文がまだ無い / "stub" = VOICEVOX 未構成 */
  status: "cached" | "pending" | "stub";
  /** 対象になった確定文の数 (観測用) */
  sentences: number;
};

/**
 * ストリーミング中の**途中経過テキスト**を受け取り、確定した文だけを先行合成する (#120)。
 *
 * **文分割の責務は BFF だけが持つ** (PR #132 レビュー対応)。フロントは「今まで届いた
 * テキスト」をそのまま投げるだけで、どこが文の切れ目かを知らない。こうしないと
 * フロントと BFF が同じ分割アルゴリズムを持つ必要があり、別デプロイ単位である両者が
 * ズレた瞬間にキャッシュが全ミスして先行合成が静かに無効化される (ADR 0024 参照)。
 *
 * 末尾の 1 文は書きかけの可能性があるため合成しない — 途中の断片を合成すると
 * 最終合成時にキャッシュに乗らず、無駄な往復になる。
 */
export async function prefetchTts(req: TtsSynthesisRequest): Promise<TtsPrefetchResult> {
  const sentences = splitTtsSentences(req.text);
  // 末尾は「まだ伸びるかもしれない文」。ストリーム終端は最終合成 (synthesizeTts) が拾う。
  const completed = sentences.slice(0, -1);
  if (completed.length === 0) {
    return { status: "pending", sentences: 0 };
  }

  const wavs = await mapWithLimit(completed, SYNTH_CONCURRENCY, (sentence) =>
    synthesizeCached(sentence, req.speakerId),
  );
  if (wavs.some((w) => w === null)) {
    return { status: "stub", sentences: completed.length };
  }
  return { status: "cached", sentences: completed.length };
}

export type TtsPlan =
  /** VOICEVOX 未構成。呼び出し元はブラウザ読み上げへ落とす。 */
  | { status: "stub" }
  /** 読み上げ単位に割った文の並び。結合すると元テキストに戻る。 */
  | { status: "ok"; sentences: string[] };

/**
 * テキストを読み上げ単位の文に割って**返すだけ**で、合成はしない (#185)。
 *
 * ねらい (time-to-first-audio): `synthesizeTts` は全文を結合してから返すため、
 * フロントが最初の音を出せるのは**最後の 1 文が合成し終わってから**だった。
 * フロントがこの並びを受け取って 1 文ずつ `/api/tts` を叩けば、1 文目が合成でき次第
 * 再生を始められる (待ち時間が「全文」から「1 文目」に落ちる)。
 *
 * **分割の責務は BFF に置いたまま** (ADR 0024)。フロントは返ってきた文字列をそのまま
 * 投げ返すだけで、自前では切らない — 両側に分割を持つと片方だけ再デプロイした瞬間に
 * キャッシュキーがズレて先行合成 (prefetchTts) が静かに無効化される。
 */
export function planTts(req: TtsSynthesisRequest): TtsPlan {
  if (!isConfigured()) return { status: "stub" };
  return { status: "ok", sentences: splitTtsSentences(req.text) };
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
