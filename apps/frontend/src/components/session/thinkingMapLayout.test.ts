/**
 * [単体] 思考整理マップのグラフ配置 (#433 段階 2 / §5.8.1)。
 *
 * 無いと何が静かに通るか:
 * - **節の取りこぼし**: 配置で節が 1 つ落ちても図は普通に描かれる。サマリは「話題 5」と
 *   言い続けるので、**画面を見ても食い違いに気づけない** (段階 1 が守った誠実さが図で崩れる)。
 * - **枝の張り忘れ**: 入り枝の無い箱は「どこにも繋がらない箱」として描かれ、
 *   親を取りこぼしたのか根なのかが読み手に区別できない。
 * - **重なり**: jsdom には `getBBox` が無いので、重なりは**座標の算術でしか**検証できない。
 *   ここが無いと、行の割り当てを壊しても (親を子の中央に置かない等) テストは緑のまま
 *   実ブラウザでラベルが重なる。
 * - **フォールバック判定**: 節が何個あっても図を描き続けるようになっても画面は成立するが、
 *   読めない図が出るだけになる (読めないことは画面に出ない)。
 * - **中心とデータ id の衝突**: LLM が中心の予約 id を返すと、中心が節に上書きされて
 *   根への枝が**自己ループ**に化ける。節数と枝数は一致したままなので
 *   `graphFallbackReason` も正常と判定し、**壊れた図が「正常」として出る** (Codex P2)。
 */

import { describe, expect, it } from "vitest";
import type { ThinkingMap } from "../../api";
import { buildThinkingTree } from "./thinkingMap";
import {
  MAX_GRAPH_NODES,
  graphFallbackReason,
  layoutThinkingGraph,
  wrapLabel,
} from "./thinkingMapLayout";

type NodeSeed = {
  id: string;
  label?: string;
  parentId?: string | null;
  status?: "confirmed" | "tentative" | "unexplored";
  kind?: "topic" | "hypothesis" | "unknown";
};

function mapOf(seeds: NodeSeed[]): ThinkingMap {
  return {
    nodes: seeds.map((seed) => ({
      id: seed.id,
      kind: seed.kind ?? "topic",
      label: seed.label ?? seed.id,
      status: seed.status ?? "confirmed",
      parentId: seed.parentId ?? null,
      problemId: null,
    })),
  };
}

const sample = mapOf([
  { id: "a", label: "転職するかどうか" },
  {
    id: "a1",
    label: "失敗が怖いのが本体?",
    parentId: "a",
    kind: "hypothesis",
    status: "tentative",
  },
  { id: "a2", label: "面談だけ受ける選択肢", parentId: "a", kind: "unknown", status: "unexplored" },
  { id: "b", label: "来週のプレゼン" },
  { id: "b1", label: "準備の時間が取れるか", parentId: "b", kind: "unknown", status: "unexplored" },
]);

