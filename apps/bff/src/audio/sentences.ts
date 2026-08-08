/**
 * TTS 分割合成 (#120) 用の文分割。
 *
 * BFF (/api/tts の分割合成) とフロント (ストリーミング中の文単位プリフェッチ) の
 * **両方から import され、同一アルゴリズムであることがキャッシュヒットの前提**。
 * フロントは mockApi が domain.ts を import するのと同じ相対 import でこのファイルを
 * 参照する。そのため、この module は Node / ブラウザどちらでも動く純粋関数のみを置く。
 */

const SENTENCE_END = /[。．！？!?\n]+/g;

/** これより短い断片は隣の文に併合する (「はい。」単独合成の不自然さ・往復増を防ぐ)。 */
const MIN_SENTENCE_CHARS = 8;

/**
 * テキストを読み上げ単位の「文」に分割する。区切り文字は文側に残す。
 * 短すぎる断片は直前の文へ併合する。空白のみの断片は捨てる。
 */
export function splitTtsSentences(text: string): string[] {
  const out: string[] = [];
  let lastIndex = 0;

  const pushPiece = (piece: string) => {
    if (!piece.trim()) return;
    const prev = out[out.length - 1];
    if (prev !== undefined && prev.length < MIN_SENTENCE_CHARS) {
      out[out.length - 1] = prev + piece;
      return;
    }
    out.push(piece);
  };

  SENTENCE_END.lastIndex = 0;
  for (let m = SENTENCE_END.exec(text); m !== null; m = SENTENCE_END.exec(text)) {
    pushPiece(text.slice(lastIndex, m.index + m[0].length));
    lastIndex = m.index + m[0].length;
  }
  pushPiece(text.slice(lastIndex));

  // 末尾断片が短い場合は前の文に併合する
  if (out.length >= 2 && out[out.length - 1].length < MIN_SENTENCE_CHARS) {
    const tail = out.pop() as string;
    out[out.length - 1] += tail;
  }

  return out;
}
