/**
 * 「AI の整理」(思考整理マップ) のグラフ配置 (#433 段階 2 / §5.8.1)。
 *
 * **描画 (ThinkingMapGraph) と分けてあるのは、座標がここだけで決まるため。**
 * jsdom には `getBBox` が無く、実測に頼った配置はテストで検証できない。ここは
 * **文字数からの見積もりだけで座標を出す純関数**にしてあるので、ノード数・辺の数・
 * 重なりの有無をブラウザ無しで assert できる。
 *
 * 段階 1 (箇条書きツリー / `thinkingMap.ts`) の集計・木組みはそのまま使う。
 * ここは「木をどこに置くか」だけを足す層で、**節は 1 つも捨てない**
 * (捨てるとサマリの実数「話題 N」と図の中の箱の数が食い違い、気づけない)。
 */

import type { ThinkingNodeKind, ThinkingNodeStatus } from "../../api";
import type { ThinkingTreeNode } from "./thinkingMap";

/** 箱の幅は全ノード共通 (列が揃い、深さから幅が決まるので座標が予測可能になる)。 */
export const NODE_WIDTH = 114;
export const FONT_SIZE = 12;
const LINE_HEIGHT = 15;
/** 1 行に入れる文字幅 (全角 = 1.0 / 半角 = 0.5 で数える)。 */
const MAX_UNITS_PER_LINE = 8;
const MAX_LINES = 2;
const NODE_PAD_Y = 6;
const COLUMN_GAP = 34;
/** 行の間隔。2 行ラベルの箱 (42px) が隣と接触しない値にしてある。 */
const ROW_STRIDE = 52;
const CANVAS_PAD = 10;

/** 中心の節はデータではなく「この会話そのもの」を表す器。LLM の生成物ではない。 */
export const CENTER_ID = "__conversation__";
export const CENTER_LABEL = "この会話";

/**
 * 図にする節数の上限。**超えたら箇条書きへ落とす** (段階 1 の資産は消さない)。
 *
 * 上限が無いと、LLM が節を大量に吐いたときに図の高さが青天井に伸び、
 * 「描画はされているが誰にも読めない」状態になる。読めないことは画面に出ないので気づけない。
 */
export const MAX_GRAPH_NODES = 40;

/** 図の上の箱 (中心もデータ上の節も共通で持つ幾何)。 */
export type ThinkingGraphBox = {
  id: string;
  /** 折り返し済みのラベル (最大 2 行 / 溢れは「…」で切る)。 */
  lines: string[];
  x: number;
  y: number;
  width: number;
  height: number;
};

/**
 * データ上の節の箱。**kind / status を必ず持つ** (null を許すと描画側で
 * 「status が無いときの既定色」という握り潰しが生まれ、割り当ての事故が色に化ける)。
 */
export type ThinkingGraphNode = ThinkingGraphBox & {
  source: ThinkingTreeNode;
  kind: ThinkingNodeKind;
  status: ThinkingNodeStatus;
};

export type ThinkingGraphEdge = {
  id: string;
  fromId: string;
  toId: string;
  /** SVG の `d`。曲線にしているのは枝が広がる見え方 (マインドマップ) に寄せるため。 */
  path: string;
  /** 線の見た目は**子側の status** で決まる (未探索へ伸びる枝は点線)。 */
  status: ThinkingNodeStatus;
};

export type ThinkingGraphLayout = {
  width: number;
  height: number;
  /** 「この会話」の箱。**データ上の節ではない**ので kind / status を持たない。 */
  center: ThinkingGraphBox;
  /** **データ上の節だけ** (中心は含まない)。数はサマリの「話題 N」と一致する。 */
  nodes: ThinkingGraphNode[];
  edges: ThinkingGraphEdge[];
};

/** 半角は 0.5 文字ぶんとして数える (日本語と英数字が混ざったラベルで幅が破綻しないように)。 */
function charUnits(char: string): number {
  return /[ -~｡-ﾟ]/.test(char) ? 0.5 : 1;
}

/**
 * ラベルを最大 2 行に折り返す。**溢れたら「…」で切る** (箱の幅は固定なので、
 * 切らないと隣の列へはみ出して重なる)。
 */
export function wrapLabel(label: string, maxLines = MAX_LINES): string[] {
  const lines: string[] = [];
  let line = "";
  let units = 0;

  for (const char of label) {
    const w = charUnits(char);
    if (units + w > MAX_UNITS_PER_LINE && line !== "") {
      lines.push(line);
      line = "";
      units = 0;
      if (lines.length === maxLines) break;
    }
    line += char;
    units += w;
  }

  if (lines.length < maxLines && line !== "") lines.push(line);

  // 全部は入り切らなかった → 最後の行の末尾を「…」に置き換える (末尾 1 文字ぶんを譲る)。
  const consumed = lines.join("");
  if (consumed.length < [...label].length && lines.length > 0) {
    const last = [...lines[lines.length - 1]];
    lines[lines.length - 1] = `${last.slice(0, Math.max(0, last.length - 1)).join("")}…`;
  }

  return lines.length > 0 ? lines : [label];
}

