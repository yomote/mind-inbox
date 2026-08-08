import GraphicEqIcon from "@mui/icons-material/GraphicEq";
import MicIcon from "@mui/icons-material/Mic";
import MicOffIcon from "@mui/icons-material/MicOff";
import RecordVoiceOverIcon from "@mui/icons-material/RecordVoiceOver";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import VolumeOffIcon from "@mui/icons-material/VolumeOff";
import { Button, Chip, CircularProgress, Stack, TextField, Typography } from "@mui/material";
import * as React from "react";
import { useVoiceInput } from "../../voice/useVoiceInput";

type SessionComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  loading: boolean;
  speaking: boolean;
  ttsEnabled: boolean;
  voiceError: string | null;
  onToggleTtsEnabled: () => void;
  onStopSpeaking: () => void;
};

function formatElapsed(totalSec: number): string {
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${String(sec).padStart(2, "0")}`;
}

export function SessionComposer({
  value,
  onChange,
  onSend,
  loading,
  speaking,
  ttsEnabled,
  voiceError,
  onToggleTtsEnabled,
  onStopSpeaking,
}: SessionComposerProps) {
  // 認識結果の追記は最新の value / onChange を ref 経由で参照する
  // (認識イベントは React の再レンダーと非同期に届くため)。
  const valueRef = React.useRef(value);
  const onChangeRef = React.useRef(onChange);
  React.useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
  });

  const appendTranscript = React.useCallback((text: string) => {
    const prev = valueRef.current;
    const separator = prev.trim().length > 0 ? "\n" : "";
    const next = `${prev}${separator}${text}`;
    // 連続 commit (再レンダー前) でも取りこぼさないよう ref を同期更新する。
    valueRef.current = next;
    onChangeRef.current(next);
  }, []);

  const voice = useVoiceInput(appendTranscript);

  return (
    <Stack spacing={1}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField
          fullWidth
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="ここに入力 / 話して入力"
        />
        <Button variant="outlined" onClick={onSend} disabled={loading || !value.trim()}>
          {loading ? <CircularProgress size={20} /> : "送信"}
        </Button>
      </Stack>

      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
        <Button
          variant={voice.listening ? "contained" : "outlined"}
          color={voice.listening ? "secondary" : "primary"}
          onClick={voice.toggle}
          disabled={!voice.supported || loading}
          startIcon={voice.listening ? <MicOffIcon /> : <MicIcon />}
        >
          {voice.listening ? "音声入力停止" : "音声入力開始"}
        </Button>

        <Button
          variant="outlined"
          onClick={onToggleTtsEnabled}
          startIcon={ttsEnabled ? <VolumeOffIcon /> : <RecordVoiceOverIcon />}
        >
          {ttsEnabled ? "読み上げOFF" : "読み上げON"}
        </Button>

        <Button
          variant="text"
          onClick={onStopSpeaking}
          disabled={!speaking}
          startIcon={<StopCircleIcon />}
        >
          読み上げ停止
        </Button>

        {voice.listening && (
          // 録音状態の可視化 (#121): 経過時間つきで「聞き続けている」ことを示す。
          // エンジンの無音タイムアウトで裏側が再起動しても、ユーザーが停止するまでこの表示は続く。
          <Chip
            icon={<GraphicEqIcon />}
            label={`認識中 ${formatElapsed(voice.elapsedSec)}`}
            color="secondary"
            size="small"
          />
        )}
        {voice.listening && voice.engine && (
          // degraded = 高精度認識に繋がるはずが予期しない失敗で落ちた状態。
          // 「動いてはいるが精度が落ちている」ことをユーザーにも見えるようにする。
          <Chip
            label={
              voice.engine === "azure"
                ? "高精度認識"
                : voice.degraded
                  ? "ブラウザ認識 (高精度認識に接続できず)"
                  : "ブラウザ認識"
            }
            variant="outlined"
            color={voice.degraded ? "warning" : "default"}
            size="small"
          />
        )}
      </Stack>

      {!voice.supported && (
        <Typography variant="caption" color="warning.main">
          このブラウザは音声入力に対応していません。
        </Typography>
      )}

      {voice.interimTranscript && (
        <Typography variant="caption" color="text.secondary">
          音声認識中: {voice.interimTranscript}
        </Typography>
      )}

      {(voice.error || voiceError) && (
        <Typography variant="caption" color="error.main">
          {voice.error ?? voiceError}
        </Typography>
      )}
    </Stack>
  );
}
