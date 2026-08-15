// @vitest-environment jsdom
/**
 * [単体] 「AI の整理」の箇条書き表示 (#433 段階 1 / §5.8.1)。
 *
 * 段階 2 (図) が入っても**この表示は消さない**ので、テストも段階 1 のまま残す。
 *
 * 無いと何が静かに通るか:
 * - **親子のネスト**: 全部を平らに並べても行数は同じなので、
 *   「どの話題の下の枝か」が失われたことに画面上で気づけない。
 * - **kind × status の 2 軸**: どちらかの表示が落ちても行は出るので、
 *   「AI の仮説をユーザーが認めた」が読めなくなったことに気づけない (裁定 4)。
 * - **ラベル本文の色**: ここは「図が読めない人の経路」なので、その本文が status 色
 *   (白背景で 3.1:1 / 5.5:1) で塗られていたら経路として成立しない (Codex P2 / WCAG 1.4.3)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ThinkingMap } from "../../api";
import { buildThinkingTree } from "./thinkingMap";
import { ThinkingMapTree } from "./ThinkingMapTree";

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

describe("[単体] ThinkingMapTree", () => {
  it("親子はネストして出す", () => {
    render(<ThinkingMapTree roots={buildThinkingTree(map)} />);

    const root = screen.getAllByTestId("thinking-map-node")[0];
    expect(root.querySelectorAll("[data-testid='thinking-map-node']").length).toBe(2);
  });

  it("各ノードは kind と status の 2 軸を機械可読に持つ", () => {
    render(<ThinkingMapTree roots={buildThinkingTree(map)} />);

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

  it("ラベル本文は status で色を変えない (薄い色で本文を塗らない / WCAG 1.4.3)", () => {
    render(<ThinkingMapTree roots={buildThinkingTree(map)} />);

    const labelOf = (status: string) =>
      screen
        .getAllByTestId("thinking-map-node")
        .find((n) => n.getAttribute("data-node-status") === status)
        ?.querySelector("[data-testid='thinking-map-node-label']")?.className;

    // 仮説 (warning) と未探索 (secondary) は太さも同じなので、**本文に色を足すと
    // ここで class が割れる**。同じなら本文の色は status に依存していない。
    expect(labelOf("tentative")).toBe(labelOf("unexplored"));
    expect(labelOf("tentative")).toBeTruthy();
  });

  it("status と kind を文字でも出す (色と記号だけに頼らない)", () => {
    render(<ThinkingMapTree roots={buildThinkingTree(map)} />);

    const nodes = screen.getAllByTestId("thinking-map-node");
    expect(nodes[0].textContent).toContain("確定");
    expect(nodes[2].textContent).toContain("未探索");
    // kind の語は status の語 (確定 / 仮説 / 未探索) と重ならないものを選んである。
    expect(nodes[1].textContent).toContain("AI の見立て");
  });
});