function nodeHeight(lines: string[]): number {
  return lines.length * LINE_HEIGHT + NODE_PAD_Y * 2;
}

/** 箱の中の i 行目のベースライン (描画側で行送りを再計算させない)。 */
export function lineBaselineY(box: ThinkingGraphBox, index: number): number {
  return box.y + NODE_PAD_Y + LINE_HEIGHT * index + FONT_SIZE - 1;
}

/**
 * 木を左から右へ広がるノードリンク図に置く (中心 = 「この会話」)。
 *
 * 配置は tidy tree の素朴版: **葉を上から順に 1 行ずつ占有させ、親は子の行の中央に置く**。
 * 行を共有しないので**重ならないことが構造から保証される** (実測に頼らない)。
 * 放射 (円形) ではなく横方向にしたのは、右ペインが縦に長い枠で、
 * モバイル幅では円形だとラベルが必ず重なるため。
 */
export function layoutThinkingGraph(roots: ThinkingTreeNode[]): ThinkingGraphLayout {
  const placed: ThinkingGraphNode[] = [];
  // 枝は**組み上がった木の親子**から張る (節の `parentId` からではない)。
  // `buildThinkingTree` は親を辿れない / 循環している節を根へ上げるが `parentId` は残すので、
  // parentId を信じると「根なのに親から線が出ている」図になり、木と図が食い違う。
  const edgeSpecs: { fromId: string; toId: string; status: ThinkingNodeStatus }[] = [];
  let nextLeafRow = 0;
  let maxDepth = 0;

  const visit = (node: ThinkingTreeNode, depth: number, parentId: string): number => {
    edgeSpecs.push({ fromId: parentId, toId: node.id, status: node.status });
    maxDepth = Math.max(maxDepth, depth);
    // **先に自分の枠を予約する** — 配列の順序を木の順序 (親 → 子) に合わせるため。
    // 順序が崩れると、各節の kind / status を並びで検証しているテストが意味を失う。
    const slot = placed.length;
    placed.push(null as unknown as ThinkingGraphNode);

    let row: number;
    if (node.children.length === 0) {
      row = nextLeafRow;
      nextLeafRow += 1;
    } else {
      const childRows = node.children.map((child) => visit(child, depth + 1, node.id));
      row = (childRows[0] + childRows[childRows.length - 1]) / 2;
    }

    const lines = wrapLabel(node.label);
    const height = nodeHeight(lines);
    placed[slot] = {
      id: node.id,
      source: node,
      kind: node.kind,
      status: node.status,
      lines,
      x: CANVAS_PAD + depth * (NODE_WIDTH + COLUMN_GAP),
      y: rowCenterY(row) - height / 2,
      width: NODE_WIDTH,
      height,
    };
    return row;
  };

  const rootRows = roots.map((root) => visit(root, 1, CENTER_ID));

  const centerRow = rootRows.length === 0 ? 0 : (rootRows[0] + rootRows[rootRows.length - 1]) / 2;
  const centerLines = wrapLabel(CENTER_LABEL);
  const centerHeight = nodeHeight(centerLines);
  const center: ThinkingGraphBox = {
    id: CENTER_ID,
    lines: centerLines,
    x: CANVAS_PAD,
    y: rowCenterY(centerRow) - centerHeight / 2,
    width: NODE_WIDTH,
    height: centerHeight,
  };

  // **すべての節にちょうど 1 本の入り枝**を張る (根は中心から生やす)。
  // 枝を張らずに浮かせると、図の上で「どこにも繋がらない箱」になり、
  // 親を取りこぼしたのか根なのかが読み手に区別できない。
  const byId = new Map<string, ThinkingGraphBox>([center, ...placed].map((box) => [box.id, box]));
  const edges: ThinkingGraphEdge[] = [];
  for (const spec of edgeSpecs) {
    const from = byId.get(spec.fromId);
    const to = byId.get(spec.toId);
    if (from === undefined || to === undefined) continue;
    edges.push({
      id: `${spec.fromId}->${spec.toId}`,
      fromId: spec.fromId,
      toId: spec.toId,
      path: edgePath(from, to),
      // 枝の見た目は**行き先の status**。未探索の枝が点線で見えることが
      // 「どこがまだ聞けていないか」の一次情報になる。
      status: spec.status,
    });
  }

  return {
    width: CANVAS_PAD * 2 + (maxDepth + 1) * NODE_WIDTH + maxDepth * COLUMN_GAP,
    height: CANVAS_PAD * 2 + Math.max(nextLeafRow, 1) * ROW_STRIDE,
    center,
    nodes: placed,
    edges,
  };
}

