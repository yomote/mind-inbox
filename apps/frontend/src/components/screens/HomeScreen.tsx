import { Button, Paper, Stack } from "@mui/material";

type HomeScreenProps = {
  onNewConsultation: () => void;
  onHistory: () => void;
  onSpecPreview?: () => void;
};

export function HomeScreen({
  onNewConsultation,
  onHistory,
  onSpecPreview,
}: HomeScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={1.5}>
        <Button variant="contained" onClick={onNewConsultation}>
          新しい相談を始める
        </Button>
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
