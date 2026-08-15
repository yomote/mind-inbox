// @vitest-environment jsdom
/**
 * [単体] 「AI の整理」ペインの表示 (#433 段階 1 + 段階 2 / dialogue-session.mdx §5.8)。
 *
 * 無いと何が静かに通るか:
 * - **サマリ行の実数**: 画面に出る唯一の数字が status の数え違いでズレても描画は壊れない
 *   (集計そのものは thinkingMap.test.ts が pin する。ここは「その数が画面に出ているか」)。
 * - **終わりの条件文**: 「未探索の枝が 0 になったら一区切り」を消しても画面は成立するが、
 *   PO の原意向 (この対話いつまで続くんだろう) への唯一の答えが消える (裁定 3)。
 * - **図とデータの一致**: 図の箱・線がデータより少なくても図は普通に描かれる。
 *   サマリは「話題 4」と言い続けるので、**見ても食い違いに気づけない**。
 * - **status → 枠線色の割り当て**: 確定と未探索が同じ色で描かれても図は成立する。
 *   「どれが確かで、どこがまだ聞けていないか」という図の唯一の情報が消えるのに気づけない。
 * - **ラベル本文の色**: status 色を本文に載せると、仮説・未探索の話題名が
 *   コントラスト 4.5:1 を割る (Codex P2)。**色が付いて見えてはいる**ので画面上は気づけない。
 * - **箇条書きへの落とし先**: 図にできない地図でも図を出し続けるようになると、
 *   空白または読めない図が出るだけになる (段階 1 の資産が死んでいることに気づけない)。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it } from "vitest";
import type { ThinkingMap } from "../../api";
import { ThinkingMapPane } from "./ThinkingMapPane";
import { MAX_GRAPH_NODES } from "./thinkingMapLayout";

afterEach(cleanup);

const map: ThinkingMap = {
  nodes: [
    {
      id: "n1",
      kind: "topic",
      label: "転職するかどうか",
      status: "confirmed",
      parentId: null,
      problemId: null,
    },
    {
      id: "n2",
      kind: "hypothesis",
      label: "失敗が怖いのが本体?",
      status: "tentative",
      parentId: "n1",
      problemId: null,
    },
    {
      id: "n3",
      kind: "unknown",
      label: "面談だけ受ける選択肢",
      status: "unexplored",
      parentId: "n1",
      problemId: null,
    },
    // status ごとの件数を**わざと不揃い**にしてある (確定 1 / 仮説 2 / 未探索 1)。
    // 揃えると、数え方を取り違える変異 (unexplored を tentative で数える等) が
    // 同じ数字を出してしまい、テストが素通りする。
    {
      id: "n4",
      kind: "topic",
      label: "眠れていない",
      status: "tentative",
      parentId: null,
      problemId: null,
    },
  ],
};

/**
 * status → 色の割り当てだけを見分けるための theme。
 * 実配色ではなく**互いに絶対に混ざらない値**を入れてあるので、
 * 割り当てを入れ替える変異が必ず落ちる。
 */
const sentinelTheme = createTheme({
  palette: {
    text: { primary: "#111111", secondary: "#666666" },
    warning: { main: "#ff8800" },
  },
});

function renderWithSentinelTheme(target: ThinkingMap | null) {
  return render(
    <ThemeProvider theme={sentinelTheme}>
      <ThinkingMapPane map={target} />
    </ThemeProvider>,
  );
}

function manyNodes(count: number): ThinkingMap {
  return {
    nodes: Array.from({ length: count }, (_, i) => ({
      id: `n${i}`,
      kind: "topic" as const,
      label: `話題${i}`,
      status: "confirmed" as const,
      parentId: null,
      problemId: null,
    })),
  };
}

