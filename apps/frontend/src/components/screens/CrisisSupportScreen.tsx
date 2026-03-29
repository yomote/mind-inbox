import { Button, Paper, Stack, Typography } from "@mui/material";

type CrisisSupportScreenProps = {
  onBackSession: () => void;
};

export function CrisisSupportScreen({
  onBackSession,
}: CrisisSupportScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography fontWeight={700}>危機時サポート導線</Typography>
        <Typography>
          緊急性が高い場合は、地域の緊急連絡先・医療機関・信頼できる人へすぐ連絡してください。
        </Typography>
        <Button variant="outlined" onClick={onBackSession}>
          セッションへ戻る
        </Button>
      </Stack>
    </Paper>
  );
}
