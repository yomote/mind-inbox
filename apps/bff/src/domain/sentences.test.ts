/**
 * [L1] TTS 文分割の単体テスト。
 *
 * 無いと何が静かに通るか: 分割規則が変わってもフロントのプリフェッチ (同一関数を
 * import) と BFF の合成は「動く」ため、キャッシュが全ミスして先行合成が無効化される
 * 退行が緑のまま通る。分割の境界仕様をここで固定する。
 */

import { describe, expect, it } from "vitest";
import { splitTtsSentences } from "./sentences";

describe("[L1] splitTtsSentences", () => {
  it("句点・疑問符で分割し、区切り文字は文側に残す", () => {
    expect(
      splitTtsSentences("今日は少し疲れました。何から話せばいいでしょうか？最近眠れていません。"),
    ).toEqual(["今日は少し疲れました。", "何から話せばいいでしょうか？", "最近眠れていません。"]);
  });

  it("短い断片は隣の文に併合する (単独合成の往復増を防ぐ)", () => {
    expect(splitTtsSentences("はい。それは大変でしたね、無理もないと思います。")).toEqual([
      "はい。それは大変でしたね、無理もないと思います。",
    ]);
  });

  it("改行も文境界として扱う", () => {
    expect(splitTtsSentences("一つ目の話です。\n二つ目の話をします。")).toEqual([
      "一つ目の話です。\n",
      "二つ目の話をします。",
    ]);
  });

  it("区切りなしのテキストは 1 文のまま", () => {
    expect(splitTtsSentences("区切り文字のないテキスト")).toEqual(["区切り文字のないテキスト"]);
  });

  it("末尾の短い断片は前の文へ併合する", () => {
    expect(splitTtsSentences("今日は疲れたので早めに休みます。以上。")).toEqual([
      "今日は疲れたので早めに休みます。以上。",
    ]);
  });

  it("空文字は空配列", () => {
    expect(splitTtsSentences("")).toEqual([]);
  });
});
