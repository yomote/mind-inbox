/**
 * 相談セッションのタイトル導出 (純粋関数)。
 *
 * 仕様: docs/design/domain_rules.md §2 (現行仕様の明文化 / #259)。
 * フロント側の対 (mock 用の自動リネーム) は
 * `apps/frontend/src/consultation/sessionTitle.ts` — 切り詰め長
 * (`TITLE_MAX_CHARS` = `SESSION_TITLE_MAX`) を両者で揃えること。
 */

/** 切り詰め長 (表示する本文の最大文字数。超過時はこの長さ + 省略記号 1 文字になる)。 */
export const TITLE_MAX_CHARS = 26;

/** 空入力 (テーマ未入力で開始) の既定タイトル。 */
export const DEFAULT_TITLE = "相談セッション";

export function deriveTitle(concern: string): string {
  const trimmed = concern.trim();
  if (trimmed.length === 0) return DEFAULT_TITLE;
  return trimmed.length > TITLE_MAX_CHARS ? `${trimmed.slice(0, TITLE_MAX_CHARS)}…` : trimmed;
}
