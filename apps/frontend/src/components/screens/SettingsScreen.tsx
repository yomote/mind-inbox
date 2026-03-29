import {
  FormControlLabel,
  Paper,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import type { PaletteMode } from "@mui/material";

type SettingsScreenProps = {
  themeMode: PaletteMode;
  onToggleTheme: () => void;
};

export function SettingsScreen({
  themeMode,
  onToggleTheme,
}: SettingsScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={1.5}>
        <Typography
          variant="h6"
          fontWeight={700}
          sx={{ display: "flex", alignItems: "center", gap: 1 }}
        >
          <SettingsRoundedIcon fontSize="small" />
          設定
        </Typography>
        <Typography>
          ローカルモック版のため、データはブラウザ再読み込みで初期化されます。
        </Typography>
        <Typography variant="body2" color="text.secondary">
          ・プロフィール設定（ダミー）
          <br />
          ・通知設定（ダミー）
        </Typography>
        <FormControlLabel
          control={
            <Switch
              checked={themeMode === "dark"}
              onChange={onToggleTheme}
              color="primary"
            />
          }
          label="ダークテーマ"
        />
      </Stack>
    </Paper>
  );
}
