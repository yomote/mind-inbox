import { Box, Button, Chip, Divider, Paper, Stack, Typography } from "@mui/material";
import FiberNewIcon from "@mui/icons-material/FiberNew";
import RepeatIcon from "@mui/icons-material/Repeat";
import type { ExtractionResult } from "../../api";
import { AFFECT_COLOR } from "./problemShared";

type ExtractReviewScreenProps = {
  extraction: ExtractionResult;
  loading: boolean;
  onDismiss: (problemId: string) => void;
  onGoToList: () => void;
};

/**
 * 抽出結果レビュー（UC-01 の段1+段2 の提示）。
 * 「N件見つけた」+ 新規（FiberNew）/ 既存に追加（Repeat）+ 再出現の気づき + その場トリアージ（却下）。
 * docs/frontend/ui_specs/extract-review.mdx が真実。
 */
export function ExtractReviewScreen({
  extraction,
  loading,
  onDismiss,
  onGoToList,
}: ExtractReviewScreenProps) {
  const count = extraction.items.length;

  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        {count === 0 ? (
          <Typography color="text.secondary">
            今回の話からは、追跡する困りごとは見つかりませんでした。受け止めました。
          </Typography>
        ) : (
          <>
            <Typography sx={{ fontWeight: 700 }}>
              今回の話から {count} 件の困りごとを見つけました
            </Typography>
            <Typography variant="body2" color="text.secondary">
              新規 {extraction.newProblemCount} 件 / 既存に追加 {extraction.updatedProblemCount} 件
            </Typography>

            <Stack spacing={1.5}>
              {extraction.items.map((item) => {
                const { mention, grouping } = item;
                return (
                  <Paper key={mention.id} variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
                    <Stack spacing={1}>
                      <Stack
                        direction="row"
                        spacing={1}
                        useFlexGap
                        sx={{ alignItems: "center", flexWrap: "wrap" }}
                      >
                        {grouping.kind === "new" ? (
                          <Chip
                            size="small"
                            color="primary"
                            // data-testid は明示する — MUI の自動付与は production ビルドで
                            // 剥がれるため、E2E (production 配信) で検証できない
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
                          <Chip
                            size="small"
                            color="warning"
                            icon={<RepeatIcon data-testid="RepeatIcon" />}
                            label={`${grouping.mentionCount}回目`}
                          />
                        )}
                        {grouping.reignited && <Chip size="small" color="warning" label="再燃" />}
                      </Stack>

                      <Typography sx={{ fontWeight: 600 }}>{grouping.problemTitle}</Typography>
                      <Typography variant="body2">{mention.statement}</Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ fontStyle: "italic" }}
                      >
                        「{mention.excerpt}」
                      </Typography>

                      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                        <Chip
                          size="small"
                          variant="outlined"
                          color={AFFECT_COLOR[mention.affect.valence]}
                          label={mention.affect.label}
                        />
                        {grouping.isRecurrence && (
                          <Typography variant="caption" color="warning.main">
                            この困りごとは繰り返し話しています
                          </Typography>
                        )}
                      </Stack>

                      {grouping.kind === "new" && (
                        <Box>
                          <Button
                            size="small"
                            color="inherit"
                            disabled={loading}
                            onClick={() => onDismiss(grouping.problemId)}
                          >
                            これは違う（却下）
                          </Button>
                        </Box>
                      )}
                    </Stack>
                  </Paper>
                );
              })}
            </Stack>

            <Divider />
          </>
        )}

        <Button variant="contained" onClick={onGoToList} disabled={loading}>
          困りごと一覧へ
        </Button>
      </Stack>
    </Paper>
  );
}
