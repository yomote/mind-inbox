import { Alert, Box, Button, Chip, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import FiberNewIcon from "@mui/icons-material/FiberNew";
import RepeatIcon from "@mui/icons-material/Repeat";
import AutoAwesomeRoundedIcon from "@mui/icons-material/AutoAwesomeRounded";
import type { ExtractionResult } from "../../api";
import { AFFECT_COLOR } from "../screens/problemShared";

export type PreviewStatus = "idle" | "updating" | "error";

type LivePreviewPaneProps = {
  /** 揮発する下書き (#187 / ADR 0039 D1)。null = まだ一度も整理していない。 */
  preview: ExtractionResult | null;
  status: PreviewStatus;
  /** 手動「今すぐ整理」(ADR 0039 D2)。 */
  onRefresh: () => void;
};

/**
 * 「整理されつつある困りごと」ペイン (#187 / ADR 0039 / dialogue-session.mdx §5.8)。
 *
 * 読み取り専用の下書き表示 — ここに出ているものはまだどこにも保存されていない。
 * カードの見た目は extract-review.mdx のカードの縮約版 (確定前の予告編なので
 * トリアージ操作は持たない)。
 */
export function LivePreviewPane({ preview, status, onRefresh }: LivePreviewPaneProps) {
  const items = preview?.items ?? [];

  return (
    <Paper
      data-testid="live-preview-pane"
      data-preview-count={items.length}
      sx={{ p: 2.5, borderRadius: 3 }}
    >
      <Stack spacing={1.5}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography fontWeight={700} sx={{ flex: 1 }}>
            整理されつつある困りごと
          </Typography>
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

        <Typography variant="caption" color="text.secondary">
          確定していない下書きです — 確定せずにセッションを閉じると消えます
        </Typography>

        {status === "updating" && <LinearProgress aria-label="下書きを更新中" />}

        {status === "error" && (
          <Alert severity="warning" variant="outlined">
            下書きを更新できませんでした。会話はそのまま続けられます。「今すぐ整理」でやり直せます。
          </Alert>
        )}

        {items.length === 0 && status !== "updating" ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            会話が進むと、AI が掴んだ困りごとの下書きがここに現れます。
          </Typography>
        ) : (
          <Stack spacing={1}>
            {items.map(({ mention, grouping }) => (
              <Paper
                key={mention.id}
                data-testid="preview-card"
                variant="outlined"
                sx={{ p: 1.5, borderRadius: 2 }}
              >
                <Stack spacing={0.75}>
                  <Stack
                    direction="row"
                    spacing={0.75}
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    {grouping.kind === "new" ? (
                      <Chip
                        size="small"
                        color="primary"
                        icon={<FiberNewIcon data-testid="FiberNewIcon" />}
                        label="新規"
                      />
                    ) : (
                      <Chip
                        size="small"
                        color="secondary"
                        icon={<RepeatIcon data-testid="RepeatIcon" />}
                        label="既存に追加"
                      />
                    )}
                    <Chip size="small" label={grouping.problemTheme} />
                    {grouping.isRecurrence && (
                      <Chip size="small" color="warning" label={`${grouping.mentionCount}回目`} />
                    )}
                  </Stack>

                  <Typography variant="body2" fontWeight={600}>
                    {grouping.problemTitle}
                  </Typography>
                  <Typography variant="body2">{mention.statement}</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ fontStyle: "italic" }}>
                    「{mention.excerpt}」
                  </Typography>
                  <Box>
                    <Chip
                      size="small"
                      variant="outlined"
                      color={AFFECT_COLOR[mention.affect.valence]}
                      label={mention.affect.label}
                    />
                  </Box>
                </Stack>
              </Paper>
            ))}
          </Stack>
        )}

        {items.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            「この内容で確定」を押すと、この下書きの内容が困りごととして保存されます
          </Typography>
        )}
      </Stack>
    </Paper>
  );
}
