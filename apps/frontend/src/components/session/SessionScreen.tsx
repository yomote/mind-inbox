import * as React from "react";
import { Badge, Box, Paper, Stack, Tab, Tabs, Typography } from "@mui/material";
import type { ConsultationSession, ExtractionResult } from "../../api";
import type { TtsStatus } from "../../voice/useTextToSpeech";
import { deriveMascotState } from "./mascotState";
import { LivePreviewPane } from "./LivePreviewPane";
import type { PreviewStatus } from "./LivePreviewPane";
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
  /**
   * 2 ペイン (#187 / ADR 0039): 右に「整理されつつある困りごと」の下書きを出すか。
   * BFF `consultation.preview` が無い環境 (real 未結線) では従来の 1 ペインのまま。
   */
  previewEnabled?: boolean;
  preview?: ExtractionResult | null;
  previewStatus?: PreviewStatus;
  onRefreshPreview?: () => void;
  onDraftMessageChange: (value: string) => void;
  onSendMessage: () => void;
  onToggleTtsEnabled: () => void;
  onStopSpeaking: () => void;
  onCrisisSupport: () => void;
  onPause: () => void;
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
  previewEnabled = false,
  preview = null,
  previewStatus = "idle",
  onRefreshPreview,
  onDraftMessageChange,
  onSendMessage,
  onToggleTtsEnabled,
  onStopSpeaking,
  onCrisisSupport,
  onPause,
  onExtract,
}: SessionScreenProps) {
  // モバイル (md 未満) は 2 ペインが成立しないのでタブ切替 (ADR 0039 D4)。
  // md 以上では両ペインを常時表示し、タブ UI 自体を出さない。
  const [activeTab, setActiveTab] = React.useState<"dialogue" | "preview">("dialogue");
  const previewCount = preview?.items.length ?? 0;
  // 対話タブを見ている間に下書きが更新されたらバッジで知らせる (D4)。
  // 「見た」の基準はプレビュータブを開いたときの件数。
  const [seenPreviewCount, setSeenPreviewCount] = React.useState(0);
  const hasUnseenPreview = activeTab !== "preview" && previewCount > seenPreviewCount;

  const handleTabChange = (_: React.SyntheticEvent, next: "dialogue" | "preview") => {
    setActiveTab(next);
    if (next === "preview") setSeenPreviewCount(previewCount);
  };

  const dialoguePane = (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography fontWeight={700}>{session.title}</Typography>

        <SessionMessages
          messages={session.messages}
          mascotState={deriveMascotState(loading, ttsStatus)}
        />

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
          onExtract={onExtract}
          // 右ペインで下書きが見えている環境では、抽出は「確認の一手」(ADR 0039 D3)。
          extractLabel={previewEnabled ? "この内容で確定" : "困りごとを抽出"}
        />
      </Stack>
    </Paper>
  );

  if (!previewEnabled) return dialoguePane;

  return (
    <Stack spacing={1.5}>
      <Tabs
        value={activeTab}
        onChange={handleTabChange}
        variant="fullWidth"
        sx={{ display: { xs: "flex", md: "none" } }}
      >
        <Tab value="dialogue" label="対話" />
        <Tab
          value="preview"
          label={
            <Badge color="secondary" variant="dot" invisible={!hasUnseenPreview}>
              整理中 {previewCount}
            </Badge>
          }
        />
      </Tabs>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "minmax(0, 3fr) minmax(0, 2fr)" },
          gap: 2,
          alignItems: "start",
        }}
      >
        <Box sx={{ display: { xs: activeTab === "dialogue" ? "block" : "none", md: "block" } }}>
          {dialoguePane}
        </Box>
        <Box sx={{ display: { xs: activeTab === "preview" ? "block" : "none", md: "block" } }}>
          <LivePreviewPane
            preview={preview}
            status={previewStatus}
            onRefresh={onRefreshPreview ?? (() => {})}
          />
        </Box>
      </Box>
    </Stack>
  );
}
