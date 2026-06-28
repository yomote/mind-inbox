import { Box, Button, Stack } from "@mui/material";

type SessionControlsProps = {
  loading: boolean;
  onCrisisSupport: () => void;
  onPause: () => void;
  onOrganize: () => void;
  onExtract?: () => void;
};

export function SessionControls({
  loading,
  onCrisisSupport,
  onPause,
  onOrganize,
  onExtract,
}: SessionControlsProps) {
  return (
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
      <Button variant="text" onClick={onCrisisSupport}>
        危機時サポート
      </Button>
      <Button variant="text" onClick={onPause}>
        一時保存 / 中断
      </Button>
      <Box sx={{ flex: 1 }} />
      {onExtract && (
        <Button variant="outlined" onClick={onExtract} disabled={loading}>
          困りごとを抽出
        </Button>
      )}
      <Button variant="contained" onClick={onOrganize} disabled={loading}>
        整理結果へ
      </Button>
    </Stack>
  );
}
