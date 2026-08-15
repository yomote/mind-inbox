import { Box, Chip, Stack, Typography } from "@mui/material";
import type { ThinkingTreeNode } from "./thinkingMap";
import { KIND_LABEL, STATUS_COLOR, STATUS_LABEL, STATUS_MARKER } from "./thinkingMapStyle";

/**
 * 箇条書きツリー表示 (#433 段階 1 / §5.8.1)。
 *
 * **段階 2 (グラフ描画 / `ThinkingMapGraph`) が入っても消さない。** ここは
 * (1) 図が描けないときの落とし先、(2) 図を読めない・読みたくないときの選択肢、の 2 役で、
 * SVG は本質的に読み上げに向かないので、**同じ内容へ到達できる文字の経路**を必ず残す。
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
        <Typography component="span" sx={{ color: STATUS_COLOR[node.status], lineHeight: 1.6 }}>
          {STATUS_MARKER[node.status]}
        </Typography>
        <Typography
          variant="body2"
          sx={{
            color: STATUS_COLOR[node.status],
            fontWeight: node.status === "confirmed" ? 600 : 400,
          }}
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
