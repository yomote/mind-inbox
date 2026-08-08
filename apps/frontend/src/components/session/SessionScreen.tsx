import { Paper, Stack, Typography } from "@mui/material";
import type { ConsultationSession } from "../../api";
import { SessionComposer } from "./SessionComposer";
import { SessionControls } from "./SessionControls";
import { SessionMessages } from "./SessionMessages";

type SessionScreenProps = {
  session: ConsultationSession;
  draftMessage: string;
  loading: boolean;
  // STT (音声入力) は SessionComposer が useVoiceInput で自律的に扱う (#121 / ADR 0023)。
  // 以下 4 つは呼び出し側 (Layout 経由の旧配線) がまだ渡してくるため型には残すが未使用。
  // 旧配線の撤去は #112/#120 (Layout 作業中) との競合を避けてフォローアップで行う。
  sttSupported?: boolean;
  listening?: boolean;
  interimTranscript?: string;
  onToggleListening?: () => void;
  speaking: boolean;
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
