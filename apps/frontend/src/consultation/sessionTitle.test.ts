/**
 * [L1] セッションタイトルの自動生成。
 *
 * 無いと何が静かに通るか: 切り詰め長が BFF (`deriveTitle`) とズレても画面は出るので、
 * mock と実 API で見出しの長さが変わる非対称に誰も気づかない (2026-08 の調査で
 * mock 18 文字 / BFF 26 文字の食い違いが実際に発見された。PO 判断で 26 に統一)。
 */

import { describe, expect, it } from "vitest";
import { SESSION_TITLE_MAX, deriveSessionTitle } from "./sessionTitle";

describe("[L1] deriveSessionTitle", () => {
  it("空文字・空白のみは既定の見出しになる", () => {
    // 無いと: 空タイトルの履歴が並び、あとから何の相談か分からなくなる
    expect(deriveSessionTitle("")).toBe("新しい相談");
    expect(deriveSessionTitle("   \n  ")).toBe("新しい相談");
  });

  it("短い発話はそのまま使う (改行・連続空白は 1 つに畳む)", () => {
    expect(deriveSessionTitle("仕事が\n  つらい")).toBe("仕事が つらい");
  });

  it(`${SESSION_TITLE_MAX} 文字を超えたら切り詰めて … を付ける`, () => {
    // 無いと: BFF の deriveTitle (26 文字) と非対称なまま出荷される
    const long = "あ".repeat(SESSION_TITLE_MAX + 5);
    const title = deriveSessionTitle(long);
    expect(title).toBe(`${"あ".repeat(SESSION_TITLE_MAX)}…`);
  });

  it(`ちょうど ${SESSION_TITLE_MAX} 文字は切り詰めない (境界)`, () => {
    const exact = "い".repeat(SESSION_TITLE_MAX);
    expect(deriveSessionTitle(exact)).toBe(exact);
  });

  it("BFF の deriveTitle と同じ閾値を使う", () => {
    // 無いと: 片側だけ閾値を変えても気づけない (この定数が唯一の接続点)
    expect(SESSION_TITLE_MAX).toBe(26);
  });
});
