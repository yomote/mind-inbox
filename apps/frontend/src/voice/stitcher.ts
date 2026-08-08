/**
 * 認識結果 (final / interim) を入力欄テキストへ縫い合わせる純ロジック (#121)。
 *
 * Web Speech API は無音・時間制限でエンジンが勝手に止まる。止まった時点で
 * final 化されていない interim テキストは、何もしないと**捨てられて発話の
 * 末尾が欠落する**。このクラスは「一区切り (flush)」の時点で未確定 interim を
 * 確定テキストとして commit することで、自動再開の境界を縫い合わせる。
 *
 * エンジン非依存 (Web Speech / Azure Speech SDK のどちらのイベント列でも使う)。
 * React 非依存の純ロジックなので [L1] で単体テストする (stitcher.test.ts)。
 */

export type StitcherCallbacks = {
  /** 確定テキスト。入力欄へ追記される。 */
  onCommit: (text: string) => void;
  /** 認識途中テキストの表示更新。空文字はクリア。 */
  onInterim: (text: string) => void;
};

export class TranscriptStitcher {
  private pendingInterim = "";
  private readonly cb: StitcherCallbacks;

  constructor(cb: StitcherCallbacks) {
    this.cb = cb;
  }

  /** final 結果を受け取った。interim はその final に置き換わったのでクリアする。 */
  handleFinal(text: string): void {
    this.pendingInterim = "";
    this.cb.onInterim("");
    const trimmed = text.trim();
    if (trimmed) this.cb.onCommit(trimmed);
  }

  /** interim (未確定) 結果を受け取った。表示のみ更新し、まだ commit しない。 */
  handleInterim(text: string): void {
    this.pendingInterim = text;
    this.cb.onInterim(text.trim());
  }

  /**
   * エンジンが一区切り止まった (無音タイムアウト / 自動再開の境界 / ユーザー停止)。
   * final 化されなかった interim を捨てずに commit する = 縫い合わせの本体。
   * 冪等 (2 回呼んでも二重 commit しない)。
   */
  flush(): void {
    const trimmed = this.pendingInterim.trim();
    this.pendingInterim = "";
    this.cb.onInterim("");
    if (trimmed) this.cb.onCommit(trimmed);
  }
}
