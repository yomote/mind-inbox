import * as React from "react";
import { Badge, Box, Paper, Stack, Tab, Tabs, Typography } from "@mui/material";
import type { ApprovalRequest, ConsultationSession, ExtractionResult } from "../../api";
import type { TtsStatus } from "../../voice/useTextToSpeech";
import { ApprovalRequestCard } from "./ApprovalRequestCard";
import { ChoiceOptions } from "./ChoiceOptions";
import { deriveMascotState } from "./mascotState";
import type { PreviewStatus } from "./LivePreviewPane";
import { OrganizingPane } from "./OrganizingPane";
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
  /** 副作用ツールの承認待ち (#82 / G1 / §5.9)。null なら承認カードを出さない。 */
  pendingApproval?: ApprovalRequest | null;
  onRespondToApproval?: (approved: boolean) => void;
  /**
   * AI が提示した選択肢 (#432-b / §5.10)。空配列なら何も出さない。
   * **承認とは独立** — 押さなくても入力欄から会話を続けられる。
   */
  offeredChoices?: string[];
  onSelectChoice?: (choice: string) => void;
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
  pendingApproval = null,
  onRespondToApproval,
  offeredChoices = [],
  onSelectChoice,
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
  // 対話タブを見ている間に下書きが**更新されたら**バッジで知らせる (D4 / §5.8)。
  //
  // 「見た」の基準は**更新そのもの** (= preview オブジェクトの世代) であって件数ではない。
  // 件数比較にすると「件数据え置きで中身だけ変わった更新」(カードの statement や
  // 回数が変わる / 1 件が別の困りごとに差し替わる) が通知されない (PR #282 再レビュー P2-b)。
  const [seenPreview, setSeenPreview] = React.useState<ExtractionResult | null>(null);
  const hasUnseenPreview = activeTab !== "preview" && preview !== null && preview !== seenPreview;

  // プレビュータブを開いている間の更新は、その場で見えているので既読にする。
  // effect ではなく render 中のガード付き調整で行う (React 公式の
  // "adjusting state when props change" パターン / react-hooks 7.1 の
  // set-state-in-effect が effect 内の同期 setState を error にする)。
  if (activeTab === "preview" && seenPreview !== preview) {
    setSeenPreview(preview);
  }

  // 承認要求が届いたら対話タブへ引き戻す (§5.9 / PR #416 Codex P2)。
  //
  // 承認カードは対話ペインの中にしか無い。md 未満で「整理中」タブを見ている間に
  // 要求が届くと、カードは display:none の裏に出るだけで**画面には何も出ない** —
  // サーバ (ai-agent) は承認待ちで止まっているのに、ユーザーには止まっていることも
  // 押すべきボタンがあることも伝わらない (G1 が静かに無効化される)。
  //
  // 引き戻すのは**到着した瞬間だけ** (id が変わった時)。pending の間ずっと対話タブに
  // 固定すると、承認を保留したまま下書きを見に行くことができなくなる。
  const arrivedApprovalId = React.useRef<string | null>(null);
  React.useEffect(() => {
    const id = pendingApproval?.id ?? null;
    if (id !== null && id !== arrivedApprovalId.current) setActiveTab("dialogue");
    arrivedApprovalId.current = id;
  }, [pendingApproval]);

  const handleTabChange = (_: React.SyntheticEvent, next: "dialogue" | "preview") => {
    setActiveTab(next);
    if (next === "preview") setSeenPreview(preview);
  };

  const dialoguePane = (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography sx={{ fontWeight: 700 }}>{session.title}</Typography>

        <SessionMessages
          messages={session.messages}
          mascotState={deriveMascotState(loading, ttsStatus)}
        />

        {/* 承認要求 (#82 / §5.9) は会話の直下・入力欄の上に出す。会話の続きを
            打つ前に「実行してよいか」を目に入れる位置に置く。 */}
        {pendingApproval && (
          <ApprovalRequestCard
            request={pendingApproval}
            loading={loading}
            onRespond={onRespondToApproval ?? (() => {})}
          />
        )}

        {/* 選択肢 (#432-b / §5.10) も会話の直下・入力欄の上。承認カードとは
            **独立に**出る (同時に出ることは無い — ai-agent が承認要求のターンに
            choices を積まないため)。入力欄は塞がない: 選ばずに書くのは正常な操作。

            md 未満で「整理」タブを見ている間に届いても対話タブへ引き戻さない
            (承認 §5.9 との違い)。承認はサーバが待っていて TTL で失効するから
            引き戻す必要があるが、選択肢はサーバに待ち状態が無く、次の発話まで
            画面に残り続けるので、見逃しても失われるものが無い。 */}
        <ChoiceOptions
          choices={offeredChoices}
          loading={loading}
          onSelect={onSelectChoice ?? (() => {})}
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
          // 確定は表示中の下書きをそのまま保存する操作 (再抽出しない — PR #282 P1)。
          // 下書きがまだ無い間は対象が無いので押せない (§5.8)。
          extractDisabled={previewEnabled && preview === null}
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
        // 右ペイン内にもタブ (AI の整理 / 下書き) があるので、**どちらのペインを見ているか**を
        // 読む側が取り違えないよう、外側のタブ群に印を付ける (#433)。
        data-testid="session-tabs"
        sx={{ display: { xs: "flex", md: "none" } }}
      >
        <Tab value="dialogue" label="対話" />
        <Tab
          value="preview"
          data-testid="preview-tab"
          // 未読状態の機械検証点 (§5.8)。E2E / 単体が「更新の通知が出ているか」を読める。
          data-preview-unseen={hasUnseenPreview ? "true" : "false"}
          // 件数は右ペイン内の「下書き N」タブが持つ (#433)。既定で開くのは
          // 「AI の整理」なので、外側のタブに下書き件数を出すと**開いて出てくるものと
          // 数が食い違う**。ここは「整理の面がある」ことと未読だけを伝える。
          label={
            <Badge color="secondary" variant="dot" invisible={!hasUnseenPreview}>
              整理
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
          {/* 右ペインは「AI の整理」(既定) / 「下書き」の 2 タブ (#433 / 裁定 1)。 */}
          <OrganizingPane
            preview={preview}
            status={previewStatus}
            onRefresh={onRefreshPreview ?? (() => {})}
          />
        </Box>
      </Box>
    </Stack>
  );
}
