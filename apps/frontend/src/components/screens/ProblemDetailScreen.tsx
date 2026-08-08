import * as React from "react";
import {
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import EditRoundedIcon from "@mui/icons-material/EditRounded";
import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import ArchiveRoundedIcon from "@mui/icons-material/ArchiveRounded";
import ReplayRoundedIcon from "@mui/icons-material/ReplayRounded";
import type { Problem, Theme, TriageInput } from "../../api";
import {
  AFFECT_COLOR,
  STATUS_COLOR,
  STATUS_LABEL,
  THEME_OPTIONS,
  formatDate,
} from "./problemShared";

type ProblemDetailScreenProps = {
  problem: Problem;
  loading: boolean;
  onBack: () => void;
  onTriage: (input: TriageInput) => void;
  onCreatePlan?: (problemId: string) => void;
};

/**
 * 困りごと詳細（UC-03/04/05）。
 * 主役は Mention タイムライン（日付 + excerpt + affect）= 再出現履歴 + 感情推移。
 * 棚卸し（解決 / もう気にしない / 再オープン）+ テーマ・タイトル編集（トリアージ）+ プラン。
 * docs/frontend/ui_specs/problem-detail.mdx が真実。
 */
export function ProblemDetailScreen({
  problem,
  loading,
  onBack,
  onTriage,
  onCreatePlan,
}: ProblemDetailScreenProps) {
  const [editingTitle, setEditingTitle] = React.useState(false);
  const [titleDraft, setTitleDraft] = React.useState(problem.title);
  // 別の Problem / 編集確定でタイトルが変わったら、編集ドラフトを描画時に同期する
  // （effect での setState を避ける: React 推奨の「前回値からの調整」パターン）。
  const syncKey = `${problem.id}:${problem.title}`;
  const [syncedKey, setSyncedKey] = React.useState(syncKey);
  if (syncedKey !== syncKey) {
    setSyncedKey(syncKey);
    setTitleDraft(problem.title);
    setEditingTitle(false);
  }

  const timeline = React.useMemo(
    () => [...problem.mentions].sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1)),
    [problem.mentions],
  );

  const saveTitle = () => {
    if (titleDraft.trim() && titleDraft.trim() !== problem.title) {
      onTriage({ action: "editTitle", problemId: problem.id, title: titleDraft });
    }
    setEditingTitle(false);
  };

  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Button variant="text" onClick={onBack} sx={{ width: "fit-content" }}>
          一覧へ
        </Button>

        {/* ── タイトル（編集可）+ 状態 ───────────────────────────── */}
        {editingTitle ? (
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              fullWidth
              size="small"
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              autoFocus
            />
            <Button onClick={saveTitle} disabled={loading}>
              保存
            </Button>
          </Stack>
        ) : (
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="h6" fontWeight={800} sx={{ flex: 1 }}>
              {problem.title}
            </Typography>
            <Chip
              size="small"
              color={STATUS_COLOR[problem.status]}
              label={STATUS_LABEL[problem.status]}
            />
            <IconButton
              size="small"
              aria-label="タイトルを編集"
              onClick={() => setEditingTitle(true)}
            >
              <EditRoundedIcon fontSize="small" />
            </IconButton>
          </Stack>
        )}

        <Typography variant="body2" color="text.secondary">
          {problem.summary}
        </Typography>

        {/* ── テーマ（編集可）+ タグ + 言及回数 ──────────────────── */}
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <TextField
            select
            size="small"
            label="主テーマ"
            value={problem.theme}
            disabled={loading}
            onChange={(e) =>
              onTriage({
                action: "editTheme",
                problemId: problem.id,
                theme: e.target.value as Theme,
              })
            }
            sx={{ minWidth: 170 }}
          >
            {THEME_OPTIONS.map((t) => (
              <MenuItem key={t} value={t}>
                {t}
              </MenuItem>
            ))}
          </TextField>
          {problem.tags.map((tag) => (
            <Chip key={tag} size="small" variant="outlined" label={`#${tag}`} />
          ))}
          <Box sx={{ flex: 1 }} />
          <Typography variant="caption" color="text.secondary">
            言及 {problem.mentionCount} 回
          </Typography>
        </Stack>

        <Divider />

        {/* ── Mention タイムライン（主役）─────────────────────────── */}
        <Typography fontWeight={700}>言及の履歴（{timeline.length}件）</Typography>
        <Stack spacing={1.5}>
          {timeline.map((m, idx) => (
            <Stack key={m.id} direction="row" spacing={1.5}>
              <Stack alignItems="center" sx={{ pt: 0.5 }}>
                <Box
                  sx={(t) => ({
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    bgcolor: idx === 0 ? "primary.main" : t.palette.divider,
                  })}
                />
                {idx < timeline.length - 1 && (
                  <Box
                    sx={(t) => ({
                      width: "2px",
                      flex: 1,
                      mt: 0.5,
                      bgcolor: t.palette.divider,
                    })}
                  />
                )}
              </Stack>
              <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, flex: 1 }}>
                <Stack spacing={0.5}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="caption" color="text.secondary">
                      {formatDate(m.createdAt)}
                    </Typography>
                    <Chip
                      size="small"
                      variant="outlined"
                      color={AFFECT_COLOR[m.affect.valence]}
                      label={m.affect.label}
                    />
                    {idx === timeline.length - 1 && <Chip size="small" label="種" />}
                  </Stack>
                  <Typography variant="body2">{m.statement}</Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                    「{m.excerpt}」
                  </Typography>
                </Stack>
              </Paper>
            </Stack>
          ))}
        </Stack>

        {/* ── プラン（UC-05）──────────────────────────────────── */}
        {problem.plans.length > 0 && (
          <>
            <Divider />
            <Typography fontWeight={700}>次の一歩</Typography>
            {problem.plans.map((plan, i) => (
              <Paper key={`${plan.title}-${i}`} variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
                <Typography fontWeight={600}>{plan.title}</Typography>
                <List dense>
                  {plan.steps.map((step) => (
                    <ListItem key={step} sx={{ px: 0 }}>
                      <ListItemText primary={step} />
                    </ListItem>
                  ))}
                </List>
              </Paper>
            ))}
          </>
        )}

        <Divider />

        {/* ── 棚卸し / トリアージ操作 ─────────────────────────── */}
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {onCreatePlan && (
            <Button variant="outlined" disabled={loading} onClick={() => onCreatePlan(problem.id)}>
              次の一歩を作る
            </Button>
          )}
          <Box sx={{ flex: 1 }} />
          {problem.status === "open" ? (
            <>
              <Button
                variant="outlined"
                color="warning"
                startIcon={<ArchiveRoundedIcon />}
                disabled={loading}
                onClick={() => onTriage({ action: "shelve", problemId: problem.id })}
              >
                もう気にしない
              </Button>
              <Button
                variant="contained"
                color="success"
                startIcon={<CheckCircleRoundedIcon />}
                disabled={loading}
                onClick={() => onTriage({ action: "resolve", problemId: problem.id })}
              >
                解決した
              </Button>
            </>
          ) : (
            <Button
              variant="outlined"
              startIcon={<ReplayRoundedIcon />}
              disabled={loading}
              onClick={() => onTriage({ action: "reopen", problemId: problem.id })}
            >
              また気になる（再オープン）
            </Button>
          )}
        </Stack>
      </Stack>
    </Paper>
  );
}
