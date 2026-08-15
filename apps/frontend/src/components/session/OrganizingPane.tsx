import * as React from "react";
import { Alert, Button, LinearProgress, Paper, Stack, Tab, Tabs } from "@mui/material";
import AutoAwesomeRoundedIcon from "@mui/icons-material/AutoAwesomeRounded";
import type { ExtractionResult } from "../../api";
import { LivePreviewPane, type PreviewStatus } from "./LivePreviewPane";
import { ThinkingMapPane } from "./ThinkingMapPane";

type OrganizingPaneProps = {
  /** 揮発する下書き + 整理マップ (#187 / #433 / ADR 0039 D1)。null = まだ一度も整理していない。 */
  preview: ExtractionResult | null;
  status: PreviewStatus;
  /** 手動「今すぐ整理」(ADR 0039 D2)。マップと下書きは同じ 1 回の preview で一緒に届く。 */
  onRefresh: () => void;
};

export type OrganizingTab = "map" | "draft";

/**
 * 右ペインの器 (#433 段階 1 / dialogue-session.mdx §5.8)。
 *
 * タブ 2 枚: **「AI の整理」(既定) / 「下書き」** (2026-08-15 PO 裁定 1)。
 * 抽出プレビューは置き換えず併存させる — 下書きカードを消すと「この内容で確定」の
 * 「この内容」が無くなり、確定が再抽出に戻る (ADR 0039 D3 が名指しで避けた事故)。
 * 既定を「AI の整理」にするのは、PO の原意向 (思考の整理過程が見たい) が既定の視界に
 * 来るようにするため。
 *
 * 「今すぐ整理」と更新中 / 失敗の表示をここに置いているのは、**両タブが同じ 1 回の
 * `consultation.preview` の結果**だから (追加の LLM 呼び出しは 0 / 裁定 2)。タブごとに
 * 更新ボタンを持つと「どちらを押したか」で結果が違うように見える。
 */
export function OrganizingPane({ preview, status, onRefresh }: OrganizingPaneProps) {
  const [tab, setTab] = React.useState<OrganizingTab>("map");
  const draftCount = preview?.items.length ?? 0;

  return (
    <Stack spacing={1.5}>
      <Paper sx={{ px: 1.5, py: 1, borderRadius: 3 }}>
        <Stack spacing={1}>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Tabs
              value={tab}
              onChange={(_, next: OrganizingTab) => setTab(next)}
              sx={{ flex: 1, minWidth: 0, minHeight: 0 }}
              variant="scrollable"
              scrollButtons={false}
            >
              <Tab value="map" data-testid="thinking-map-tab" label="AI の整理" />
              <Tab value="draft" data-testid="draft-tab" label={`下書き ${draftCount}`} />
            </Tabs>
            <Button
              size="small"
              variant="outlined"
              startIcon={<AutoAwesomeRoundedIcon />}
              onClick={onRefresh}
              disabled={status === "updating"}
            >
              今すぐ整理
            </Button>
          </Stack>

          {status === "updating" && <LinearProgress aria-label="整理を更新中" />}

          {/* 失敗は右ペイン内に出す (会話は止めない / Snackbar にも出さない / ADR 0018)。
              マップと下書きは同じ 1 回の更新なので、警告もタブに関係なくここに出る。 */}
          {status === "error" && (
            <Alert severity="warning" variant="outlined">
              整理を更新できませんでした。会話はそのまま続けられます。「今すぐ整理」でやり直せます。
            </Alert>
          )}
        </Stack>
      </Paper>

      {/* 表示していないタブは描画しない — display:none で隠すと「DOM にはあるのに
          画面には出ない」状態が生まれ、機械検証が「見えている」と誤読する。 */}
      {tab === "map" ? (
        <ThinkingMapPane map={preview?.thinkingMap ?? null} />
      ) : (
        <LivePreviewPane preview={preview} status={status} />
      )}
    </Stack>
  );
}
