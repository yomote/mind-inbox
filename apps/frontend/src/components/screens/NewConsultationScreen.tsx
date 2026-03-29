import {
  Button,
  CircularProgress,
  Paper,
  Stack,
  TextField,
} from "@mui/material";

type NewConsultationScreenProps = {
  concern: string;
  loading: boolean;
  onConcernChange: (value: string) => void;
  onBack: () => void;
  onStart: () => void;
};

export function NewConsultationScreen({
  concern,
  loading,
  onConcernChange,
  onBack,
  onStart,
}: NewConsultationScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <TextField
          label="相談テーマ"
          value={concern}
          onChange={(e) => onConcernChange(e.target.value)}
          placeholder="例: 仕事の優先順位が整理できない"
          fullWidth
        />
        <Stack direction="row" spacing={1}>
          <Button variant="text" onClick={onBack}>
            戻る
          </Button>
          <Button variant="contained" onClick={onStart} disabled={loading}>
            {loading ? <CircularProgress size={20} /> : "対話を開始"}
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
