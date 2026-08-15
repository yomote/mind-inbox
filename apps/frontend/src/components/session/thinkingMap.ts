/**
 * 「AI の整理」(思考整理マップ) の集計とツリー組み立て (#433 段階 1 / §5.8)。
 *
 * 表示 (ThinkingMapPane) と分けてあるのは、**画面に出る実数がここだけで決まる**ため。
 * 整理度合いは「ノードを数えただけ」の実数で出す約束 (2026-08-15 PO 裁定 3) なので、
 * ここに率や推定を入れない — 分母 (悩みが出きった状態) は誰も測っていない。
 */

import type { ThinkingMap, ThinkingNode } from "../../api";

export type ThinkingMapSummary = {
  /** 地図にある節の総数 (画面の「話題 N」) */
  total: number;
  confirmed: number;
  tentative: number;
  /** まだ聞けていない枝。**増えることもある** (会話で新しい穴が見つかる) */
  unexplored: number;
};

/**
 * サマリとツリーが**同じ集合**を見るための正規化 (Codex P2 / #433)。
 *
 * 契約 (`ThinkingMapSchema`) は id の一意性を検証しないので、重複 id を含む応答も
 * 素通りする。数える側と描く側が別々に配列を扱うと、**「話題 2」と出しながらツリーは
 * 1 行**という食い違いが起きる — 画面は普通に描画されるので気づけない。
 * id の重複は**先勝ち**で 1 つに潰す (後勝ちにすると親の指す先が割れる。ai-agent 側の
 * 健全化と同じ規則)。
 */
function normalizeNodes(map: ThinkingMap | null | undefined): ThinkingNode[] {
  const byId = new Map<string, ThinkingNode>();
  for (const node of map?.nodes ?? []) {
    if (!byId.has(node.id)) byId.set(node.id, node);
  }
  return [...byId.values()];
}

export function summarizeThinkingMap(map: ThinkingMap | null | undefined): ThinkingMapSummary {
  const nodes = normalizeNodes(map);
  return {
    total: nodes.length,
    confirmed: nodes.filter((n) => n.status === "confirmed").length,
    tentative: nodes.filter((n) => n.status === "tentative").length,
    unexplored: nodes.filter((n) => n.status === "unexplored").length,
  };
}

export type ThinkingTreeNode = ThinkingNode & { children: ThinkingTreeNode[] };

/**
 * 箇条書きツリーに組み直す。**節は 1 つも捨てない**。
 *
 * 親が地図に居ない / 自分自身 / 循環している場合は**根に上げる** — 捨てると
 * サマリの実数 (話題 N) と画面に出る行数が食い違い、「数えただけ」という誠実さが崩れる。
 * 循環をここで断つのは描画の無限再帰を止めるため (ai-agent 側でも落としているが、
 * 画面は LLM 出力の最後の砦なので二重に持つ)。
 */
export function buildThinkingTree(map: ThinkingMap | null | undefined): ThinkingTreeNode[] {
  // **サマリと同じ正規化済み集合**から組む。ここだけで重複 id を捨てると、
  // 「話題 2」と数えながらツリーは 1 行、という食い違いが出る (Codex P2)。
  const byId = new Map<string, ThinkingTreeNode>();
  for (const node of normalizeNodes(map)) {
    byId.set(node.id, { ...node, children: [] });
  }

  const roots: ThinkingTreeNode[] = [];
  for (const node of byId.values()) {
    const parent = node.parentId === null ? undefined : byId.get(node.parentId);
    if (parent === undefined || parent === node || createsCycle(node, byId)) {
      roots.push(node);
      continue;
    }
    parent.children.push(node);
  }
  return roots;
}

/** node から親を辿って自分に戻るか (= 循環)。 */
function createsCycle(node: ThinkingTreeNode, byId: Map<string, ThinkingTreeNode>): boolean {
  const seen = new Set<string>([node.id]);
  let cursor = node.parentId === null ? undefined : byId.get(node.parentId);
  while (cursor !== undefined) {
    if (seen.has(cursor.id)) return true;
    seen.add(cursor.id);
    cursor = cursor.parentId === null ? undefined : byId.get(cursor.parentId);
  }
  return false;
}
