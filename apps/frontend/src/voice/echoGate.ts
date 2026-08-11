/**
 * TTS 再生中のエコー破棄 (#228): スピーカーから出た合成音声 (ずんだもん / ブラウザ
 * 読み上げ) を音声認識が拾い、ユーザー発話として入力欄へ入ってしまうのを堰き止める門。
 *
 * **エンジンを止めずに結果を破棄する**方式を取る。pause/resume でエンジン自体を
 * 止めない理由:
 *   - Azure Speech の再開は「トークン + SDK + マイク open」の非同期列で、TTS 終了
 *     時点にはユーザージェスチャが無い。iOS Safari はジェスチャ外のマイク open を
 *     許可済みでも実質無効にする (#186 と同じ罠) — 再開が**静かに失敗**する
 *   - Web Speech は stop/start の境界で interim を落とす (#121 で縫い合わせた
 *     末尾欠落が再開のたびに再発する)
 *
 * 判定は 2 段:
 *   - `playing`: TTS が音を出している間 true。この間に届いた interim / final は破棄
 *   - 再生終了後の猶予 (grace): 認識エンジンの final 化は再生停止より数百 ms 遅れて
 *     届くため、終了直後に届く結果は合成音声の尻尾でありうる。**エンジンが尻尾を
 *     持っているかどうかは interim の有無からは判定できない** — 両エンジンとも
 *     interim を経ずに final だけ届く経路がある (azureSpeech の recognized 単独 /
 *     Web Speech の final-only イベント。PR #231 Codex P1)。よって再生終了時は
 *     **無条件に**猶予を張り、猶予内の結果はすべて破棄する
 *
 * エンジンは止めていないので、猶予内に**始まった**ユーザー発話は失われない —
 * 破棄されるのは配達だけで、猶予後に届く final にはセグメント全文が入っている。
 * 飲み込むのは「猶予内に final 化まで完了する短い発話」のみ (エコー混入より安全側)。
 *
 * どの認識エンジン (Azure / Web Speech) × どの読み上げ経路 (VOICEVOX / ブラウザ
 * 読み上げ) の組み合わせでも、stitcher の手前 (useVoiceInput の配達口) で同じに効く。
 *
 * React 非依存の純ロジック。時刻は引数で受け取り [L1] で単体テストする (echoGate.test.ts)。
 */

/**
 * 再生終了後、結果を破棄し続ける猶予。
 * 長すぎると再生直後のユーザー発話 (猶予内に final 化まで完了する短いもの) を飲み込み、
 * 短すぎるとエコーの尻尾が入力欄に入る。
 * 認識エンジンの final 化遅延 (実測で数百 ms 台) に余裕を持たせた値。
 */
export const POST_PLAYBACK_GRACE_MS = 1500;

export class PlaybackEchoGate {
  private playing = false;
  private graceUntil = Number.NEGATIVE_INFINITY;

  /** TTS の再生開始 / 終了を伝える。`now` はミリ秒時刻 (Date.now())。 */
  setPlaying(playing: boolean, now: number): void {
    if (playing === this.playing) return;
    this.playing = playing;
    if (!playing) {
      // 尻尾の有無は判定できない (final-only の経路がある) ので、無条件に張る。
      this.graceUntil = now + POST_PLAYBACK_GRACE_MS;
    }
  }

  private accept(now: number): boolean {
    if (this.playing) return false;
    return now > this.graceUntil;
  }

  /** interim (未確定) 結果を通してよいか。false なら破棄する。 */
  acceptInterim(now: number): boolean {
    return this.accept(now);
  }

  /** final (確定) 結果を通してよいか。false なら破棄する。 */
  acceptFinal(now: number): boolean {
    return this.accept(now);
  }

  /**
   * 認識エンジンを止めた (追跡中のセグメントごと消えた)。
   * 再生状態と猶予は保つ — 再生中や猶予中にマイクを入れ直しても、スピーカーの音が
   * 消えたわけではないので門は閉じたままにする。
   */
  resetSegment(): void {
    // 破棄すべき内部セグメント状態は現設計では持たない (猶予は時刻だけで決まる)。
    // 呼び出し口は残す — エンジン再起動と門の関係をここで一元的に説明するため。
  }
}
