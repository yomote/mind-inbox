import { Button, Paper, Stack } from "@mui/material";

type HomeScreenProps = {
  onStartConsultation: () => void;
  onProblemList?: () => void;
  onHistory: () => void;
  onSpecPreview?: () => void;
};

export function HomeScreen({
  onStartConsultation,
  onProblemList,
  onHistory,
  onSpecPreview,
}: HomeScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={1.5}>
        <Button variant="contained" onClick={onStartConsultation}>
          新しい相談を始める
        </Button>
        {onProblemList && (
          <Button variant="outlined" onClick={onProblemList}>
            困りごと一覧
          </Button>
        )}
        {onSpecPreview && (
          <Button variant="outlined" onClick={onSpecPreview}>
            UI仕様プレビュー
          </Button>
        )}
        <Button variant="outlined" onClick={onHistory}>
          履歴・振り返り
        </Button>
      </Stack>
    </Paper>
  );
}
