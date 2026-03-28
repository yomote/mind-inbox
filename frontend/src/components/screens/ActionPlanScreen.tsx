import {
  Button,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import type { ActionPlan } from "../../mockApi";

type ActionPlanScreenProps = {
  plan: ActionPlan;
  onSave: () => void;
};

export function ActionPlanScreen({ plan, onSave }: ActionPlanScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={2}>
        <Typography fontWeight={700}>{plan.title}</Typography>
        <List dense>
          {plan.steps.map((step) => (
            <ListItem key={step} sx={{ px: 0 }}>
              <ListItemText primary={step} />
            </ListItem>
          ))}
        </List>
        <Button variant="contained" onClick={onSave}>
          保存して履歴へ
        </Button>
      </Stack>
    </Paper>
  );
}
