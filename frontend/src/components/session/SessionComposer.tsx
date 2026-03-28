import MicIcon from "@mui/icons-material/Mic";
import MicOffIcon from "@mui/icons-material/MicOff";
import RecordVoiceOverIcon from "@mui/icons-material/RecordVoiceOver";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import VolumeOffIcon from "@mui/icons-material/VolumeOff";
import {
  Button,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

type SessionComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  loading: boolean;
  sttSupported: boolean;
  listening: boolean;
  interimTranscript: string;
  speaking: boolean;
  ttsEnabled: boolean;
  voiceError: string | null;
  onToggleListening: () => void;
  onToggleTtsEnabled: () => void;
  onStopSpeaking: () => void;
};

export function SessionComposer({
  value,
  onChange,
  onSend,
  loading,
  sttSupported,
  listening,
  interimTranscript,
  speaking,
  ttsEnabled,
  voiceError,
  onToggleListening,
  onToggleTtsEnabled,
  onStopSpeaking,
}: SessionComposerProps) {
  return (
    <Stack spacing={1}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField
          fullWidth
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="ここに入力 / 話して入力"
        />
        <Button
          variant="outlined"
          onClick={onSend}
          disabled={loading || !value.trim()}
        >
          {loading ? <CircularProgress size={20} /> : "送信"}
        </Button>
      </Stack>

      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Button
          variant={listening ? "contained" : "outlined"}
          color={listening ? "secondary" : "primary"}
          onClick={onToggleListening}
          disabled={!sttSupported || loading}
          startIcon={listening ? <MicOffIcon /> : <MicIcon />}
        >
          {listening ? "音声入力停止" : "音声入力開始"}
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

        {listening && <Chip label="認識中" color="secondary" size="small" />}
      </Stack>

      {!sttSupported && (
        <Typography variant="caption" color="warning.main">
          このブラウザはWeb Speech APIに未対応です。
        </Typography>
      )}

      {interimTranscript && (
        <Typography variant="caption" color="text.secondary">
          音声認識中: {interimTranscript}
        </Typography>
      )}

      {voiceError && (
        <Typography variant="caption" color="error.main">
          {voiceError}
        </Typography>
      )}
    </Stack>
  );
}
