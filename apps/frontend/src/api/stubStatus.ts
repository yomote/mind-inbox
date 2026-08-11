/**
 * BFF が stub 応答を返しているかの外部ストア (#146 / dialogue-session.mdx §5.7)。
 *
 * api 層 (consultation / problems) が BFF 応答の `stubbed` フラグを書き込み、
 * `StubResponseBanner` が useSyncExternalStore で購読して警告バナーを出す。
 * `streamingReply.ts` と同じ流儀 (api 層は React を知らないので hook にしない)。
 *
 * 意味論は「**直近に画面へ入った応答が stub かどうか**」:
 *   - stub 応答 (stubbed: true) を受け取ったら立てる
 *   - 実応答 (フラグ無し) を受け取ったら下ろす — BFF が復旧したのにバナーが残り続けない
 *   - mock モード (VITE_USE_MOCK) は BFF を呼ばない自己完結デモで stub とは別物。触らない
 */

let stubbed = false;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeStubbedResponse(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getStubbedResponse(): boolean {
  return stubbed;
}

/** BFF 応答を受け取るたびに api 層が呼ぶ。undefined (旧 BFF / 実応答) は false 扱い。 */
export function reportStubbedResponse(value: boolean | undefined): void {
  const next = value === true;
  if (next === stubbed) return;
  stubbed = next;
  emit();
}

/** ログアウト等で状態を捨てる用。 */
export function resetStubbedResponse(): void {
  reportStubbedResponse(false);
}
