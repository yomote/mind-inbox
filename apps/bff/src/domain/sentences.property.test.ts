/**
 * [L1] TTS 文分割のプロパティテスト (fast-check / #259 の 1 本目)。
 * 仕様: docs/design/domain_rules.md §1。
 *
 * 無いと何が静かに通るか: 例ベースのテスト (sentences.test.ts) は代表例しか見ない。
 * 分割規則の変更が「文字を落とす / 増やす / 並びを変える」退行 (読み上げが本文と
 * 食い違う) を起こしても、例に無い組み合わせなら緑のまま通る。往復性質は全入力に
 * ついて内容保存を固定する。
 *
 * 発見の記録: strategy.md の性質候補は「分割して連結すると元の文字列に戻る」だったが、
 * 現行実装は**空白のみの断片を捨てる** (例: "a。 \n b。" の間の空白断片) ため、
 * 厳密な往復は「空白のみ断片が生じない入力」でだけ成り立つ。全入力で成り立つのは
 * 「元の文字列から空白のみ断片だけを除いたものとの厳密一致」— domain_rules.md §1 に
 * 現行仕様として明文化した。
 *
 * 当初は両辺の空白を落として比較していたが、それでは「本文中の空白を潰す」退行
 * (例: 「今日は 少し疲れた。」→「今日は少し疲れた。」で TTS に渡る本文が静かに変わる)
 * まで素通しになる (PR #263 Codex 指摘)。現在は参照モデル (expectedJoined) との
 * 厳密一致で「捨ててよいのは空白のみ断片だけ・残る断片は内部の空白まで一字一句保存」を固定する。
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { splitTtsSentences } from "./sentences";

/** sentences.ts の MIN_SENTENCE_CHARS と同値 (仕様: domain_rules.md §1)。 */
const MIN_SENTENCE_CHARS = 8;

// 空白を含まない本文文字 (絵文字 = サロゲートペアも含める) と区切り文字。
// アルファベットを絞るのは shrink を効かせるため (落ちたときの反例が読める)。
const CONTENT_CHARS = [..."今日は少し疲れて眠れない仕事abZ09、・ー…😀"];
const SEPARATORS_NO_NEWLINE = [..."。．！？!?"];
const WHITESPACE = [" ", "\n", "\t", "　"];

/** 空白を一切含まない入力 — 厳密な往復が成り立つ領域。 */
const wsFreeText = fc.string({
  unit: fc.constantFrom(...CONTENT_CHARS, ...SEPARATORS_NO_NEWLINE),
  maxLength: 120,
});

/** 空白・改行 (改行は区切り文字でもある) を混ぜた一般入力。 */
const anyText = fc.string({
  unit: fc.constantFrom(...CONTENT_CHARS, ...SEPARATORS_NO_NEWLINE, ...WHITESPACE),
  maxLength: 120,
});

/**
 * 参照モデル: 実装が捨ててよいのは「空白のみの断片」だけ、という仕様 (domain_rules.md §1-3)
 * を実装と独立に表したもの。区切り文字の連続 (run) の直後で断片に切り、空白のみの断片を
 * 取り除いて連結する。分割結果の連結はこれと**厳密一致**しなければならない — 断片の
 * 内部・端の空白も一字一句保存される (併合の仕方は join に影響しないので関与しない)。
 */
const SEPARATOR_RUN_END = /(?<=[。．！？!?\n])(?![。．！？!?\n])/;
const expectedJoined = (text: string): string =>
  text
    .split(SEPARATOR_RUN_END)
    .filter((piece) => piece.trim() !== "")
    .join("");

describe("[L1] splitTtsSentences (property)", () => {
  it("空白のみ断片が生じない入力では、分割して連結すると元の文字列に戻る (往復)", () => {
    fc.assert(
      fc.property(wsFreeText, (text) => {
        expect(splitTtsSentences(text).join("")).toBe(text);
      }),
    );
  });

  it("どんな入力でも、連結結果は「元の文字列から空白のみ断片だけを除いたもの」と厳密一致する (本文中の空白も潰れない)", () => {
    fc.assert(
      fc.property(anyText, (text) => {
        expect(splitTtsSentences(text).join("")).toBe(expectedJoined(text));
      }),
    );
  });

  it("区切り文字を含まない入力は、空白のみでない限り丸ごと 1 文になる (本文中の空白がそのまま残る)", () => {
    // 参照モデルの自明ケースを退行例 (「今日は 少し疲れた。」の空白潰し) の形で直接固定する。
    // 改行は区切り文字なので除外する (区切り文字を含まない入力、が前提の性質)。
    fc.assert(
      fc.property(
        fc.string({ unit: fc.constantFrom(...CONTENT_CHARS, " ", "\t", "　"), maxLength: 120 }),
        (text) => {
          const expected = text.trim() === "" ? [] : [text];
          expect(splitTtsSentences(text)).toEqual(expected);
        },
      ),
    );
  });

  it("出力に空白のみの文は含まれない (無音の合成往復を発生させない)", () => {
    fc.assert(
      fc.property(anyText, (text) => {
        for (const sentence of splitTtsSentences(text)) {
          expect(sentence.trim()).not.toBe("");
        }
      }),
    );
  });

  it("2 文以上に分かれたら、どの文も MIN_SENTENCE_CHARS 以上 (短断片は必ず併合される)", () => {
    fc.assert(
      fc.property(anyText, (text) => {
        const sentences = splitTtsSentences(text);
        if (sentences.length >= 2) {
          for (const sentence of sentences) {
            expect(sentence.length).toBeGreaterThanOrEqual(MIN_SENTENCE_CHARS);
          }
        }
      }),
    );
  });

  it("空配列になるのは空白のみの入力に限る", () => {
    fc.assert(
      fc.property(anyText, (text) => {
        expect(splitTtsSentences(text).length === 0).toBe(text.trim() === "");
      }),
    );
  });
});
