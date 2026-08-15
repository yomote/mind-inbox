import * as React from "react";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import RepeatIcon from "@mui/icons-material/Repeat";
import type { Problem, Theme } from "../../api";
import { STATUS_COLOR, STATUS_LABEL, THEME_OPTIONS, relativeDays } from "./problemShared";

type SortKey = "recent" | "frequent";

type ProblemListScreenProps = {
  problems: Problem[];
  loading: boolean;
  onOpen: (id: string) => void;
  onBackHome: () => void;
};

/**
 * 困りごと一覧（UC-02）。
 * 既定: 直近言及順 + 回数バッジ（Repeat アイコン + N回）で再出現を常時強調 +「よく出る順」トグル。
 * テーマフィルタ + 棚卸し済み（resolved/shelved）の表示トグル。
 * docs/frontend/ui_specs/problem-list.mdx が真実。
 */
export function ProblemListScreen({
  problems,
  loading,
  onOpen,
  onBackHome,
}: ProblemListScreenProps) {
  const [sort, setSort] = React.useState<SortKey>("recent");
  const [themeFilter, setThemeFilter] = React.useState<Theme | "all">("all");
  const [showArchived, setShowArchived] = React.useState(false);

  const visible = React.useMemo(() => {
    let items = problems.filter((p) => (showArchived ? true : p.status === "open"));
    if (themeFilter !== "all") {
      items = items.filter((p) => p.theme === themeFilter);
    }
    items = [...items].sort((a, b) => {
      if (sort === "frequent") {
        if (b.mentionCount !== a.mentionCount) {
          return b.mentionCount - a.mentionCount;
        }
      }
      return a.lastMentionedAt < b.lastMentionedAt ? 1 : -1;
    });
    return items;
  }, [problems, showArchived, themeFilter, sort]);

  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Button variant="text" onClick={onBackHome} sx={{ width: "fit-content" }}>
          ホームへ
        </Button>

        <Stack
          direction="row"
          spacing={1.5}
          useFlexGap
          sx={{ alignItems: "center", flexWrap: "wrap" }}
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={sort}
            onChange={(_, next: SortKey | null) => next && setSort(next)}
          >
            <ToggleButton value="recent">直近順</ToggleButton>
            <ToggleButton value="frequent">よく出る順</ToggleButton>
          </ToggleButtonGroup>

          <TextField
            select
            size="small"
            label="テーマ"
            value={themeFilter}
            onChange={(e) => setThemeFilter(e.target.value as Theme | "all")}
            sx={{ minWidth: 160 }}
          >
            <MenuItem value="all">すべて</MenuItem>
            {THEME_OPTIONS.map((t) => (
              <MenuItem key={t} value={t}>
                {t}
              </MenuItem>
            ))}
          </TextField>

          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={showArchived}
                onChange={(e) => setShowArchived(e.target.checked)}
              />
            }
            label="棚卸し済みも表示"
          />
        </Stack>

        {loading ? (
          <Typography color="text.secondary">読み込み中...</Typography>
        ) : visible.length === 0 ? (
          <Typography color="text.secondary">該当する困りごとはありません。</Typography>
        ) : (
          visible.map((p) => (
            // カードは「押せるもの」なので button にする (#98)。
            // Paper + onClick だとキーボードで到達も実行もできず、支援技術からは
            // ただの箱に見える。aria-label に状態と回数まで載せるのは、読み上げでも
            // 「解決済みか / 何回話したか」が分かるようにするため。
            <Paper
              key={p.id}
              component={ButtonBase}
              variant="outlined"
              aria-label={`${p.title}（${STATUS_LABEL[p.status]}・言及 ${p.mentionCount} 回）`}
              sx={{
                p: 2,
                borderRadius: 2,
                width: "100%",
                display: "block",
                textAlign: "left",
              }}
              onClick={() => onOpen(p.id)}
            >
              <Stack spacing={1}>
                <Stack
                  direction="row"
                  spacing={1}
                  useFlexGap
                  sx={{ alignItems: "center", flexWrap: "wrap" }}
                >
                  <Typography sx={{ fontWeight: 700, flex: 1, minWidth: 0 }}>{p.title}</Typography>
                  {p.mentionCount > 1 && (
                    <Chip
                      size="small"
                      color="warning"
                      // data-testid は明示する — MUI の自動付与 (アイコン名 + "Icon") は
                      // production ビルドで剥がれるため、E2E (production 配信) で検証できない
                      icon={<RepeatIcon data-testid="RepeatIcon" />}
                      label={`${p.mentionCount}回`}
                    />
                  )}
                  <Chip
                    size="small"
                    color={STATUS_COLOR[p.status]}
                    label={STATUS_LABEL[p.status]}
                  />
                </Stack>

                <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                  <Chip size="small" variant="outlined" label={p.theme} />
                  <Typography variant="caption" color="text.secondary">
                    最終言及 {relativeDays(p.lastMentionedAt)}
                  </Typography>
                </Stack>

                <Typography variant="body2" color="text.secondary">
                  {p.summary}
                </Typography>
              </Stack>
            </Paper>
          ))
        )}

        <Box />
      </Stack>
    </Paper>
  );
}
