// @vitest-environment jsdom
/**
 * [単体] 「AI の整理」ペインの表示 (#433 段階 1 / dialogue-session.mdx §5.8)。
 *
 * 無いと何が静かに通るか:
 * - **サマリ行の実数**: 画面に出る唯一の数字が status の数え違いでズレても描画は壊れない
 *   (集計そのものは thinkingMap.test.ts が pin する。ここは「その数が画面に出ているか」)。
 * - **終わりの条件文**: 「未探索の枝が 0 になったら一区切り」を消しても画面は成立するが、
 *   PO の原意向 (この対話いつまで続くんだろう) への唯一の答えが消える (裁定 3)。
 * - **kind × status の 2 軸**: どちらかの表示が落ちても行は出るので、
 *   「AI の仮説をユーザーが認めた」が読めなくなったことに気づけない (裁定 4)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ThinkingMap } from "../../api";
import { ThinkingMapPane } from "./ThinkingMapPane";

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

  it("各ノードは kind と status の 2 軸を機械可読に持つ", () => {
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

  it("親子はネストして出す (箇条書きツリー)", () => {
    render(<ThinkingMapPane map={map} />);

    const root = screen.getAllByTestId("thinking-map-node")[0];
    expect(root.querySelectorAll("[data-testid='thinking-map-node']").length).toBe(2);
  });

  it("マップがまだ無いときは空状態を出す (数字を作らない)", () => {
    render(<ThinkingMapPane map={null} />);

    expect(screen.queryByTestId("thinking-map-summary")).toBeNull();
    expect(screen.getByTestId("thinking-map-pane").textContent).toContain(
      "会話が進むと、AI が今どう整理しているかがここに現れます",
    );
  });
});
