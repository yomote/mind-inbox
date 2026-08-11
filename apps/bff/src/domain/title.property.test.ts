/**
 * [L1] セッションタイトル導出のプロパティテスト (fast-check / #259)。
 * 仕様: docs/design/domain_rules.md §2。
 *
 * 無いと何が静かに通るか: タイトルは画面に出続けるだけの文字列なので、空になっても
 * 極端に長くなっても例外は出ない。一覧の見出しが空・崩れのまま静かに出荷される。
 *
 * 発見の記録: strategy.md の性質候補は「常に 26 文字以下」だったが、現行実装は
 * 「26 文字 + 省略記号 1 = 最大 27 文字」。仕様は domain_rules.md §2 で
 * 「本文 26 + 省略記号」に確定した (このテストは確定後の仕様に追従)。
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { DEFAULT_TITLE, TITLE_MAX_CHARS, deriveTitle } from "./title";

describe("[L1] deriveTitle (property)", () => {
  it("どんな入力でも空にならない", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (concern) => {
        expect(deriveTitle(concern).length).toBeGreaterThan(0);
      }),
    );
  });

  it("長さは常に TITLE_MAX_CHARS + 1 (省略記号込み) 以下", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (concern) => {
        expect(deriveTitle(concern).length).toBeLessThanOrEqual(TITLE_MAX_CHARS + 1);
      }),
    );
  });

  it("前後空白を除いて TITLE_MAX_CHARS 以下の入力は、そのままタイトルになる (情報を勝手に変えない)", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: TITLE_MAX_CHARS }), (concern) => {
        const trimmed = concern.trim();
        fc.pre(trimmed.length > 0 && trimmed.length <= TITLE_MAX_CHARS);
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

  // 既知の仕様の穴 (docs/design/domain_rules.md §2 未決): 切り詰めは UTF-16 コード
  // ユニット単位 (String.prototype.slice) なので、26 ユニット目がサロゲートペアの
  // 途中に落ちると絵文字が壊れて「�」が表示される。直したらこのテストを通常の it に
  // 昇格すること (it.fails は「現状失敗する」ことを固定している)。
  it.fails("サロゲートペア (絵文字) を切り詰めで壊さない — 未決の仕様の穴", () => {
    fc.assert(
      fc.property(fc.integer({ min: 13, max: 40 }), (n) => {
        // "あ" (1 ユニット) + 絵文字 n 個 (2n ユニット) → 26 ユニット目が必ずペアの途中
        const title = deriveTitle(`あ${"😀".repeat(n)}`);
        expect(/[\uD800-\uDBFF]$/.test(title.slice(0, -1))).toBe(false);
      }),
    );
  });
});
