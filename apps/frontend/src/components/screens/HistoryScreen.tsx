import { Button, Paper, Stack, Typography } from "@mui/material";
import type { HistoryItem } from "../../mockApi";

type HistoryScreenProps = {
  histories: HistoryItem[];
  selectedHistory: HistoryItem | null;
  onBackHome: () => void;
  onOpenResult: (item: HistoryItem) => void;
};

export function HistoryScreen({
  histories,
  selectedHistory,
  onBackHome,
  onOpenResult,
}: HistoryScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Button
          variant="text"
          onClick={onBackHome}
          sx={{ width: "fit-content" }}
        >
          ホームへ
        </Button>
        {histories.length === 0 ? (
          <Typography color="text.secondary">履歴はまだありません。</Typography>
        ) : (
          histories.map((item) => (
            <Paper key={item.id} sx={{ p: 2, borderRadius: 2 }}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography fontWeight={700} sx={{ flex: 1 }}>
                  {item.title}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {new Date(item.createdAt).toLocaleString("ja-JP")}
                </Typography>
              </Stack>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {item.result.summary}
              </Typography>
              <Button
                variant="outlined"
                sx={{ mt: 1.5 }}
                onClick={() => onOpenResult(item)}
              >
                この結果を開く
              </Button>
            </Paper>
          ))
        )}

        {selectedHistory && (
          <Typography variant="body2" color="text.secondary">
            選択中: {selectedHistory.title}
          </Typography>
        )}
      </Stack>
    </Paper>
  );
}
