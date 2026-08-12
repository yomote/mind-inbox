/**
 * 相談セッションのタイトル導出 (純粋関数)。
 *
 * 仕様: docs/design/domain_rules.md §2 (現行仕様の明文化 / #259。
 * 切り詰め位置は 2026-08-11 PO 裁定「文字境界で安全に切る」で確定)。
 * フロント側の対 (mock 用の自動リネーム) は
 * `apps/frontend/src/consultation/sessionTitle.ts` — 切り詰め長
 * (`TITLE_MAX_CHARS` = `SESSION_TITLE_MAX`) を両者で揃えること。
 */

/**
 * 切り詰め長 (表示する本文の最大**書記素 (grapheme)** 数。超過時はこの長さ + 省略記号 1 文字になる)。
 * 「文字」は見た目の 1 文字 = 書記素で数える (絵文字・結合文字も 1)。
 */
export const TITLE_MAX_CHARS = 26;

/** 空入力 (テーマ未入力で開始) の既定タイトル。 */
export const DEFAULT_TITLE = "相談セッション";

/**
 * 書記素 (grapheme) 単位の分割器。
 *
 * 実装の選択 (2026-08-11 PO 裁定「文字境界で安全に切る」の実現手段):
 * コードポイント境界 (`[...str]`) はサロゲートペアの分断 (「�」表示) だけを防ぎ、
 * ZWJ 結合絵文字 (👩‍👩‍👧‍👦) や結合文字 (か + 結合濁点) は依然として途中で千切れる。
 * `Intl.Segmenter` は Node / 全モダンブラウザ標準で依存追加なし・入力は短文なので
 * コスト差も無視でき、複雑さの増分に対して「見た目の 1 文字を壊さない」効果が
 * 完全になる書記素分割を採る。
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

export function deriveTitle(concern: string): string {
  const trimmed = concern.trim();
  if (trimmed.length === 0) return DEFAULT_TITLE;
  const sliced = sliceGraphemes(trimmed, TITLE_MAX_CHARS);
  // sliceGraphemes は超過時のみ短い文字列を返すので、同一なら切り詰め不要。
  return sliced === trimmed ? trimmed : `${sliced}…`;
}
