/**
 * [L1] TranscriptStitcher — 認識再開境界の縫い合わせ純ロジック (#121)。
 *
 * L1 で書く理由: 「エンジンが勝手に止まった瞬間の interim の扱い」はブラウザ実機で
 * しか再現できないイベント列であり、L2/L3 では機械的に検証できない。純ロジックに
 * 切り出してここで固定する (docs/testing/strategy.md §1.2 の「書く判断」)。
 */

import { describe, expect, it, vi } from "vitest";
import { TranscriptStitcher } from "./stitcher";

function makeStitcher() {
  const onCommit = vi.fn();
  const onInterim = vi.fn();
  return { stitcher: new TranscriptStitcher({ onCommit, onInterim }), onCommit, onInterim };
}

describe("[L1] TranscriptStitcher", () => {
  it("final を commit し interim 表示をクリアする", () => {
    // 無いと: final が入力欄に積まれない / 確定後も interim 表示が残る退行が静かに通る
    const { stitcher, onCommit, onInterim } = makeStitcher();
    stitcher.handleInterim("転職しようか");
    stitcher.handleFinal("転職しようか迷っている");

    expect(onCommit).toHaveBeenCalledExactlyOnceWith("転職しようか迷っている");
    expect(onInterim).toHaveBeenLastCalledWith("");
  });

  it("interim は表示のみで commit しない", () => {
    // 無いと: 未確定テキストが入力欄へ確定として混入する退行が静かに通る
    const { stitcher, onCommit, onInterim } = makeStitcher();
    stitcher.handleInterim("しごとが");

    expect(onInterim).toHaveBeenCalledWith("しごとが");
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("flush は final 化されなかった interim を commit する (再開境界の縫い合わせ)", () => {
    // 無いと: 無音タイムアウトでエンジンが止まるたびに interim が捨てられ、
    // 1〜2 分の発話の末尾が毎回静かに欠落する (#121 の核心)
    const { stitcher, onCommit } = makeStitcher();
    stitcher.handleInterim("やっぱり相談したくて");
    stitcher.flush();

    expect(onCommit).toHaveBeenCalledExactlyOnceWith("やっぱり相談したくて");
  });

  it("final 直後の flush は二重 commit しない (冪等)", () => {
    // 無いと: onend とユーザー停止の両方で flush され同じ発話が 2 回積まれる退行が静かに通る
    const { stitcher, onCommit } = makeStitcher();
    stitcher.handleInterim("迷っている");
    stitcher.handleFinal("迷っている");
    stitcher.flush();
    stitcher.flush();

    expect(onCommit).toHaveBeenCalledExactlyOnceWith("迷っている");
  });

  it("空白のみの interim / final は commit しない", () => {
    // 無いと: 空行が入力欄へ積まれてメッセージが汚れる退行が静かに通る
    const { stitcher, onCommit } = makeStitcher();
    stitcher.handleInterim("   ");
    stitcher.flush();
    stitcher.handleFinal("  ");

    expect(onCommit).not.toHaveBeenCalled();
  });
});
