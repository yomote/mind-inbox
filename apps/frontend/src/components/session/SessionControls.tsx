import { Box, Button, Stack } from "@mui/material";

type SessionControlsProps = {
  loading: boolean;
  onCrisisSupport: () => void;
  onPause: () => void;
  onExtract?: () => void;
  /**
   * 抽出ボタンの文言。下書きプレビュー (#187 / ADR 0039 D3) が有効な環境では
   * 「この内容で確定」(右ペインの予告編を確定する一手)、無効な環境では従来の
   * 「困りごとを抽出」(一発勝負の抽出)。
   */
  extractLabel?: string;
};

export function SessionControls({
  loading,
  onCrisisSupport,
  onPause,
  onExtract,
  extractLabel = "困りごとを抽出",
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
      {/*
        セッションからの出口は「困りごとを抽出 (= この内容で確定)」1 本だけ (ADR 0034)。
        旧 PoC の「整理結果へ」は UC にも FR にも無く、下流が 404 を返す
        壊れた導線だったため撤去した。
      */}
      {onExtract && (
        <Button variant="contained" onClick={onExtract} disabled={loading}>
          {extractLabel}
        </Button>
      )}
    </Stack>
  );
}
