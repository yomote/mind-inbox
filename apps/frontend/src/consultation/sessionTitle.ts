/**
 * セッションのタイトルを最初の発話から自動生成する。
 *
 * タイトルは開始時に聞かず、内容から後付けする方針 (home.mdx: テーマ入力画面なしで
 * 直接対話開始)。**切り詰め長は BFF の `deriveTitle` と揃える** — mock と実 API で
 * 見出しの長さが変わるのは非対称なので、ここを唯一の接続点として明示する。
 */

/**
 * 切り詰め長。BFF `apps/bff/src/domain/title.ts` の TITLE_MAX_CHARS と同値にすること。
 * 仕様: docs/design/domain_rules.md §2。
 */
export const SESSION_TITLE_MAX = 26;

export function deriveSessionTitle(text: string): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  if (!oneLine) return "新しい相談";
  return oneLine.length > SESSION_TITLE_MAX ? `${oneLine.slice(0, SESSION_TITLE_MAX)}…` : oneLine;
}
