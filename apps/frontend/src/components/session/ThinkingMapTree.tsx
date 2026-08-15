import { Box, Chip, Stack, Typography } from "@mui/material";
import type { ThinkingTreeNode } from "./thinkingMap";
import { KIND_LABEL, STATUS_COLOR, STATUS_LABEL, STATUS_MARKER } from "./thinkingMapStyle";

/**
 * 箇条書きツリー表示 (#433 段階 1 / §5.8.1)。
 *
 * **段階 2 (グラフ描画 / `ThinkingMapGraph`) が入っても消さない。** ここは
 * (1) 図が描けないときの落とし先、(2) 図を読めない・読みたくないときの選択肢、の 2 役で、
 * SVG は本質的に読み上げに向かないので、**同じ内容へ到達できる文字の経路**を必ず残す。
 *
 * **ラベル本文は status で色を変えない** (Codex P2 / WCAG 1.4.3)。ここは
 * 「図が読めない人の経路」なので、その経路の本文が 4.5:1 を割っていたら意味がない。
 * status は記号 (●/◐/○) とチップの文字が持つ。
 */
export function ThinkingMapTree({ roots }: { roots: ThinkingTreeNode[] }) {
  return (
    <Box component="ul" data-testid="thinking-map-tree" sx={{ listStyle: "none", pl: 0, m: 0 }}>
      {roots.map((node) => (
        <TreeNode key={node.id} node={node} />
      ))}
    </Box>
  );
}

function TreeNode({ node }: { node: ThinkingTreeNode }) {
  return (
    <Box
      component="li"
      data-testid="thinking-map-node"
      data-node-status={node.status}
      data-node-kind={node.kind}
    >
      <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
        {/* 記号は status 色のまま (チップの文字と重複する冗長な印なので、色が落ちても
            情報は失われない)。`unexplored` を `text.disabled` から `text.secondary` へ
            上げてあるのは、非テキストの UI 部品にも 3:1 が要るため (WCAG 1.4.11)。 */}
        <Typography component="span" sx={{ color: STATUS_COLOR[node.status], lineHeight: 1.6 }}>
          {STATUS_MARKER[node.status]}
        </Typography>
        <Typography
          variant="body2"
          data-testid="thinking-map-node-label"
          sx={{ fontWeight: node.status === "confirmed" ? 600 : 400 }}
        >
          {node.label}
        </Typography>
        <Chip size="small" variant="outlined" label={STATUS_LABEL[node.status]} />
        {KIND_LABEL[node.kind] && (
          <Chip size="small" variant="outlined" color="info" label={KIND_LABEL[node.kind]} />
        )}
      </Stack>

      {node.children.length > 0 && (
        <Box component="ul" sx={{ listStyle: "none", pl: 2, m: 0 }}>
          {node.children.map((child) => (
            <TreeNode key={child.id} node={child} />
          ))}
        </Box>
      )}
    </Box>
  );
}
