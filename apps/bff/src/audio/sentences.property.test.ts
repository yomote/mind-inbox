/**
 * [単体] TTS 文分割のプロパティベーステスト (fast-check)。
 *
 * 無いと何が静かに通るか: 分割規則の変更で「読み上げ内容の一部が黙って消える」
 * 「プリフェッチと最終合成のキャッシュキーがズレて先行合成が無効化される」退行が、
 * 例ベースのテスト (sentences.test.ts) が拾わない入力の組み合わせで緑のまま通る。
 *
 * ここに書くのは**仕様として言える性質だけ** (docs/testing/strategy.md §3)。
 * 仕様の出どころは ADR 0024「TTS の文分割は BFF が単独で所有する」と
 * `prefetchTts` / `planTts` の責務 (apps/bff/src/tts/ttsService.ts):
 *   - 分割はキャッシュキーの生成器を兼ねる。プリフェッチ (途中経過テキスト) が
 *     合成した文が、最終合成でも同じ文字列として現れなければならない
 *   - フロントは planTts の返した文を 1 つずつ投げ返す。返した文がさらに割れて
 *     別のキャッシュキーになってはならない
 *
 * **プロパティとして書けなかった仕様**: 「分割結果を連結すると元テキストに戻る」
 * (TtsPlan の doc コメントにある表現) は成り立たない — 空白のみの断片は捨てる仕様
 * (関数 doc に明記) のため、連結すると空白のみの断片ぶんだけ原文より短くなり得る。
 * 読み上げで意味を持つのは非空白文字なので、仕様として言えるのは
 * 「空白を除いた内容の保存」まで。詳細は導入 PR の本文を参照。
 */

import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { MIN_SENTENCE_CHARS, splitTtsSentences } from "./sentences";

/**
 * 読み上げテキストらしい文字の混合: 文末記号 (全種) / 改行 / 空白 / 短い日本語・
 * ASCII 片。区切り記号の連続・空白のみ断片・記号無しの長文が全部生成される。
 */
const ttsTextArb = fc.string({
  unit: fc.constantFrom(
    "。",
    "．",
    "！",
    "？",
    "!",
    "?",
    "\n",
    " ",
    "\t",
    "あ",
    "疲",
    "れた",
    "はい",
    "a",
    "B",
    "、",
  ),
  maxLength: 120,
});

const stripWhitespace = (s: string): string => s.replace(/\s+/g, "");

describe("[単体] splitTtsSentences のプロパティ", () => {
  it("読み上げ内容 (非空白文字) を失わない・増やさない・並べ替えない", () => {
    // 仕様: 分割は読み上げ単位を切るだけで、話す内容そのものは変えない。
    // 空白は音にならないので「空白を除いた連結が原文と一致」までが仕様。
    fc.assert(
      fc.property(ttsTextArb, (text) => {
        const joined = splitTtsSentences(text).join("");
        expect(stripWhitespace(joined)).toBe(stripWhitespace(text));
      }),
    );
  });

  it("空白のみのセグメントを作らない (無音の合成リクエストを発生させない)", () => {
    fc.assert(
      fc.property(ttsTextArb, (text) => {
        for (const sentence of splitTtsSentences(text)) {
          expect(sentence.trim()).not.toBe("");
        }
      }),
    );
  });

  it("複数に割れたときの各文は最小長以上 (短い断片を単独で合成しない)", () => {
    // 仕様: 「はい。」のような短断片の単独合成 (不自然さ・往復増) を避けるため併合する。
    fc.assert(
      fc.property(ttsTextArb, (text) => {
        const sentences = splitTtsSentences(text);
        if (sentences.length >= 2) {
          for (const sentence of sentences) {
            expect(sentence.length).toBeGreaterThanOrEqual(MIN_SENTENCE_CHARS);
          }
        }
      }),
    );
  });

  it("分割済みの文をもう一度分割しても変わらない (投げ返された文のキャッシュキーがズレない)", () => {
    // 仕様: planTts の返した文をフロントが 1 つずつ /api/tts へ投げ返す。
    // そこで再分割が起きて別の文になると、プリフェッチ済みキャッシュにヒットしない。
    fc.assert(
      fc.property(ttsTextArb, (text) => {
        for (const sentence of splitTtsSentences(text)) {
          expect(splitTtsSentences(sentence)).toEqual([sentence]);
        }
      }),
    );
  });

  it("途中経過で確定した文は、最終テキストの分割にそのまま現れる (先行合成が必ずヒットする)", () => {
    // 仕様 (ADR 0024 の核): prefetchTts は途中経過テキストの「末尾以外」を確定文として
    // 合成する。ストリームが伸びても確定文の切り出しが変わらなければ、最終合成
    // (synthesizeTts) は同じキャッシュキーで必ずヒットする。
    fc.assert(
      fc.property(ttsTextArb, ttsTextArb, (prefix, rest) => {
        const completed = splitTtsSentences(prefix).slice(0, -1);
        const full = splitTtsSentences(prefix + rest);
        expect(full.slice(0, completed.length)).toEqual(completed);
      }),
    );
  });
});
