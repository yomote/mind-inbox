/**
 * [単体] 思考整理マップの契約の形を pin する (#433 段階 1 / 2026-08-15 PO 裁定 3)。
 *
 * 無いと何が静かに通るか:
 *   「進捗バーがあると分かりやすい」で `progress` / `completeness` / `percent` のような
 *   フィールドが後から生え、**測っていない数字**が画面に出る。文言テストでは止まらない
 *   (文言は後から変わる) ので、**そういうフィールドを契約に作れないこと**で止める。
 *   整理度合いは「ノードを数えた実数」だけで表す — 分母 (悩みが出きった状態) を
 *   誰も測っていないため、率は生成であって測定ではない。
 *
 * ここで test しないこと:
 *   - 画面の文言や配色 (frontend の単体テストの領域)
 *   - ai-agent 側 pydantic との一致 (`npm run test:contract` = L0 の領域)
 */

import { describe, expect, it } from "vitest";
import { ExtractionResultSchema, ThinkingMapSchema, ThinkingNodeSchema } from "./domain";

describe("[単体] ThinkingMap の契約 (#433)", () => {
  it("マップが持つのは nodes だけ (率・進捗のフィールドを作らせない)", () => {
    expect(Object.keys(ThinkingMapSchema.shape)).toEqual(["nodes"]);
  });

  it("ノードは kind × status の 2 軸 + ツリーの親 + Problem 接続穴だけ", () => {
    // 2 軸 (裁定 4) と、#321 用に空けた problemId 以外を増やさない。
    expect(Object.keys(ThinkingNodeSchema.shape).sort()).toEqual(
      ["id", "kind", "label", "parentId", "problemId", "status"].sort(),
    );
    expect(ThinkingNodeSchema.shape.kind.options).toEqual(["topic", "hypothesis", "unknown"]);
    expect(ThinkingNodeSchema.shape.status.options).toEqual([
      "confirmed",
      "tentative",
      "unexplored",
    ]);
  });

  it("マップを持たない抽出結果 (旧 ai-agent / 確定経路) は null として読める", () => {
    // 無いと: マップを返さない応答で ExtractionResult のパースが落ち、
    //         右ペインの下書きごと消える (マップは付加物であって前提ではない)。
    const parsed = ExtractionResultSchema.parse({
      sessionId: "s1",
      items: [],
      newProblemCount: 0,
      updatedProblemCount: 0,
    });
    expect(parsed.thinkingMap).toBeNull();
  });

  it("parentId / problemId を省いたノードは null で埋まる", () => {
    const parsed = ThinkingMapSchema.parse({
      nodes: [{ id: "n1", kind: "topic", label: "転職の不安", status: "confirmed" }],
    });
    expect(parsed.nodes[0]).toMatchObject({ parentId: null, problemId: null });
  });
});
