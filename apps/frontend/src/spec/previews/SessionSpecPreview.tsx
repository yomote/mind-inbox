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

// 右ペインの下書きプレビュー (#187 / ADR 0039 / §5.8) の fixture。
// 「既存に追加 (🔁)」と「新規」の両方のカードを見せる。
const previewExtraction = {
  sessionId: "preview-session",
  items: [
    {
      mention: {
        id: "m-preview-recur",
        sessionId: "preview-session",
        dumpId: "preview-session",
        createdAt: new Date().toISOString(),
        statement: "やることが多く、優先順位がつけられないことに焦りがある。",
        excerpt: "やることが多くて、何から手をつけるか決められません。",
        affect: { label: "焦り", valence: "negative" as const, intensity: 0.6 },
        proposedTheme: "仕事・キャリア" as const,
        proposedTags: ["優先順位"],
        problemId: "p-career",
        groupingConfidence: 0.85,
      },
      grouping: {
        kind: "existing" as const,
        problemId: "p-career",
        problemTitle: "転職すべきか迷っている",
        problemTheme: "仕事・キャリア" as const,
        isRecurrence: true,
        mentionCount: 4,
        reignited: false,
        groupingConfidence: 0.85,
      },
    },
    {
      mention: {
        id: "m-preview-new",
        sessionId: "preview-session",
        dumpId: "preview-session",
        createdAt: new Date().toISOString(),
        statement: "タスクの全体量が見えず、着手の判断ができない。",
        excerpt: "何から手をつけるか決められません",
        affect: { label: "混乱", valence: "negative" as const, intensity: 0.5 },
        proposedTheme: "日常・生活" as const,
        proposedTags: ["タスク管理"],
        problemId: "p-draft-preview",
        groupingConfidence: null,
      },
      grouping: {
        kind: "new" as const,
        problemId: "p-draft-preview",
        problemTitle: "タスクが多すぎて着手できない",
        problemTheme: "日常・生活" as const,
        isRecurrence: false,
        mentionCount: 1,
        reignited: false,
        groupingConfidence: null,
      },
    },
  ],
  newProblemCount: 1,
  updatedProblemCount: 1,
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
      previewEnabled
      preview={previewExtraction}
      previewStatus="idle"
      onRefreshPreview={() => {}}
      // 承認カード (#82 / §5.9) は「承認要求が来ている状態」でしか出ないので、
      // 仕様プレビューでは常に出しておく (この画面は各状態の見本市)。
      pendingApproval={{
        id: "preview-approval",
        description: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
      }}
      onRespondToApproval={() => {}}
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
