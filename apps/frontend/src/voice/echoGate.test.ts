/**
 * [L1] PlaybackEchoGate — TTS 再生中のエコー破棄 (#228)。
 *
 * 無いと何が静かに通るか: TTS 再生中もマイクが生きているため、スピーカーから出た
 * ずんだもんの声が認識され「ユーザーの発話」として入力欄に入る。認識も再生も個々には
 * 正常に動いている (どちらのモジュールのテストも緑のまま) ので、**組み合わせの
 * 振る舞いをここで固定しない限り誰も検知できない** (2026-08-10 PO 実使用で発生)。
 */

import { describe, expect, it } from "vitest";
import { PlaybackEchoGate, POST_PLAYBACK_GRACE_MS } from "./echoGate";

describe("[L1] PlaybackEchoGate — 再生中の破棄", () => {
  it("再生中に届いた interim / final はどちらも破棄される", () => {
    // 無いと: ずんだもんの声がそのまま入力欄に流れる (#228 の実事象)
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);

    expect(gate.acceptInterim(100)).toBe(false);
    expect(gate.acceptFinal(200)).toBe(false);
  });

  it("再生していない間は素通しする", () => {
    // 無いと: 門の導入自体が通常の音声入力を殺す退行になる
    const gate = new PlaybackEchoGate();

    expect(gate.acceptInterim(0)).toBe(true);
    expect(gate.acceptFinal(100)).toBe(true);
  });

  it("再生が終わると自動で通すようになる (ユーザーの再操作は不要)", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptFinal(100)).toBe(false);
    gate.setPlaying(false, 1000);

    expect(gate.acceptInterim(1100)).toBe(true);
    expect(gate.acceptFinal(1200)).toBe(true);
  });
});

describe("[L1] PlaybackEchoGate — 再生終了直後の尻尾 (汚染セグメント)", () => {
  it("再生中に聞こえた発話が再生終了後に final 化されても、猶予内なら破棄される", () => {
    // 無いと: 認識エンジンの final 化は再生停止より数百 ms 遅れるため、
    //         ずんだもんの最後の 1 文だけが毎回入力欄に入る
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false); // 再生中にエンジンが何か聞いた
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS - 1)).toBe(false);
  });

  it("猶予を過ぎた final は通す (再生後のユーザー発話を飲み込まない)", () => {
    // 無いと: 一度エコーを聞いただけで以降のユーザー発話が永久に破棄される
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
  });

  it("汚染セグメントの尻尾を破棄したら、その後の final は通る", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    gate.setPlaying(false, 1000);
    expect(gate.acceptFinal(1100)).toBe(false); // 尻尾を消費

    expect(gate.acceptFinal(1200)).toBe(true); // 新しいセグメント = ユーザー発話
  });

  it("猶予内の interim も破棄されるが、汚染は保持される (次の final を待ち構える)", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    gate.setPlaying(false, 1000);

    expect(gate.acceptInterim(1100)).toBe(false);
    expect(gate.acceptFinal(1200)).toBe(false);
  });

  it("再生中に final まで完結したセグメントは汚染を残さない (終了直後の final は通る)", () => {
    // 無いと: 尻尾の防御が広すぎて、再生直後のユーザー発話まで破棄される
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    expect(gate.acceptFinal(800)).toBe(false); // 再生中に final 化まで済んだ
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1100)).toBe(true);
  });

  it("再生中に何も聞こえなければ、終了直後の final は即通る", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1001)).toBe(true);
  });
});

describe("[L1] PlaybackEchoGate — セグメントのリセット", () => {
  it("resetSegment は汚染を捨てるが、再生中なら門は閉じたまま", () => {
    // 無いと: 再生中にマイクを入れ直した瞬間からエコーが入力欄に入る
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    gate.resetSegment();

    expect(gate.acceptFinal(600)).toBe(false); // まだ再生中 → 破棄
    gate.setPlaying(false, 1000);
    expect(gate.acceptFinal(1001)).toBe(true); // 汚染は消えている → 即通る
  });

  it("同じ再生状態を重ねて伝えても猶予が延びない (冪等)", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    gate.setPlaying(false, 1000);
    gate.setPlaying(false, 5000); // 重複通知で graceUntil が動いてはいけない

    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
  });
});
