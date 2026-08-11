import * as React from "react";
import { SessionScreen } from "../../components/session/SessionScreen";

const previewSession = {
  id: "preview-session",
  title: "仕事の優先順位を整理したい",
  // AI の挨拶 (初手) は無い (#241 / §3.1) — 会話はユーザーの発話から始まる。
  messages: [
    {
      id: "u-1",
      role: "user" as const,
      text: "やることが多くて、何から手をつけるか決められません。",
      createdAt: new Date().toISOString(),
    },
    {
      id: "a-1",
      role: "assistant" as const,
      text: "受け止めました。特に気持ちが動いた場面を1つ教えてください。",
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
