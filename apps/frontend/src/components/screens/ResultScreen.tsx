import {
  Box,
  Button,
  Chip,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import type { OrganizedResult } from "../../mockApi";

type ResultScreenProps = {
  result: OrganizedResult;
  loading: boolean;
  onHistory: () => void;
  onCreatePlan: () => void;
};

export function ResultScreen({
  result,
  loading,
  onHistory,
  onCreatePlan,
}: ResultScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography>{result.summary}</Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {result.emotions.map((e) => (
            <Chip key={e} label={e} />
          ))}
        </Stack>
        <List dense>
          {result.priorities.map((p) => (
            <ListItem key={p} sx={{ px: 0 }}>
              <ListItemText primary={p} />
            </ListItem>
          ))}
        </List>
        <Stack direction="row" spacing={1}>
          <Button variant="text" onClick={onHistory}>
            履歴へ
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" onClick={onCreatePlan} disabled={loading}>
            行動プランへ
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
