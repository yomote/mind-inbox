import { Box, useTheme } from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  CENTER_LABEL,
  FONT_SIZE,
  lineBaselineY,
  nodeShapePath,
  type ThinkingGraphBox,
  type ThinkingGraphLayout,
} from "./thinkingMapLayout";
import { KIND_LABEL, STATUS_LABEL, statusStyle } from "./thinkingMapStyle";

type ThinkingMapGraphProps = {
  layout: ThinkingGraphLayout;
  /** 図全体の読み上げ文 (実数サマリと同じ数字を渡す)。 */
  ariaLabel: string;
};

/**
 * 思考整理マップのグラフ描画 (#433 段階 2 / §5.8.1)。
 *
 * **依存を足していない** — 自前の SVG。D3 等のレイアウトライブラリを入れると
 * bundle が数十 KB 増えるうえ、座標が実測 (`getBBox`) 依存になって jsdom で検証できなくなる。
 * ここが描くのは `thinkingMapLayout` が出した座標だけで、**計算はこのファイルに無い**。
 *
 * 2 軸 (裁定 4) の見せ方:
 * - **形 = kind** (話題 = 角丸 / AI の見立て = 錠剤形 / 確かめたいこと = 六角形)
 * - **色と線 = status** (確定 = 実線・濃 / 仮説 = 破線・警告色 / 未探索 = 点線・淡)
 *
 * 中心の「この会話」は**データ上の節ではない** (LLM の生成物ではない器) ので、
 * `thinking-map-node` を名乗らせない — 名乗らせるとサマリの実数より箱が 1 個多く数えられる。
 */
export function ThinkingMapGraph({ layout, ariaLabel }: ThinkingMapGraphProps) {
  const theme = useTheme();

  return (
    // モバイル幅では図が枠より広くなる。**縮小せず横スクロール**にしているのは、
    // 縮小すると 12px の文字が読めない大きさまで潰れるため (崩れないが読めない、を避ける)。
    <Box sx={{ maxWidth: "100%", overflowX: "auto", pb: 0.5 }}>
      <svg
        data-testid="thinking-map-graph"
        data-node-count={layout.nodes.length}
        data-edge-count={layout.edges.length}
        role="img"
        aria-label={ariaLabel}
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        style={{ display: "block" }}
      >
        {layout.edges.map((edge) => {
          const style = statusStyle(theme, edge.status);
          return (
            <path
              key={edge.id}
              data-testid="thinking-map-edge"
              data-edge-status={edge.status}
              d={edge.path}
              fill="none"
              stroke={style.color}
              strokeWidth={style.strokeWidth}
              strokeDasharray={style.dash}
              strokeOpacity={0.7}
            />
          );
        })}

        <g data-testid="thinking-map-center">
          <title>{CENTER_LABEL}</title>
          <path
            d={nodeShapePath(
              null,
              layout.center.x,
              layout.center.y,
              layout.center.width,
              layout.center.height,
            )}
            fill={alpha(theme.palette.primary.main, 0.14)}
            stroke={theme.palette.primary.main}
            strokeWidth={1.5}
          />
          <NodeLabel node={layout.center} color={theme.palette.primary.main} bold />
        </g>

        {layout.nodes.map((node) => {
          const status = node.status;
          const style = statusStyle(theme, status);
          return (
            <g
              key={node.id}
              data-testid="thinking-map-node"
              data-node-kind={node.kind}
              data-node-status={status}
            >
              {/* 図の中身を文字でも辿れるようにする (SVG は読み上げに向かないので、
                  詳細を読む正規の経路は「箇条書き」表示のほう)。 */}
              <title>
                {`${node.source.label} — ${KIND_LABEL[node.kind] ?? "話題"} / ${STATUS_LABEL[status]}`}
              </title>
              <path
                data-testid="thinking-map-node-shape"
                d={nodeShapePath(node.kind, node.x, node.y, node.width, node.height)}
                fill={theme.palette.background.paper}
                stroke={style.color}
                strokeWidth={style.strokeWidth}
                strokeDasharray={style.dash}
              />
              <NodeLabel node={node} color={style.color} bold={status === "confirmed"} />
            </g>
          );
        })}
      </svg>
    </Box>
  );
}

function NodeLabel({
  node,
  color,
  bold,
}: {
  node: ThinkingGraphBox;
  color: string;
  bold?: boolean;
}) {
  return (
    <text
      x={node.x + node.width / 2}
      textAnchor="middle"
      fontSize={FONT_SIZE}
      fontWeight={bold === true ? 700 : 400}
      fill={color}
    >
      {node.lines.map((line, index) => (
        <tspan
          key={`${node.id}-${index}`}
          x={node.x + node.width / 2}
          y={lineBaselineY(node, index)}
        >
          {line}
        </tspan>
      ))}
    </text>
  );
}
