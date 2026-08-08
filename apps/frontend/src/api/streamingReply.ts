/**
 * ストリーミング中の AI 応答 (途中経過テキスト) の外部ストア (#120)。
 *
 * sendMessage (api/consultation.ts) が逐次テキストを書き込み、SessionMessages が
 * useSyncExternalStore で購読して「伸びていくバブル」を描画する。
 *
 * 完了時の消え方: ストアは done では消さない。最終メッセージ (同じ id) が
 * session.messages に追加された時点で SessionMessages 側が id 一致で非表示にする。
 * こうすることで「バブルが消える → 一瞬空白 → 最終メッセージが出る」のちらつきを防ぐ。
 * ストアを空にするのは次のターン開始 (begin) か失敗時 (clear) のみ。
 */

export type StreamingReply = {
  /** 完了時に append される最終 ChatMessage と同一の id */
  id: string;
  text: string;
};

let current: StreamingReply | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeStreamingReply(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getStreamingReply(): StreamingReply | null {
  return current;
}

export function beginStreamingReply(id: string): void {
  current = { id, text: "" };
  emit();
}

export function appendStreamingReply(delta: string): void {
  if (!current || !delta) return;
  current = { ...current, text: current.text + delta };
  emit();
}

/** 失敗時 (フォールバック前) に途中経過を消す。成功時は呼ばない (上記コメント参照)。 */
export function clearStreamingReply(): void {
  if (!current) return;
  current = null;
  emit();
}