describe("[単体] layoutThinkingGraph", () => {
  it("節を 1 つも落とさない (図の箱の数 = 地図の節の数)", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));

    expect(layout.nodes.length).toBe(sample.nodes.length);
    expect(layout.nodes.map((n) => n.id).sort()).toEqual(["a", "a1", "a2", "b", "b1"]);
  });

  it("すべての節にちょうど 1 本の入り枝が張られ、根は中心から生える", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));

    expect(layout.edges.length).toBe(layout.nodes.length);
    const incoming = new Map(layout.edges.map((e) => [e.toId, e.fromNodeId]));
    expect(incoming.size).toBe(layout.nodes.length);
    // 根の出どころは **null = 中心**。データ id ではないので、どんな id とも衝突しない。
    expect(incoming.get("a")).toBeNull();
    expect(incoming.get("b")).toBeNull();
    expect(incoming.get("a1")).toBe("a");
    expect(incoming.get("b1")).toBe("b");
  });

  it("中心はデータ id の名前空間に居ない (予約 id を LLM が返しても壊れない)", () => {
    // LLM が中心の器と同じ名前を節 id に使ってきた場合。契約 (`ThinkingNodeSchema`) は
    // 任意の文字列 id を許し、ai-agent の健全化もこの名前を拒否しない。
    const collided = mapOf([
      { id: "__conversation__", label: "中心を名乗る節" },
      { id: "child", label: "その子", parentId: "__conversation__" },
      { id: "other", label: "別の根" },
    ]);
    const layout = layoutThinkingGraph(buildThinkingTree(collided));

    expect(layout.nodes.length).toBe(3);
    expect(layout.edges.length).toBe(3);
    // **自己ループが 1 本も無いこと**が要点 (中心が節に化けると根が自分自身に繋がる)。
    for (const edge of layout.edges) expect(edge.fromNodeId).not.toBe(edge.toId);
    // 「中心を名乗る節」も普通の根として中心から生える。
    const incoming = new Map(layout.edges.map((e) => [e.toId, e.fromNodeId]));
    expect(incoming.get("__conversation__")).toBeNull();
    expect(incoming.get("other")).toBeNull();
    expect(incoming.get("child")).toBe("__conversation__");
    // 中心の箱は別に存在し続ける (節に食われない)。
    expect(layout.center.lines).toEqual(["この会話"]);
    expect(graphFallbackReason(layout, 3)).toBeNull();
  });

  it("枝の見た目は行き先の status で決まる (未探索の枝が図の上で見分けられる)", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));

    const byTo = new Map(layout.edges.map((e) => [e.toId, e.status]));
    expect(byTo.get("a1")).toBe("tentative");
    expect(byTo.get("a2")).toBe("unexplored");
    expect(byTo.get("a")).toBe("confirmed");
  });

  it("同じ列の箱は縦に重ならない (実測できないので座標で確かめる)", () => {
    // 2 行ラベルになる長い名前を混ぜる — 箱が高くなる側で重なりが出やすい。
    const layout = layoutThinkingGraph(
      buildThinkingTree(
        mapOf([
          { id: "a", label: "転職するかどうかを決めたい" },
          { id: "a1", label: "失敗が怖いのが本体かもしれない", parentId: "a" },
          { id: "a2", label: "面談だけ受ける選択肢もある", parentId: "a" },
          { id: "b", label: "来週のプレゼン" },
          { id: "b1", label: "準備の時間が取れるか", parentId: "b" },
          { id: "b2", label: "誰に相談できるか", parentId: "b" },
        ]),
      ),
    );

    const columns = new Map<number, { y: number; height: number }[]>();
    for (const node of [...layout.nodes, layout.center]) {
      const column = columns.get(node.x) ?? [];
      column.push({ y: node.y, height: node.height });
      columns.set(node.x, column);
    }
    for (const boxes of columns.values()) {
      const sorted = [...boxes].sort((l, r) => l.y - r.y);
      for (let i = 1; i < sorted.length; i += 1) {
        expect(sorted[i].y).toBeGreaterThanOrEqual(sorted[i - 1].y + sorted[i - 1].height);
      }
    }
  });

  it("すべての箱がキャンバスの内側に収まる", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));

    for (const node of [...layout.nodes, layout.center]) {
      expect(node.x).toBeGreaterThanOrEqual(0);
      expect(node.y).toBeGreaterThanOrEqual(0);
      expect(node.x + node.width).toBeLessThanOrEqual(layout.width);
      expect(node.y + node.height).toBeLessThanOrEqual(layout.height);
    }
  });

  it("親は子より右の列に置かれ、子の縦位置の中央に来る", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));
    const byId = new Map(layout.nodes.map((n) => [n.id, n]));
    const centerY = (id: string) => {
      const node = byId.get(id);
      if (node === undefined) throw new Error(`missing ${id}`);
      return node.y + node.height / 2;
    };

    const parent = byId.get("a");
    const child = byId.get("a1");
    if (parent === undefined || child === undefined) throw new Error("missing node");
    expect(child.x).toBeGreaterThan(parent.x);
    expect(centerY("a")).toBeCloseTo((centerY("a1") + centerY("a2")) / 2, 5);
  });

  it("親を辿れない節も図に出る (箇条書きと同じく根へ上げる)", () => {
    // 親が地図に居ない / 循環している節。段階 1 は根へ上げる約束なので、図でも同じ数だけ出る。
    const broken = mapOf([
      { id: "orphan", parentId: "missing" },
      { id: "x", parentId: "y" },
      { id: "y", parentId: "x" },
    ]);
    const layout = layoutThinkingGraph(buildThinkingTree(broken));

    expect(layout.nodes.length).toBe(3);
    expect(layout.edges.length).toBe(3);
    expect(graphFallbackReason(layout, 3)).toBeNull();
    // 循環していた側も、図の上では中心から 1 本だけ線が来る (根に上がっているため)。
    const incoming = layout.edges.filter((e) => e.toId === "orphan");
    expect(incoming.map((e) => e.fromNodeId)).toEqual([null]);
  });

  it("節が 1 つも無くても座標計算が壊れない", () => {
    const layout = layoutThinkingGraph([]);

    expect(layout.nodes).toEqual([]);
    expect(Number.isFinite(layout.width)).toBe(true);
    expect(Number.isFinite(layout.height)).toBe(true);
  });
});

