// PR レビュー Routine の自動Resolveループ動作確認用デモ。使い捨て。
export function lastItem<T>(arr: T[]): T | undefined {
  return arr[arr.length - 1];
}
