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

  it("再生が終わると猶予明けから自動で通す (ユーザーの再操作は不要)", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptFinal(100)).toBe(false);
    gate.setPlaying(false, 1000);

    expect(gate.acceptInterim(1000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 2)).toBe(true);
  });
});

describe("[L1] PlaybackEchoGate — 再生終了直後の尻尾 (猶予)", () => {
  it("再生中に聞こえた発話が再生終了後に final 化されても、猶予内なら破棄される", () => {
    // 無いと: 認識エンジンの final 化は再生停止より数百 ms 遅れるため、
    //         ずんだもんの最後の 1 文だけが毎回入力欄に入る
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false); // 再生中にエンジンが何か聞いた
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS - 1)).toBe(false);
  });

  it("interim を一切経ない final-only のエコー尻尾も猶予内なら破棄される", () => {
    // 無いと: 両エンジンとも interim を経ずに final だけ届く経路があり
    //         (azureSpeech の recognized 単独 / Web Speech の final-only イベント)、
    //         その経路のエコーだけ素通りする (PR #231 Codex P1)
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    // 再生中に interim は一度も届いていない
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1001)).toBe(false);
    expect(gate.acceptInterim(1100)).toBe(false);
  });

  it("猶予を過ぎた final は通す (再生後のユーザー発話を飲み込まない)", () => {
    // 無いと: 一度再生しただけで以降のユーザー発話が永久に破棄される
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    expect(gate.acceptInterim(500)).toBe(false);
    gate.setPlaying(false, 1000);

    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
  });

  it("猶予内に始まったユーザー発話は、猶予後の final で全文届く (配達だけを止めている)", () => {
    // エンジンは止めていないので、猶予内の interim を破棄しても
    // セグメント全文は猶予後の final に入って届く — この性質が
    // 「無条件猶予でもユーザー発話を失わない」根拠 (echoGate.ts の doc 参照)
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    gate.setPlaying(false, 1000);

    expect(gate.acceptInterim(1200)).toBe(false); // 表示だけ抑止される
    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 500)).toBe(true);
  });

  it("再生が再開したら猶予は関係なく閉じ、再終了で猶予が張り直される", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    gate.setPlaying(false, 1000);
    gate.setPlaying(true, 1200); // 次の応答の読み上げが始まった
    expect(gate.acceptFinal(1300)).toBe(false);
    gate.setPlaying(false, 5000);

    expect(gate.acceptFinal(5000 + POST_PLAYBACK_GRACE_MS - 1)).toBe(false);
    expect(gate.acceptFinal(5000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
  });
});

describe("[L1] PlaybackEchoGate — セグメントのリセット", () => {
  it("resetSegment しても再生中は門が閉じたまま", () => {
    // 無いと: 再生中にマイクを入れ直した瞬間からエコーが入力欄に入る
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    gate.resetSegment();

    expect(gate.acceptFinal(600)).toBe(false);
  });

  it("resetSegment しても猶予は保たれる (マイク入れ直しで尻尾が素通りしない)", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    gate.setPlaying(false, 1000);
    gate.resetSegment();

    expect(gate.acceptFinal(1100)).toBe(false); // 猶予内 → まだ破棄
    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
  });

  it("同じ再生状態を重ねて伝えても猶予が延びない (冪等)", () => {
    const gate = new PlaybackEchoGate();
    gate.setPlaying(true, 0);
    gate.setPlaying(false, 1000);
    gate.setPlaying(false, 5000); // 重複通知で graceUntil が動いてはいけない

    expect(gate.acceptFinal(1000 + POST_PLAYBACK_GRACE_MS + 1)).toBe(true);
  });
});
