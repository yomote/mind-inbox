import { Paper, Stack, Typography } from "@mui/material";
import type { ChatMessage } from "../../mockApi";

type SessionMessagesProps = {
  messages: ChatMessage[];
};

export function SessionMessages({ messages }: SessionMessagesProps) {
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
    </Stack>
  );
}