describe("[単体] wrapLabel", () => {
  it("長いラベルは 2 行に折り返し、溢れたら「…」で切る (隣の列へはみ出させない)", () => {
    const lines = wrapLabel("転職するかどうかをそろそろ決めないといけない気がしている");

    expect(lines.length).toBe(2);
    expect(lines[lines.length - 1].endsWith("…")).toBe(true);
    for (const line of lines) expect([...line].length).toBeLessThanOrEqual(9);
  });

  it("収まるラベルは切らない", () => {
    expect(wrapLabel("来週のプレゼン")).toEqual(["来週のプレゼン"]);
  });

  it("半角は 0.5 文字ぶんで数える (英数字のラベルが不必要に折り返らない)", () => {
    expect(wrapLabel("ABCDEFGHIJKLMNOP")).toEqual(["ABCDEFGHIJKLMNOP"]);
  });
});

describe("[単体] graphFallbackReason", () => {
  it("描ける地図では null (= 図で出す)", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));

    expect(graphFallbackReason(layout, sample.nodes.length)).toBeNull();
  });

  it("節が 0 なら図にしない", () => {
    expect(graphFallbackReason(layoutThinkingGraph([]), 0)).not.toBeNull();
  });

  it("上限は上流 (extractor の 24 節 cap) と同値にしてある (画面が独自の締めを持たない)", () => {
    // ここがずれると「上流は通したのに画面が落とす」/「上流が緩んだのに画面が素通し」の
    // どちらかが起き、どちらが正しいか誰も判断できなくなる。
    expect(MAX_GRAPH_NODES).toBe(24);
  });

  it(`節が ${MAX_GRAPH_NODES} 個までは図、超えたら箇条書きへ落とす`, () => {
    const seeds = (count: number) =>
      Array.from({ length: count }, (_, i) => ({ id: `n${i}`, label: `話題${i}` }));

    const atLimit = mapOf(seeds(MAX_GRAPH_NODES));
    expect(
      graphFallbackReason(layoutThinkingGraph(buildThinkingTree(atLimit)), MAX_GRAPH_NODES),
    ).toBeNull();

    const overLimit = mapOf(seeds(MAX_GRAPH_NODES + 1));
    expect(
      graphFallbackReason(layoutThinkingGraph(buildThinkingTree(overLimit)), MAX_GRAPH_NODES + 1),
    ).toContain("多すぎる");
  });

  it("配置できた節の数が地図の節の数と違うなら図にしない", () => {
    // 「話題 6」と言いながら箱が 5 個、を図で出さないための門。
    const layout = layoutThinkingGraph(buildThinkingTree(sample));

    expect(graphFallbackReason(layout, sample.nodes.length + 1)).toContain("図に置けなかった");
  });

  it("座標が数でなくなったら図にしない", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));
    const broken = { ...layout, nodes: layout.nodes.map((n) => ({ ...n, y: Number.NaN })) };

    expect(graphFallbackReason(broken, sample.nodes.length)).toContain("座標");
  });

  it("枝が節より少ないなら図にしない (浮いた箱を出さない)", () => {
    const layout = layoutThinkingGraph(buildThinkingTree(sample));
    const broken = { ...layout, edges: layout.edges.slice(1) };

    expect(graphFallbackReason(broken, sample.nodes.length)).toContain("枝を張れなかった");
  });
});
