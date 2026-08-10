import * as React from "react";
import { SessionScreen } from "../../components/session/SessionScreen";

const previewSession = {
  id: "preview-session",
  title: "仕事の優先順位を整理したい",
  messages: [
    {
      id: "a-1",
      role: "assistant" as const,
      text: "今日はどんなことが気になっていますか?思いつくままで大丈夫です。",
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
      speaking={false}
      ttsEnabled
      voiceError={null}
      onDraftMessageChange={setDraftMessage}
      onSendMessage={() => {}}
      onToggleTtsEnabled={() => {}}
      onStopSpeaking={() => {}}
      onCrisisSupport={() => {}}
      onPause={() => {}}
      onExtract={() => {}}
    />
  );
}
