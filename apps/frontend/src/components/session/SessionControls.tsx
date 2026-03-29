import { Box, Button, Stack } from "@mui/material";

type SessionControlsProps = {
  loading: boolean;
  onCrisisSupport: () => void;
  onPause: () => void;
  onOrganize: () => void;
};

export function SessionControls({
  loading,
  onCrisisSupport,
  onPause,
  onOrganize,
}: SessionControlsProps) {
  return (
    <Stack direction="row" spacing={1}>
      <Button variant="text" onClick={onCrisisSupport}>
        危機時サポート
      </Button>
      <Button variant="text" onClick={onPause}>
        一時保存 / 中断
      </Button>
      <Box sx={{ flex: 1 }} />
      <Button variant="contained" onClick={onOrganize} disabled={loading}>
        整理結果へ
      </Button>
    </Stack>
  );
}
