import { Button, Paper, Stack, Typography } from "@mui/material";
import type { ApprovalRequest } from "../../api";

/**
 * 副作用ツールの実行確認カード (#82 / G1 / dialogue-session.mdx §5.9)。
 *
 * サーバ側 (ai-agent) は `approval_mode="always_require"` のツールを実行する前に
 * 承認待ちで止まる。この画面が無いと**ユーザーには普通の返事しか見えず**、
 * 「サーバは待っているのに誰も押せない」状態になる (G1 が画面に出ていなかった状態)。
 *
 * カードは**単体で「何を実行しようとしているか」が読める**ようにする (§5.9)。
 * 要求文は AI の吹き出しにも出るが、吹き出しを遡らないと分からない承認は
 * 「よく分からないまま押す」ボタンにしかならない。
 */
type ApprovalRequestCardProps = {
  request: ApprovalRequest;
  /** 通信中は二度押しさせない (承認は取り消せない操作)。 */
  loading?: boolean;
  onRespond: (approved: boolean) => void;
};

export function ApprovalRequestCard({ request, loading, onRespond }: ApprovalRequestCardProps) {
  return (
    <Paper
      data-testid="approval-request"
      variant="outlined"
      sx={{ p: 2, borderRadius: 2, borderColor: "warning.main" }}
    >
      <Stack spacing={1}>
        <Typography variant="subtitle2" fontWeight={700}>
          この操作にはあなたの承認が必要です
        </Typography>
        <Typography sx={{ whiteSpace: "pre-wrap" }}>{request.description}</Typography>
        <Typography variant="caption" color="text.secondary">
          承認するまで、この操作は行われません。
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button variant="contained" disabled={loading} onClick={() => onRespond(true)}>
            承認して実行
          </Button>
          <Button variant="outlined" disabled={loading} onClick={() => onRespond(false)}>
            却下する
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
