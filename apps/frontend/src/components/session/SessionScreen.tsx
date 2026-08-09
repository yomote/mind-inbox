import { Paper, Stack, Typography } from "@mui/material";
import type { ConsultationSession } from "../../api";
import type { TtsStatus } from "../../voice/useTextToSpeech";
import { SessionComposer } from "./SessionComposer";
import { SessionControls } from "./SessionControls";
import { SessionMessages } from "./SessionMessages";

type SessionScreenProps = {
  session: ConsultationSession;
  draftMessage: string;
  loading: boolean;
  // STT (音声入力) は SessionComposer が useVoiceInput で自律的に扱う (#121 / ADR 0023)。
  // 旧配線 (sttSupported / listening / interimTranscript / onToggleListening) は #133 で撤去済み。
  speaking: boolean;
  /** 読み上げの進行状態 (#185)。合成中の待ち時間を画面に出すために使う。 */
  ttsStatus?: TtsStatus;
  ttsEnabled: boolean;
  voiceError: string | null;
  onDraftMessageChange: (value: string) => void;
  onSendMessage: () => void;
  onToggleTtsEnabled: () => void;
  onStopSpeaking: () => void;
  onCrisisSupport: () => void;
  onPause: () => void;
  onOrganize: () => void;
  onExtract?: () => void;
};

export function SessionScreen({
  session,
  draftMessage,
  loading,
  speaking,
  ttsStatus,
  ttsEnabled,
  voiceError,
  onDraftMessageChange,
  onSendMessage,
  onToggleTtsEnabled,
  onStopSpeaking,
  onCrisisSupport,
  onPause,
  onOrganize,
  onExtract,
}: SessionScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography fontWeight={700}>{session.title}</Typography>

        <SessionMessages messages={session.messages} />

        <SessionComposer
          value={draftMessage}
          onChange={onDraftMessageChange}
          onSend={onSendMessage}
          loading={loading}
          speaking={speaking}
          ttsStatus={ttsStatus}
          ttsEnabled={ttsEnabled}
          voiceError={voiceError}
          onToggleTtsEnabled={onToggleTtsEnabled}
          onStopSpeaking={onStopSpeaking}
        />

        <SessionControls
          loading={loading}
          onCrisisSupport={onCrisisSupport}
          onPause={onPause}
          onOrganize={onOrganize}
          onExtract={onExtract}
        />
      </Stack>
    </Paper>
  );
}
