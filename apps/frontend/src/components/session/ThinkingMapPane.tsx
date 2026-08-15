import { Box, Chip, Paper, Stack, Typography } from "@mui/material";
import type { ThinkingMap, ThinkingNodeKind, ThinkingNodeStatus } from "../../api";
import { buildThinkingTree, summarizeThinkingMap, type ThinkingTreeNode } from "./thinkingMap";

type ThinkingMapPaneProps = {
  /** 揮発する整理マップ (#433 / ADR 0039 D1 と同じく保存しない)。null = まだ整理していない。 */
  map: ThinkingMap | null;
};

/** status = どれだけ確かか。色は「濃さ」であって数値ではない (定量の顔をさせない)。 */
const STATUS_LABEL: Record<ThinkingNodeStatus, string> = {
  confirmed: "確定",
  tentative: "仮説",
  unexplored: "未探索",
};

const STATUS_COLOR: Record<ThinkingNodeStatus, string> = {
  confirmed: "text.primary",
  tentative: "warning.main",
  unexplored: "text.disabled",
};

const STATUS_MARKER: Record<ThinkingNodeStatus, string> = {
  confirmed: "●",
  tentative: "◐",
  unexplored: "○",
};

/**
 * kind = それが何か。topic は既定なのでチップを出さない (行が読みづらくなる)。
 * status の語 (確定 / 仮説 / 未探索) と重ならない言葉を選ぶ — 「未探索」の隣に
 * 「聞けていない」を並べても情報が増えず、2 軸あることが伝わらない。
 */
const KIND_LABEL: Partial<Record<ThinkingNodeKind, string>> = {
  hypothesis: "AI の見立て",
  unknown: "確かめたいこと",
};

/**
 * 「AI の整理」ペイン (#433 段階 1 / dialogue-session.mdx §5.8)。
 *
 * PO の原意向は「AI がどう思っているか、思考の整理過程が見たい」。ここは**成果物**
 * (下書きカード) ではなく **AI の作業机**で、読み取り専用・保存されない。
 *
 * **% を出さない**: 「整理度 68%」の分母 = 「悩みが全部出きった状態」を誰も測っていない
 * ので、率は測定ではなく生成になる (2026-08-15 PO 裁定 3)。出すのは「数えただけ」の
 * 実数と、終わりの**条件**だけ。プログレスバーも置かない — 会話が進むと枝は増えるので、
 * 減る前提のバーは「あと少し」と嘘をつく。
 */
export function ThinkingMapPane({ map }: ThinkingMapPaneProps) {
  const summary = summarizeThinkingMap(map);
  const roots = buildThinkingTree(map);

  return (
    <Paper
      data-testid="thinking-map-pane"
      data-node-count={summary.total}
      data-unexplored-count={summary.unexplored}
      sx={{ p: 2.5, borderRadius: 3 }}
    >
      <Stack spacing={1.5}>
        <Typography variant="caption" color="text.secondary">
          AI が今この会話をこう捉えています。違っていたら会話で直してください
        </Typography>

        {summary.total === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            会話が進むと、AI が今どう整理しているかがここに現れます。
          </Typography>
        ) : (
          <>
            <Typography variant="body2" fontWeight={600} data-testid="thinking-map-summary">
              話題 {summary.total} / 確定 {summary.confirmed}・仮説 {summary.tentative}・未探索{" "}
              {summary.unexplored}
            </Typography>

            <Box component="ul" sx={{ listStyle: "none", pl: 0, m: 0 }}>
              {roots.map((node) => (
                <TreeNode key={node.id} node={node} />
              ))}
            </Box>

            <Typography variant="caption" color="text.secondary">
              未探索の枝が 0 になったら一区切りです (いま {summary.unexplored} 本)。
              会話が進むと枝は増えることもあります。「この内容で確定」はいつでも押せます。
            </Typography>
          </>
        )}
      </Stack>
    </Paper>
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
