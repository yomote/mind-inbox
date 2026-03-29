import { Button, Paper, Stack, Typography } from "@mui/material";

type OnboardingScreenProps = {
  onStart: () => void;
};

export function OnboardingScreen({ onStart }: OnboardingScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography>
          ようこそ。気持ちや課題を対話で整理し、行動プランまで作成できます。
        </Typography>
        <Button variant="contained" onClick={onStart}>
          はじめる
        </Button>
      </Stack>
    </Paper>
  );
}
