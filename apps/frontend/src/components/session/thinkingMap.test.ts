/**
 * [単体] 整理マップの集計とツリー組み立て (#433 段階 1 / dialogue-session.mdx §5.8)。
 *
 * 無いと何が静かに通るか:
 * - 集計: 「話題 5 / 確定 2・仮説 2・未探索 1」は**画面に出る唯一の実数**。status の
 *   数え違い (unexplored を tentative に混ぜる等) が起きても画面は普通に描画されるので、
 *   数字が嘘になったことに誰も気づけない。整理度合いを % ではなく実数で出すと決めた
 *   (2026-08-15 PO 裁定 3) 以上、その数の正しさがこの機能の誠実さそのもの。
 * - ツリー: 親が居ない / 循環している parentId は LLM 出力なら普通に起きる。捨てる実装だと
 *   節が画面から消えて実数と行数が食い違い、循環をそのまま組むと描画が無限再帰する。
 *
 * ここで test しないこと:
 *   - ノードの中身の妥当性 (LLM の生成物 — prompt の領域)
 *   - 色や文言 (ThinkingMapPane のテストと MDX の領域)
 */

import { describe, expect, it } from "vitest";
import type { ThinkingMap, ThinkingNode } from "../../api";
import { buildThinkingTree, summarizeThinkingMap } from "./thinkingMap";

const node = (overrides: Partial<ThinkingNode> & { id: string }): ThinkingNode => ({
  kind: "topic",
  label: overrides.id,
  status: "confirmed",
  parentId: null,
  problemId: null,
  ...overrides,
});

const map = (nodes: ThinkingNode[]): ThinkingMap => ({ nodes });

describe("[単体] summarizeThinkingMap", () => {
  it("status ごとの実数と総数を数える", () => {
    const summary = summarizeThinkingMap(
      map([
        node({ id: "a", status: "confirmed" }),
        node({ id: "b", status: "confirmed" }),
        node({ id: "c", status: "tentative" }),
        node({ id: "d", status: "tentative" }),
        node({ id: "e", status: "unexplored" }),
      ]),
    );

    expect(summary).toEqual({ total: 5, confirmed: 2, tentative: 2, unexplored: 1 });
  });

  it("未探索の枝は増える方向にも変わる (減る一方ではない)", () => {
    // 無いと: 単調減少を前提にした実装 (減った分だけバーを伸ばす等) が静かに入り込む。
    // 会話が進むと新しい穴が見つかるので、枝は増えることがある。
    const before = summarizeThinkingMap(map([node({ id: "a", status: "unexplored" })]));
    const after = summarizeThinkingMap(
      map([node({ id: "a", status: "unexplored" }), node({ id: "b", status: "unexplored" })]),
    );

    expect(before.unexplored).toBe(1);
    expect(after.unexplored).toBe(2);
  });

  it("マップが無いときは全部 0 (エラーにしない)", () => {
    expect(summarizeThinkingMap(null)).toEqual({
      total: 0,
      confirmed: 0,
      tentative: 0,
      unexplored: 0,
    });
  });
});

describe("[単体] buildThinkingTree", () => {
  it("parentId から親子に組み直す", () => {
    const roots = buildThinkingTree(
      map([node({ id: "root" }), node({ id: "child", parentId: "root" })]),
    );

    expect(roots.map((n) => n.id)).toEqual(["root"]);
    expect(roots[0].children.map((n) => n.id)).toEqual(["child"]);
  });

  it("親が地図に居ない節は捨てずに根へ上げる", () => {
    // 無いと: 画面の行数がサマリの実数より少なくなり、「数えただけ」が崩れる。
    const roots = buildThinkingTree(map([node({ id: "orphan", parentId: "missing" })]));

    expect(roots.map((n) => n.id)).toEqual(["orphan"]);
  });

  it("循環した parentId は根に落として断ち切る (描画の無限再帰を止める)", () => {
    const roots = buildThinkingTree(
      map([node({ id: "a", parentId: "b" }), node({ id: "b", parentId: "a" })]),
    );

    expect(roots.map((n) => n.id).sort()).toEqual(["a", "b"]);
    expect(roots.every((n) => n.children.length === 0)).toBe(true);
  });

  it("節を 1 つも失わない (サマリの総数と一致する)", () => {
    const nodes = [
      node({ id: "a" }),
      node({ id: "b", parentId: "a" }),
      node({ id: "c", parentId: "missing" }),
      node({ id: "d", parentId: "d" }),
    ];
    const count = (list: ReturnType<typeof buildThinkingTree>): number =>
      list.reduce((acc, n) => acc + 1 + count(n.children), 0);

    expect(count(buildThinkingTree(map(nodes)))).toBe(summarizeThinkingMap(map(nodes)).total);
  });
});
