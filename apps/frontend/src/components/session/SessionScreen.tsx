import { Paper, Stack, Typography } from "@mui/material";
import type { ConsultationSession } from "../../mockApi";
import { SessionComposer } from "./SessionComposer";
import { SessionControls } from "./SessionControls";
import { SessionMessages } from "./SessionMessages";

type SessionScreenProps = {
  session: ConsultationSession;
  draftMessage: string;
  loading: boolean;
  sttSupported: boolean;
  listening: boolean;
  interimTranscript: string;
  speaking: boolean;
  ttsEnabled: boolean;
  voiceError: string | null;
  onDraftMessageChange: (value: string) => void;
  onSendMessage: () => void;
  onToggleListening: () => void;
  onToggleTtsEnabled: () => void;
  onStopSpeaking: () => void;
  onCrisisSupport: () => void;
  onPause: () => void;
  onOrganize: () => void;
};

export function SessionScreen({
  session,
  draftMessage,
  loading,
  sttSupported,
  listening,
  interimTranscript,
  speaking,
  ttsEnabled,
  voiceError,
  onDraftMessageChange,
  onSendMessage,
  onToggleListening,
  onToggleTtsEnabled,
  onStopSpeaking,
  onCrisisSupport,
  onPause,
  onOrganize,
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
          sttSupported={sttSupported}
          listening={listening}
          interimTranscript={interimTranscript}
          speaking={speaking}
          ttsEnabled={ttsEnabled}
          voiceError={voiceError}
          onToggleListening={onToggleListening}
          onToggleTtsEnabled={onToggleTtsEnabled}
          onStopSpeaking={onStopSpeaking}
        />

        <SessionControls
          loading={loading}
          onCrisisSupport={onCrisisSupport}
          onPause={onPause}
          onOrganize={onOrganize}
        />
      </Stack>
    </Paper>
  );
}
