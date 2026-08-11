import GraphicEqIcon from "@mui/icons-material/GraphicEq";
import MicIcon from "@mui/icons-material/Mic";
import MicOffIcon from "@mui/icons-material/MicOff";
import RecordVoiceOverIcon from "@mui/icons-material/RecordVoiceOver";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import VolumeOffIcon from "@mui/icons-material/VolumeOff";
import { Button, Chip, CircularProgress, Stack, TextField, Typography } from "@mui/material";
import * as React from "react";
import { useVoiceInput } from "../../voice/useVoiceInput";
import type { TtsStatus } from "../../voice/useTextToSpeech";

type SessionComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  loading: boolean;
  speaking: boolean;
  /** 読み上げの進行状態 (#185)。合成中と再生中を分けて出すために使う。 */
  ttsStatus?: TtsStatus;
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

/** 認識途中テキストを入力欄の末尾に継ぐ。区切りは appendTranscript と揃える。 */
function withInterim(committed: string, interim: string): string {
  if (!interim) return committed;
  return committed.trim().length > 0 ? `${committed}\n${interim}` : interim;
}

export function SessionComposer({
  value,
  onChange,
  onSend,
  loading,
  speaking,
  ttsStatus,
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

  // TTS の再生状態を認識側へ配線する (#228)。再生中はスピーカーの合成音声を
  // ユーザー発話として拾ってしまうため、認識結果を破棄する (再生が終わると自動復帰)。
  const voice = useVoiceInput(appendTranscript, { ttsPlaying: ttsStatus === "playing" });
  const inputRef = React.useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);

  /**
   * マイクボタン (#186)。**同期で** 入力欄にフォーカスしてから開始する。
   * 押した後にユーザーが入力欄をクリックし直さないと手直し・送信できない、
   * という往復をここで無くす。
   */
  const handleToggleVoice = React.useCallback(() => {
    if (!voice.listening) inputRef.current?.focus();
    voice.toggle();
  }, [voice]);

  // 認識途中テキストは**入力欄の中**に出す (#186)。以前は入力欄の外の小さな
  // キャプションだったため、喋っている最中は入力欄が空に見えて「入っていない」
  // と受け取られていた。確定すると stitcher が value 側へ commit する。
  const displayValue = voice.listening ? withInterim(value, voice.interimTranscript) : value;

  return (
    <Stack spacing={1}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField
          fullWidth
          multiline
          minRows={1}
          maxRows={6}
          inputRef={inputRef}
          value={displayValue}
          onChange={(e) => onChange(e.target.value)}
          placeholder="ここに入力 / 話して入力"
          // 認識中は表示が「確定テキスト + 未確定テール」の合成なので、直接編集させると
          // 確定時に二重に入る。停止すれば未確定分は確定して普通に編集できる。
          slotProps={{ input: { readOnly: voice.listening } }}
          helperText={voice.listening ? "認識中は編集できません。停止すると編集できます。" : " "}
        />
        <Button variant="outlined" onClick={onSend} disabled={loading || !value.trim()}>
          {loading ? <CircularProgress size={20} /> : "送信"}
        </Button>
      </Stack>

      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
        <Button
          variant={voice.listening ? "contained" : "outlined"}
          color={voice.listening ? "secondary" : "primary"}
          onClick={handleToggleVoice}
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

        {/*
          読み上げの待ち時間を見えるようにする (#185)。合成には数秒かかるのに以前は
          画面が何も変わらず、「待てば鳴るのか、もう終わったのか」が判別できなかった。
          合成中と再生中を別の表示にして、どちらでも停止できることを示す。
        */}
        {ttsStatus === "synthesizing" && (
          <Chip
            icon={<CircularProgress size={12} color="inherit" />}
            label="ずんだもんが準備中…"
            variant="outlined"
            size="small"
            data-testid="tts-status"
          />
        )}
        {ttsStatus === "playing" && (
          <Chip
            icon={<GraphicEqIcon />}
            label="読み上げ中"
            color="primary"
            size="small"
            data-testid="tts-status"
          />
        )}

        {voice.phase === "preparing" && (
          // 押した直後〜マイクが開くまで (#186)。この区間に喋られた内容は落ちるので、
          // 「押した = もう聞いている」と誤解させないために別表示にする。
          <Chip
            icon={<CircularProgress size={12} color="inherit" />}
            label="マイクを準備中…"
            color="default"
            variant="outlined"
            size="small"
          />
        )}
        {voice.phase === "listening" && voice.muted && (
          // TTS 再生中は認識結果を破棄している (#228)。「聞いています」のままだと
          // この間の発話が入ると誤解させるので、止まっていることを見せる。
          <Chip
            icon={<MicOffIcon />}
            label="読み上げ中は音声入力を一時停止中"
            variant="outlined"
            size="small"
            data-testid="stt-muted"
          />
        )}
        {voice.phase === "listening" && !voice.muted && (
          // 録音状態の可視化 (#121): 経過時間つきで「聞き続けている」ことを示す。
          // エンジンの無音タイムアウトで裏側が再起動しても、ユーザーが停止するまでこの表示は続く。
          <Chip
            icon={<GraphicEqIcon />}
            label={`聞いています ${formatElapsed(voice.elapsedSec)}`}
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

      {(voice.error || voiceError) && (
        <Typography variant="caption" color="error.main">
          {voice.error ?? voiceError}
        </Typography>
      )}
    </Stack>
  );
}
