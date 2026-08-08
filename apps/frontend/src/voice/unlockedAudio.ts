/**
 * 自動再生ブロック対策の「解錠済み再生要素」を 1 つだけ持つ (#150)。
 *
 * ブラウザ読み上げ (speechSynthesis) と VOICEVOX の再生 (HTMLAudioElement) は
 * **別々に解錠が要る**。従来は前者しか解錠しておらず、ずんだもんの再生側は
 * 未解錠のままだった。さらに合成には数秒かかるため、再生が始まる頃には
 * タップから離れており、自動再生ポリシーの厳しいブラウザでは弾かれうる。
 *
 * 対策はユーザー操作の中で一度 `play()` を通した要素を使い回すこと。都度
 * `new Audio()` すると、その解錠は引き継がれない。
 *
 * hook の ref ではなくモジュールに置いている理由: 要素は「アプリに 1 つ」で
 * よく、React の状態ではないため。ref に置くと `react-hooks/immutability` が
 * ref 経由の変更 (`audio.src = ...`) を禁止する。
 */

/** 無音 WAV (8kHz mono 8bit / 0.05 秒)。解錠のためだけに鳴らす。 */
const SILENT_WAV_DATA_URI =
  "data:audio/wav;base64,UklGRrQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YZ" +
  "ABAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI" +
  "CAgICA";

let unlocked: HTMLAudioElement | null = null;

/**
 * ユーザー操作 (タップ) の中で呼ぶ。無音 WAV を実際に再生して要素を解錠する。
 * 解錠に失敗しても致命ではない (後段の再生で改めて判定し、劣化として見せる)。
 */
export function unlockAudioElement(): void {
  if (typeof window === "undefined") return;
  try {
    const el = new Audio(SILENT_WAV_DATA_URI);
    void el
      .play()
      .then(() => {
        el.pause();
        el.currentTime = 0;
      })
      .catch(() => {
        // ここで弾かれても後段の再生で判定する。
      });
    unlocked = el;
  } catch {
    // 解錠失敗は致命ではない。
  }
}

/**
 * 再生に使う要素を返す。解錠済みならそれを使い回し、無ければ新規に作る。
 * 呼び出し側はこの要素の `src` / ハンドラを設定して `play()` する。
 */
export function acquireAudioElement(src: string): HTMLAudioElement {
  const el = unlocked ?? new Audio();
  el.src = src;
  return el;
}

/** テスト用: モジュールに残った解錠状態を捨てる。 */
export function resetUnlockedAudioForTest(): void {
  unlocked = null;
}
