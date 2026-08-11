/**
 * セッションのタイトルを最初の発話から自動生成する。
 *
 * タイトルは開始時に聞かず、内容から後付けする方針 (home.mdx: テーマ入力画面なしで
 * 直接対話開始)。**切り詰め長は BFF の `deriveTitle` と揃える** — mock と実 API で
 * 見出しの長さが変わるのは非対称なので、ここを唯一の接続点として明示する。
 * 仕様: docs/design/domain_rules.md §2 (切り詰め位置は 2026-08-11 PO 裁定
 * 「文字境界で安全に切る」で確定)。
 */

/**
 * 切り詰め長 (本文の最大**書記素 (grapheme)** 数。絵文字・結合文字も見た目の 1 文字 = 1)。
 * BFF `apps/bff/src/domain/title.ts` の TITLE_MAX_CHARS と同値にすること。
 */
export const SESSION_TITLE_MAX = 26;

/**
 * 書記素 (grapheme) 単位の分割器。実装の選択は BFF `domain/title.ts` と同じ:
 * コードポイント境界ではサロゲートペアの分断しか防げず ZWJ 結合絵文字・結合文字が
 * 千切れるため、標準 API の Intl.Segmenter で書記素境界に切る (依存追加なし)。
 */
const graphemeSegmenter = new Intl.Segmenter("ja", { granularity: "grapheme" });

/** 先頭 max 書記素で切った文字列を返す (max 書記素以下ならそのまま)。 */
function sliceGraphemes(text: string, max: number): string {
  let count = 0;
  for (const segment of graphemeSegmenter.segment(text)) {
    if (count === max) return text.slice(0, segment.index);
    count += 1;
  }
  return text;
}

export function deriveSessionTitle(text: string): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  if (!oneLine) return "新しい相談";
  const sliced = sliceGraphemes(oneLine, SESSION_TITLE_MAX);
  return sliced === oneLine ? oneLine : `${sliced}…`;
}