function rowCenterY(row: number): number {
  return CANVAS_PAD + row * ROW_STRIDE + ROW_STRIDE / 2;
}

function edgePath(from: ThinkingGraphBox, to: ThinkingGraphBox): string {
  const x1 = from.x + from.width;
  const y1 = from.y + from.height / 2;
  const x2 = to.x;
  const y2 = to.y + to.height / 2;
  const dx = Math.max((x2 - x1) / 2, 8);
  return `M ${round(x1)} ${round(y1)} C ${round(x1 + dx)} ${round(y1)}, ${round(x2 - dx)} ${round(y2)}, ${round(x2)} ${round(y2)}`;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/** 箱の形は **kind** (それが何か) で変える。確かさ (status) は線と色が持つ。 */
export function nodeShapePath(
  kind: ThinkingNodeKind | null,
  x: number,
  y: number,
  w: number,
  h: number,
): string {
  if (kind === "hypothesis") {
    // AI の見立て = 角丸を最大まで丸めた錠剤形 (断定していないことを形で出す)。
    return roundedRectPath(x, y, w, h, h / 2);
  }
  if (kind === "unknown") {
    // 確かめたいこと = 左右が尖った六角形 (話題とも見立てとも違う「穴」)。
    const notch = Math.min(12, w / 4);
    return [
      `M ${round(x + notch)} ${round(y)}`,
      `L ${round(x + w - notch)} ${round(y)}`,
      `L ${round(x + w)} ${round(y + h / 2)}`,
      `L ${round(x + w - notch)} ${round(y + h)}`,
      `L ${round(x + notch)} ${round(y + h)}`,
      `L ${round(x)} ${round(y + h / 2)}`,
      "Z",
    ].join(" ");
  }
  // 話題 (既定) と中心 = 角丸長方形。
  return roundedRectPath(x, y, w, h, 8);
}

function roundedRectPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, w / 2, h / 2);
  return [
    `M ${round(x + rr)} ${round(y)}`,
    `H ${round(x + w - rr)}`,
    `A ${round(rr)} ${round(rr)} 0 0 1 ${round(x + w)} ${round(y + rr)}`,
    `V ${round(y + h - rr)}`,
    `A ${round(rr)} ${round(rr)} 0 0 1 ${round(x + w - rr)} ${round(y + h)}`,
    `H ${round(x + rr)}`,
    `A ${round(rr)} ${round(rr)} 0 0 1 ${round(x)} ${round(y + h - rr)}`,
    `V ${round(y + rr)}`,
    `A ${round(rr)} ${round(rr)} 0 0 1 ${round(x + rr)} ${round(y)}`,
    "Z",
  ].join(" ");
}

/**
 * 図にできない理由。null = 図で出してよい。
 *
 * **「描けなかった」を黙って空白にしない**ための判定 (CLAUDE.md / 取れなかったものを
 * 「異常なし」と書かない)。ここが null を返し続けるようになると、節を取りこぼした図が
 * そのまま出て、サマリの実数と図の中身が食い違ったまま気づけなくなる。
 */
export function graphFallbackReason(
  layout: ThinkingGraphLayout,
  expectedNodeCount: number,
): string | null {
  if (expectedNodeCount === 0) return "図にする節がないため";
  if (expectedNodeCount > MAX_GRAPH_NODES) {
    return `節が多すぎるため (${expectedNodeCount} 個 / 図にできるのは ${MAX_GRAPH_NODES} 個まで)`;
  }
  if (layout.nodes.length !== expectedNodeCount) {
    // 図に置けなかった節がある = 「話題 N」と箱の数が食い違う状態。図では出さない。
    return `図に置けなかった節があるため (${layout.nodes.length} / ${expectedNodeCount} 個)`;
  }
  if (layout.edges.length !== layout.nodes.length) {
    // 節 1 つにつき入り枝 1 本。欠けている = どこに繋がるか分からない箱が浮いている。
    return `枝を張れなかった節があるため (枝 ${layout.edges.length} / 節 ${layout.nodes.length})`;
  }
  const coordinates = [
    layout.width,
    layout.height,
    ...layout.nodes.flatMap((node) => [node.x, node.y, node.width, node.height]),
  ];
  if (coordinates.some((value) => !Number.isFinite(value))) {
    return "図の座標を計算できなかったため";
  }
  return null;
}
