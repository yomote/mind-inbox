import { useSyncExternalStore } from "react";
import { Box, Paper, Stack, Typography } from "@mui/material";
import type { ChatMessage } from "../../mockApi";
import { getStreamingReply, subscribeStreamingReply } from "../../api/streamingReply";

type SessionMessagesProps = {
  messages: ChatMessage[];
};

export function SessionMessages({ messages }: SessionMessagesProps) {
  // ストリーミング中の AI 応答 (#120 / dialogue session.mdx §5.2)。
  // 最終メッセージ (同じ id) が messages に入ったら重複表示しない。
  const streaming = useSyncExternalStore(
    subscribeStreamingReply,
    getStreamingReply,
    getStreamingReply,
  );
  const showStreaming =
    streaming !== null && !messages.some((message) => message.id === streaming.id);

  return (
    <Stack spacing={1}>
      {messages.map((m) => (
        <Paper
          key={m.id}
          sx={{
            p: 1.5,
            borderRadius: 2,
            bgcolor:
              m.role === "assistant"
                ? "background.default"
                : "background.paper",
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {m.role === "assistant" ? "ガイド" : "あなた"}
          </Typography>
          <Typography sx={{ whiteSpace: "pre-wrap" }}>{m.text}</Typography>
        </Paper>
      ))}
      {showStreaming && (
        <Paper
          sx={{
            p: 1.5,
            borderRadius: 2,
            bgcolor: "background.default",
          }}
        >
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
        </Paper>
      )}
    </Stack>
  );
}
