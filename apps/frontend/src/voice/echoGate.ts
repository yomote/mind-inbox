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
 * 状態は 2 つ:
 *   - `playing`: TTS が音を出している間 true。この間に届いた interim / final は破棄
 *   - `dirty`: 再生中に interim が届いた (= エンジンが合成音声を聞いてしまった) のに
 *     まだ final 化されていない状態。再生終了の**直後**に届く final はこの汚染
 *     セグメントの尻尾なので、grace 時間内なら破棄する (認識エンジンの final 化は
 *     再生停止より数百 ms 遅れて届く縁があるため)
 *
 * どの認識エンジン (Azure / Web Speech) × どの読み上げ経路 (VOICEVOX / ブラウザ
 * 読み上げ) の組み合わせでも、stitcher の手前 (useVoiceInput の配達口) で同じに効く。
 *
 * React 非依存の純ロジック。時刻は引数で受け取り [L1] で単体テストする (echoGate.test.ts)。
 */

/**
 * 再生終了後、汚染セグメントの final を破棄し続ける猶予。
 * 長すぎると再生直後のユーザー発話を飲み込み、短すぎるとエコーの尻尾が入力欄に入る。
 * 認識エンジンの final 化遅延 (実測で数百 ms 台) に余裕を持たせた値。
 */
export const POST_PLAYBACK_GRACE_MS = 1500;

export class PlaybackEchoGate {
  private playing = false;
  private dirty = false;
  private graceUntil = 0;

  /** TTS の再生開始 / 終了を伝える。`now` はミリ秒時刻 (Date.now())。 */
  setPlaying(playing: boolean, now: number): void {
    if (playing === this.playing) return;
    this.playing = playing;
    if (!playing && this.dirty) {
      // 再生中に聞こえた発話がまだ final 化されていない → 尻尾を待ち構える。
      this.graceUntil = now + POST_PLAYBACK_GRACE_MS;
    }
  }

  /** interim (未確定) 結果を通してよいか。false なら破棄する。 */
  acceptInterim(now: number): boolean {
    if (this.playing) {
      this.dirty = true;
      return false;
    }
    if (this.dirty) {
      if (now <= this.graceUntil) return false;
      // 猶予を過ぎた = もう合成音声の尻尾ではない。ユーザー発話を飲み込まない。
      this.dirty = false;
    }
    return true;
  }

  /** final (確定) 結果を通してよいか。false なら破棄する。 */
  acceptFinal(now: number): boolean {
    if (this.playing) {
      // 再生中に final まで完結した = 汚染セグメントはここで消費された。
      this.dirty = false;
      return false;
    }
    if (this.dirty) {
      this.dirty = false;
      return now > this.graceUntil;
    }
    return true;
  }

  /**
   * 認識エンジンを止めた (追跡中のセグメントごと消えた)。
   * 再生状態は保つ — 再生中にマイクを入れ直しても門は閉じたままにする。
   */
  resetSegment(): void {
    this.dirty = false;
    this.graceUntil = 0;
  }
}
