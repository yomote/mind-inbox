/**
 * [単体] セッションタイトル自動生成のプロパティテスト (fast-check / #259 のフロント実証)。
 * 仕様: docs/design/domain_rules.md §2。
 *
 * 無いと何が静かに通るか: タイトルは表示されるだけの文字列なので、空・複数行・
 * 過長になっても例外は出ず、履歴一覧の見出しが崩れたまま出荷される。
 * 例ベース (sessionTitle.test.ts) が境界の代表例を、ここが全入力の性質を固定する。
 *
 * フロントの純粋ロジック層では BFF と同じ書き味で fast-check が使えることの実証
 * (docs/testing/property-based-testing.md §4 の調査結論の根拠)。
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { SESSION_TITLE_MAX, deriveSessionTitle } from "./sessionTitle";

const segmenter = new Intl.Segmenter("ja", { granularity: "grapheme" });
const countGraphemes = (text: string): number => [...segmenter.segment(text)].length;

describe("[単体] deriveSessionTitle (property)", () => {
  it("どんな入力でも空にならない", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (text) => {
        expect(deriveSessionTitle(text).length).toBeGreaterThan(0);
      }),
    );
  });

  it("長さは常に SESSION_TITLE_MAX + 1 書記素 (省略記号込み) 以下", () => {
    // 2026-08-11 PO 裁定「文字境界で安全に切る」: 「26 文字」の単位は書記素
    // (見た目の 1 文字)。以前は .length (UTF-16 ユニット) で数えていた。
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (text) => {
        expect(countGraphemes(deriveSessionTitle(text))).toBeLessThanOrEqual(SESSION_TITLE_MAX + 1);
      }),
    );
  });

  it("サロゲートペア・結合文字を切り詰めで壊さない — 書記素境界で切る (2026-08-11 PO 裁定)", () => {
    // BFF `domain/title.property.test.ts` の昇格テストと対 (mock と実 API の対称性)。
    fc.assert(
      fc.property(
        // 1 (あ) + n 書記素で必ず SESSION_TITLE_MAX を超える → 必ず切り詰めが起きる
        fc.integer({ min: SESSION_TITLE_MAX, max: 40 }),
        fc.constantFrom("😀", "👩‍👩‍👧‍👦", "か\u3099"),
        (n, unit) => {
          const title = deriveSessionTitle(`あ${unit.repeat(n)}`);
          // 本文は入力の先頭 26 書記素そのまま + 省略記号 (書記素を千切らない)
          expect(title).toBe(`あ${unit.repeat(SESSION_TITLE_MAX - 1)}…`);
          // 孤立サロゲート (「�」表示の原因) が一切現れない
          expect(title).not.toMatch(/[\uD800-\uDFFF]/u);
        },
      ),
    );
  });

  it("結果は必ず 1 行に畳まれる (改行・タブ・連続空白を含まない)", () => {
    // 一覧の見出しに使うための本質的な性質。改行入りの発話・音声認識の生テキストが
    // そのまま見出しに漏れると行が崩れる。
    fc.assert(
      fc.property(
        fc.string({
          unit: fc.oneof(
            fc.string({ unit: "grapheme", minLength: 1, maxLength: 1 }),
            fc.constantFrom(" ", "\n", "\r", "\t", "　"),
          ),
          maxLength: 100,
        }),
        (text) => {
          const title = deriveSessionTitle(text);
          expect(title).not.toMatch(/[\n\r\t]/);
          expect(title).not.toMatch(/ {2}/);
        },
      ),
    );
  });
});
