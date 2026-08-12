/**
 * [単体] セッションタイトル導出のプロパティテスト (fast-check / #259)。
 * 仕様: docs/design/domain_rules.md §2。
 *
 * 無いと何が静かに通るか: タイトルは画面に出続けるだけの文字列なので、空になっても
 * 極端に長くなっても例外は出ない。一覧の見出しが空・崩れのまま静かに出荷される。
 *
 * 発見の記録: strategy.md の性質候補は「常に 26 文字以下」だったが、現行実装は
 * 「26 文字 + 省略記号 1 = 最大 27 文字」。仕様は domain_rules.md §2 で
 * 「本文 26 + 省略記号」に確定した (このテストは確定後の仕様に追従)。
 * さらに 2026-08-11 PO 裁定「文字境界で安全に切る」で、「文字」の単位は
 * **書記素 (grapheme)** に確定 (それまでは UTF-16 コードユニット単位で、
 * サロゲートペアを壊す未決の穴を it.fails で固定していた)。
 * このファイルの長さ検査は裁定に合わせて書記素で数える。
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { DEFAULT_TITLE, TITLE_MAX_CHARS, deriveTitle } from "./title";

const segmenter = new Intl.Segmenter("ja", { granularity: "grapheme" });
const countGraphemes = (text: string): number => [...segmenter.segment(text)].length;

describe("[単体] deriveTitle (property)", () => {
  it("どんな入力でも空にならない", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (concern) => {
        expect(deriveTitle(concern).length).toBeGreaterThan(0);
      }),
    );
  });

  it("長さは常に TITLE_MAX_CHARS + 1 書記素 (省略記号込み) 以下", () => {
    // 2026-08-11 PO 裁定: 「26 文字」の単位は書記素 (見た目の 1 文字)。
    // 以前は .length (UTF-16 ユニット) で数えていたが、裁定後は絵文字 1 個 = 1 と数える。
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (concern) => {
        expect(countGraphemes(deriveTitle(concern))).toBeLessThanOrEqual(TITLE_MAX_CHARS + 1);
      }),
    );
  });

  it("前後空白を除いて TITLE_MAX_CHARS 書記素以下の入力は、そのままタイトルになる (情報を勝手に変えない)", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: TITLE_MAX_CHARS }), (concern) => {
        const trimmed = concern.trim();
        fc.pre(trimmed.length > 0 && countGraphemes(trimmed) <= TITLE_MAX_CHARS);
        expect(deriveTitle(concern)).toBe(trimmed);
      }),
    );
  });

  it("空白のみの入力は既定タイトルになる", () => {
    fc.assert(
      fc.property(
        fc.string({ unit: fc.constantFrom(" ", "\n", "\t", "　"), maxLength: 30 }),
        (concern) => {
          expect(deriveTitle(concern)).toBe(DEFAULT_TITLE);
        },
      ),
    );
  });

  // 2026-08-11 PO 裁定「文字境界で安全に切る」で確定し、it.fails から昇格。
  // 旧実装は UTF-16 コードユニット単位 (String.prototype.slice) で切っており、
  // サロゲートペアの途中で千切れて「�」が表示された (domain_rules.md §2 の旧・未決)。
  // 書記素単位に確定したので、サロゲートペア (😀) も ZWJ 結合絵文字 (👩‍👩‍👧‍👦) も
  // 結合文字 (か + 結合濁点) も見た目の 1 文字として数え、境界で壊れない。
  it("サロゲートペア・結合文字を切り詰めで壊さない — 書記素境界で切る (2026-08-11 PO 裁定)", () => {
    fc.assert(
      fc.property(
        // 1 (あ) + n 書記素で必ず TITLE_MAX_CHARS を超える → 必ず切り詰めが起きる
        fc.integer({ min: TITLE_MAX_CHARS, max: 40 }),
        fc.constantFrom("😀", "👩‍👩‍👧‍👦", "か\u3099"),
        (n, unit) => {
          const title = deriveTitle(`あ${unit.repeat(n)}`);
          // 本文は入力の先頭 26 書記素そのまま + 省略記号 (書記素を千切らない)
          expect(title).toBe(`あ${unit.repeat(TITLE_MAX_CHARS - 1)}…`);
          // 孤立サロゲート (「�」表示の原因) が一切現れない (u フラグ付きの
          // サロゲート範囲は、対を成さないサロゲートにだけマッチする)
          expect(title).not.toMatch(/[\uD800-\uDFFF]/u);
        },
      ),
    );
  });
});