describe("[単体] ThinkingMapPane", () => {
  it("実数のサマリを出す (話題 N / 確定・仮説・未探索)", () => {
    render(<ThinkingMapPane map={map} />);

    const summary = screen.getByTestId("thinking-map-summary").textContent ?? "";
    expect(summary).toContain("話題 4");
    expect(summary).toContain("確定 1");
    expect(summary).toContain("仮説 2");
    expect(summary).toContain("未探索 1");
  });

  it("終わりの条件を実数つきで出す (% でも進捗バーでもなく)", () => {
    render(<ThinkingMapPane map={map} />);

    const pane = screen.getByTestId("thinking-map-pane");
    expect(pane.textContent).toContain("未探索の枝が 0 になったら一区切り");
    expect(pane.textContent).toContain("いま 1 本");
    // 「増えることもある」を書いておかないと、枝が増えたときに裏切りになる。
    expect(pane.textContent).toContain("増えることもあります");
  });

  it("既定は図 (グラフ) 表示", () => {
    render(<ThinkingMapPane map={map} />);

    expect(screen.getByTestId("thinking-map-pane").getAttribute("data-view")).toBe("graph");
    expect(screen.getByTestId("thinking-map-graph")).toBeTruthy();
    expect(screen.queryByTestId("thinking-map-tree")).toBeNull();
  });

  it("図の箱と線の数がデータと一致する (節 4 個 / 入り枝 4 本 + 中心 1 個)", () => {
    render(<ThinkingMapPane map={map} />);

    expect(screen.getAllByTestId("thinking-map-node").length).toBe(map.nodes.length);
    expect(screen.getAllByTestId("thinking-map-edge").length).toBe(map.nodes.length);
    // 中心 (「この会話」) はデータ上の節ではないので thinking-map-node を名乗らない。
    expect(screen.getAllByTestId("thinking-map-center").length).toBe(1);
    const svg = screen.getByTestId("thinking-map-graph");
    expect(svg.getAttribute("data-node-count")).toBe(String(map.nodes.length));
  });

  it("図でも各ノードは kind と status の 2 軸を機械可読に持つ", () => {
    render(<ThinkingMapPane map={map} />);

    const nodes = screen.getAllByTestId("thinking-map-node");
    expect(nodes.map((n) => n.getAttribute("data-node-kind"))).toEqual([
      "topic",
      "hypothesis",
      "unknown",
      "topic",
    ]);
    expect(nodes.map((n) => n.getAttribute("data-node-status"))).toEqual([
      "confirmed",
      "tentative",
      "unexplored",
      "tentative",
    ]);
  });

  it("図の枠線の色は status ごとに別 (確定 / 仮説 / 未探索 が同じ色にならない)", () => {
    renderWithSentinelTheme(map);

    const strokeOf = (status: string) => {
      const node = screen
        .getAllByTestId("thinking-map-node")
        .find((n) => n.getAttribute("data-node-status") === status);
      return node?.querySelector("[data-testid='thinking-map-node-shape']")?.getAttribute("stroke");
    };

    expect(strokeOf("confirmed")).toBe("#111111"); // text.primary
    expect(strokeOf("tentative")).toBe("#ff8800"); // warning.main
    expect(strokeOf("unexplored")).toBe("#666666"); // text.secondary
  });

  it("ラベル本文の色は status で変えない (薄い色で本文を塗らない / WCAG 1.4.3)", () => {
    renderWithSentinelTheme(map);

    const fills = screen
      .getAllByTestId("thinking-map-node")
      .map((n) => n.querySelector("text")?.getAttribute("fill"));

    // 3 つの status が混ざった地図でも、本文の色は 1 種類だけ = 本文用の色。
    expect(new Set(fills).size).toBe(1);
    expect(fills[0]).toBe("#111111"); // text.primary (枠線の warning / secondary ではない)
  });

  it("図の枝も行き先の status で色と線が変わる (未探索の枝が見分けられる)", () => {
    renderWithSentinelTheme(map);

    const edges = screen.getAllByTestId("thinking-map-edge");
    const unexplored = edges.find((e) => e.getAttribute("data-edge-status") === "unexplored");
    const confirmed = edges.find((e) => e.getAttribute("data-edge-status") === "confirmed");

    expect(unexplored?.getAttribute("stroke")).toBe("#666666");
    expect(unexplored?.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(confirmed?.getAttribute("stroke")).toBe("#111111");
    // 確定は実線 — 破線にすると「確かさ = 線の途切れ」の意味が崩れる。
    expect(confirmed?.getAttribute("stroke-dasharray")).toBeNull();
  });

  it("形は kind ごとに別 (話題 / AI の見立て / 確かめたいこと が同じ形にならない)", () => {
    render(<ThinkingMapPane map={map} />);

    const shapeOf = (kind: string) =>
      screen
        .getAllByTestId("thinking-map-node")
        .find((n) => n.getAttribute("data-node-kind") === kind)
        ?.querySelector("[data-testid='thinking-map-node-shape']")
        ?.getAttribute("d");

    const shapes = ["topic", "hypothesis", "unknown"].map(shapeOf);
    expect(shapes.every((d) => typeof d === "string" && d.length > 0)).toBe(true);
    expect(new Set(shapes).size).toBe(3);
  });

  it("図には実数と同じ数字の読み上げ文が付く (SVG のままでは読めないため)", () => {
    render(<ThinkingMapPane map={map} />);

    const label = screen.getByTestId("thinking-map-graph").getAttribute("aria-label") ?? "";
    expect(label).toContain("話題 4");
    expect(label).toContain("未探索 1");
    expect(label).toContain("箇条書き");
  });

  it("「箇条書き」に切り替えると段階 1 のツリーが出る (図は消える)", () => {
    render(<ThinkingMapPane map={map} />);

    fireEvent.click(screen.getByTestId("thinking-map-view-tree"));

    expect(screen.getByTestId("thinking-map-pane").getAttribute("data-view")).toBe("tree");
    expect(screen.getByTestId("thinking-map-tree")).toBeTruthy();
    expect(screen.queryByTestId("thinking-map-graph")).toBeNull();
    // ツリーでも節の数は変わらない (同じ地図の別の見せ方)。
    expect(screen.getAllByTestId("thinking-map-node").length).toBe(map.nodes.length);
  });

  it(`節が ${MAX_GRAPH_NODES} 個までは図で出す`, () => {
    render(<ThinkingMapPane map={manyNodes(MAX_GRAPH_NODES)} />);

    expect(screen.getByTestId("thinking-map-pane").getAttribute("data-view")).toBe("graph");
    expect(screen.queryByTestId("thinking-map-fallback-note")).toBeNull();
  });

  it("図にできない地図では箇条書きへ自動で落ち、理由を画面に出す", () => {
    render(<ThinkingMapPane map={manyNodes(MAX_GRAPH_NODES + 1)} />);

    expect(screen.getByTestId("thinking-map-pane").getAttribute("data-view")).toBe("tree");
    expect(screen.queryByTestId("thinking-map-graph")).toBeNull();
    expect(screen.getByTestId("thinking-map-tree")).toBeTruthy();
    // 落ちた事実を黙って飲み込まない。
    expect(screen.getByTestId("thinking-map-fallback-note").textContent).toContain("多すぎる");
    // 図に戻せない状態なので、図のボタンは押せない (押しても変わらないボタンを出さない)。
    expect(screen.getByTestId("thinking-map-view-graph").getAttribute("disabled")).not.toBeNull();
    // 節は 1 つも落とさない。
    expect(screen.getAllByTestId("thinking-map-node").length).toBe(MAX_GRAPH_NODES + 1);
  });

  it("中心の予約名を LLM が節 id に使ってきても図が壊れない", () => {
    // 中心が節に上書きされると根の枝が自己ループに化けるが、節数と枝数は一致したままなので
    // フォールバック判定は正常と答える = **壊れた図が「正常」として出る** (Codex P2)。
    render(
      <ThinkingMapPane
        map={{
          nodes: [
            {
              id: "__conversation__",
              kind: "topic",
              label: "中心を名乗る節",
              status: "confirmed",
              parentId: null,
              problemId: null,
            },
            {
              id: "n2",
              kind: "unknown",
              label: "その子",
              status: "unexplored",
              parentId: "__conversation__",
              problemId: null,
            },
          ],
        }}
      />,
    );

    expect(screen.getByTestId("thinking-map-pane").getAttribute("data-view")).toBe("graph");
    expect(screen.getAllByTestId("thinking-map-node").length).toBe(2);
    expect(screen.getAllByTestId("thinking-map-center").length).toBe(1);
    const edges = screen.getAllByTestId("thinking-map-edge");
    expect(edges.length).toBe(2);
    // 根は中心 (center) から。自分自身から生えている枝が無いことを見る。
    expect(edges.map((e) => e.getAttribute("data-edge-from"))).toEqual([
      "center",
      "__conversation__",
    ]);
  });

  it("マップがまだ無いときは空状態を出す (数字を作らない)", () => {
    render(<ThinkingMapPane map={null} />);

    expect(screen.queryByTestId("thinking-map-summary")).toBeNull();
    expect(screen.queryByTestId("thinking-map-graph")).toBeNull();
    expect(screen.getByTestId("thinking-map-pane").textContent).toContain(
      "会話が進むと、AI が今どう整理しているかがここに現れます",
    );
  });
});
