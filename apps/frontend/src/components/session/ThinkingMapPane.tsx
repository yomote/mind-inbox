import * as React from "react";
import { Paper, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import type { ThinkingMap } from "../../api";
import { buildThinkingTree, summarizeThinkingMap } from "./thinkingMap";
import { graphFallbackReason, layoutThinkingGraph } from "./thinkingMapLayout";
import { KIND_SHAPE_LABEL, STATUS_LABEL, STATUS_MARKER } from "./thinkingMapStyle";
import { ThinkingMapGraph } from "./ThinkingMapGraph";
import { ThinkingMapTree } from "./ThinkingMapTree";

type ThinkingMapPaneProps = {
  /** 揮発する整理マップ (#433 / ADR 0039 D1 と同じく保存しない)。null = まだ整理していない。 */
  map: ThinkingMap | null;
};

export type ThinkingMapView = "graph" | "tree";

/**
 * 「AI の整理」ペイン (#433 段階 1 + 段階 2 / dialogue-session.mdx §5.8)。
 *
 * PO の原意向は「AI がどう思っているか、思考の整理過程が見たい」。ここは**成果物**
 * (下書きカード) ではなく **AI の作業机**で、読み取り専用・保存されない。
 *
 * **% を出さない**: 「整理度 68%」の分母 = 「悩みが全部出きった状態」を誰も測っていない
 * ので、率は測定ではなく生成になる (2026-08-15 PO 裁定 3)。出すのは「数えただけ」の
 * 実数と、終わりの**条件**だけ。プログレスバーも置かない — 会話が進むと枝は増えるので、
 * 減る前提のバーは「あと少し」と嘘をつく。
 *
 * **表示は 2 通り (段階 2 / 2026-08-15 PO GO)**: 既定は図 (マインドマップ)、もう一方が
 * 段階 1 の箇条書き。箇条書きは**落とし先であり選択肢でもある** — SVG は読み上げに
 * 向かないので、同じ内容へ文字で到達できる経路を常に残す。図にできない状態
 * (節 0 / 節が多すぎる / 座標が壊れている) では**自動で箇条書きに落ち、理由を画面に出す**
 * (描けなかったことを黙って空白にしない)。
 */
export function ThinkingMapPane({ map }: ThinkingMapPaneProps) {
  const summary = summarizeThinkingMap(map);
  const roots = buildThinkingTree(map);
  const layout = layoutThinkingGraph(roots);
  const fallbackReason = graphFallbackReason(layout, summary.total);

  const [preferredView, setPreferredView] = React.useState<ThinkingMapView>("graph");
  // 図にできないときは希望に関わらず箇条書き。**希望そのものは書き換えない** —
  // 書き換えると、節が減って図に戻せる状態になっても箇条書きのまま貼り付く。
  const view: ThinkingMapView = fallbackReason === null ? preferredView : "tree";

  return (
    <Paper
      data-testid="thinking-map-pane"
      data-node-count={summary.total}
      data-unexplored-count={summary.unexplored}
      data-view={summary.total === 0 ? "empty" : view}
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
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              justifyContent="space-between"
              flexWrap="wrap"
              useFlexGap
            >
              <Typography variant="body2" fontWeight={600} data-testid="thinking-map-summary">
                話題 {summary.total} / 確定 {summary.confirmed}・仮説 {summary.tentative}・未探索{" "}
                {summary.unexplored}
              </Typography>

              <ToggleButtonGroup
                size="small"
                exclusive
                value={view}
                aria-label="整理マップの表示方法"
                onChange={(_, next: ThinkingMapView | null) => {
                  if (next !== null) setPreferredView(next);
                }}
              >
                <ToggleButton
                  value="graph"
                  data-testid="thinking-map-view-graph"
                  disabled={fallbackReason !== null}
                >
                  図
                </ToggleButton>
                <ToggleButton value="tree" data-testid="thinking-map-view-tree">
                  箇条書き
                </ToggleButton>
              </ToggleButtonGroup>
            </Stack>

            {/* 落ちた事実と理由を出す。黙って箇条書きになると、
                「図が出ない」のか「図で出すほどの中身が無い」のか読み手に区別できない。 */}
            {fallbackReason !== null && (
              <Typography
                variant="caption"
                color="text.secondary"
                data-testid="thinking-map-fallback-note"
              >
                図にできなかったので箇条書きで出しています ({fallbackReason})。
              </Typography>
            )}

            {view === "graph" ? (
              <>
                <ThinkingMapGraph
                  layout={layout}
                  ariaLabel={`AI の整理の図。話題 ${summary.total}、確定 ${summary.confirmed}、仮説 ${summary.tentative}、未探索 ${summary.unexplored}。詳しくは「箇条書き」表示で読めます`}
                />
                <Typography variant="caption" color="text.secondary">
                  線と色 = 確かさ ({STATUS_MARKER.confirmed} {STATUS_LABEL.confirmed} 実線 /{" "}
                  {STATUS_MARKER.tentative} {STATUS_LABEL.tentative} 破線 /{" "}
                  {STATUS_MARKER.unexplored} {STATUS_LABEL.unexplored} 点線)、形 = 種類 (
                  {KIND_SHAPE_LABEL.topic} / {KIND_SHAPE_LABEL.hypothesis} /{" "}
                  {KIND_SHAPE_LABEL.unknown})
                </Typography>
              </>
            ) : (
              <ThinkingMapTree roots={roots} />
            )}

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
