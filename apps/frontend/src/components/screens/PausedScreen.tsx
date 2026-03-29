import { Button, Paper, Stack, Typography } from "@mui/material";

type PausedScreenProps = {
  onBackHome: () => void;
};

export function PausedScreen({ onBackHome }: PausedScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography>現在のセッションは一時保存されました。</Typography>
        <Button variant="contained" onClick={onBackHome}>
          ホームに戻る
        </Button>
      </Stack>
    </Paper>
  );
}
