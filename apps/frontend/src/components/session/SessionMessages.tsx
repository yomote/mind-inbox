import { useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { Box, Paper, Stack, Typography } from "@mui/material";
import type { ChatMessage } from "../../api";
import { getStreamingReply, subscribeStreamingReply } from "../../api/streamingReply";
import { MascotAvatar } from "./MascotAvatar";
import type { MascotState } from "./MascotAvatar";

type SessionMessagesProps = {
  messages: ChatMessage[];
  /**
   * アクティブな吹き出し (ストリーミング中の吹き出し、なければ最新の AI メッセージ) の
   * マスコットに反映する状態 (dialogue-session.mdx §5.6)。過去の吹き出しは常に idle。
   */
  mascotState?: MascotState;
};

/** AI メッセージの「マスコット + 吹き出し」行 (dialogue-session.mdx §5.6)。 */
function AssistantRow({
  mascotState,
  children,
}: {
  mascotState: MascotState;
  children: ReactNode;
}) {
  return (
    <Stack direction="row" spacing={1} alignItems="flex-end">
      <MascotAvatar state={mascotState} />
      <Paper
        sx={{
          p: 1.5,
          borderRadius: 2,
          // 吹き出しの「しっぽ」側 (マスコットに接する左下) だけ角を立てる。
          borderBottomLeftRadius: 4,
          bgcolor: "background.default",
          flex: 1,
          minWidth: 0,
        }}
      >
        {children}
      </Paper>
    </Stack>
  );
}

export function SessionMessages({ messages, mascotState = "idle" }: SessionMessagesProps) {
  // ストリーミング中の AI 応答 (#120 / dialogue-session.mdx §5.2)。
  // 最終メッセージ (同じ id) が messages に入ったら重複表示しない。
  const streaming = useSyncExternalStore(
    subscribeStreamingReply,
    getStreamingReply,
    getStreamingReply,
  );
  const showStreaming =
    streaming !== null && !messages.some((message) => message.id === streaming.id);

  const lastMessage = messages.at(-1);
  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id;

  // AI の返事を待っている間 (最後がユーザー発話で生成中・ストリーミング未開始) は、
  // マスコットの準備中表現のプレースホルダ吹き出しを出す — 無反応の空白を作らない (§5.6)。
  const showPlaceholder =
    !showStreaming && mascotState === "preparing" && lastMessage?.role !== "assistant";

  // 開始直後 (#241): 挨拶を出さない代わりに、待機表情のマスコットが「聞く準備が
  // できている」ことを示す (§3.1)。会話が始まったら出さない。
  const showEmptyState = messages.length === 0 && !showStreaming && !showPlaceholder;

  return (
    <Stack spacing={1}>
      {showEmptyState && (
        <Box data-testid="mascot-empty-state" sx={{ py: 1 }}>
          <MascotAvatar state={mascotState} size={56} />
        </Box>
      )}
      {messages.map((m) =>
        m.role === "assistant" ? (
          <AssistantRow
            key={m.id}
            // 状態を反映するのはアクティブな吹き出しのマスコット 1 体だけ (§5.6)。
            // ストリーミング / プレースホルダが出ている間はそちらがアクティブ。
            mascotState={
              !showStreaming && !showPlaceholder && m.id === lastAssistantId ? mascotState : "idle"
            }
          >
            <Typography variant="caption" color="text.secondary">
              ガイド
            </Typography>
            <Typography sx={{ whiteSpace: "pre-wrap" }}>{m.text}</Typography>
          </AssistantRow>
        ) : (
          <Paper
            key={m.id}
            sx={{
              p: 1.5,
              borderRadius: 2,
              bgcolor: "background.paper",
            }}
          >
            <Typography variant="caption" color="text.secondary">
              あなた
            </Typography>
            <Typography sx={{ whiteSpace: "pre-wrap" }}>{m.text}</Typography>
          </Paper>
        ),
      )}
      {showPlaceholder && (
        // NOTE: 「ガイド」ラベルは付けない — E2E は「ガイド」の個数で AI の返事を数える
        // (e2e-uc/fixtures.ts) ので、返事前のプレースホルダを返事として数えさせない。
        <Box data-testid="mascot-thinking">
          <AssistantRow mascotState={mascotState}>
            <Typography
              aria-label="応答を準備中"
              sx={{
                color: "text.secondary",
                animation: "mi-thinking-pulse 1.2s ease-in-out infinite",
                "@keyframes mi-thinking-pulse": {
                  "0%, 100%": { opacity: 0.35 },
                  "50%": { opacity: 1 },
                },
                "@media (prefers-reduced-motion: reduce)": { animation: "none" },
              }}
            >
              ……
            </Typography>
          </AssistantRow>
        </Box>
      )}
      {showStreaming && (
        <AssistantRow mascotState={mascotState}>
          <Typography variant="caption" color="text.secondary">
            ガイド
          </Typography>
          <Typography sx={{ whiteSpace: "pre-wrap" }} aria-live="polite">
            {streaming.text}
            <Box
              component="span"
              sx={{
                ml: 0.25,
                animation: "mi-caret-blink 1s steps(1) infinite",
                "@keyframes mi-caret-blink": { "50%": { opacity: 0 } },
              }}
            >
              ▍
            </Box>
          </Typography>
        </AssistantRow>
      )}
    </Stack>
  );
}
