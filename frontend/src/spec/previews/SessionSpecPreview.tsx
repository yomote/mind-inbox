import * as React from "react";
import { SessionScreen } from "../../components/session/SessionScreen";

const previewSession = {
  id: "preview-session",
  title: "仕事の優先順位を整理したい",
  messages: [
    {
      id: "a-1",
      role: "assistant" as const,
      text: "ありがとうございます。今いちばん気になっていることを教えてください。",
      createdAt: new Date().toISOString(),
    },
    {
      id: "u-1",
      role: "user" as const,
      text: "やることが多くて、何から手をつけるか決められません。",
      createdAt: new Date().toISOString(),
    },
  ],
};

export function SessionSpecPreview() {
  const [draftMessage, setDraftMessage] = React.useState("下書きメッセージ");

  return (
    <SessionScreen
      session={previewSession}
      draftMessage={draftMessage}
      loading={false}
      sttSupported
      listening={false}
      interimTranscript=""
      speaking={false}
      ttsEnabled
      voiceError={null}
      onDraftMessageChange={setDraftMessage}
      onSendMessage={() => {}}
      onToggleListening={() => {}}
      onToggleTtsEnabled={() => {}}
      onStopSpeaking={() => {}}
      onCrisisSupport={() => {}}
      onPause={() => {}}
      onOrganize={() => {}}
    />
  );
}
