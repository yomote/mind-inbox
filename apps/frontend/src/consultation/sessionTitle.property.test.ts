/**
 * [L1] セッションタイトル自動生成のプロパティテスト (fast-check / #259 のフロント実証)。
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

describe("[L1] deriveSessionTitle (property)", () => {
  it("どんな入力でも空にならない", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (text) => {
        expect(deriveSessionTitle(text).length).toBeGreaterThan(0);
      }),
    );
  });

  it("長さは常に SESSION_TITLE_MAX + 1 (省略記号込み) 以下", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme", maxLength: 100 }), (text) => {
        expect(deriveSessionTitle(text).length).toBeLessThanOrEqual(SESSION_TITLE_MAX + 1);
      }),
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
