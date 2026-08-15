import { Box, FormControlLabel, Paper, Slider, Stack, Switch, Typography } from "@mui/material";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import RecordVoiceOverRoundedIcon from "@mui/icons-material/RecordVoiceOverRounded";
import type { PaletteMode } from "@mui/material";
import {
  SPEED_SCALE_DEFAULT,
  SPEED_SCALE_MAX,
  SPEED_SCALE_MIN,
  SPEED_SCALE_STEP,
  formatSpeedScale,
} from "../../voice/speedScale";

type SettingsScreenProps = {
  themeMode: PaletteMode;
  onToggleTheme: () => void;
  /** 読み上げ速度 (等倍 = 1.0 / #242)。 */
  speedScale: number;
  onChangeSpeedScale: (value: number) => void;
};

const SPEED_MARKS = [
  { value: SPEED_SCALE_MIN, label: "ゆっくり" },
  { value: SPEED_SCALE_DEFAULT, label: "標準" },
  { value: SPEED_SCALE_MAX, label: "速い" },
];

export function SettingsScreen({
  themeMode,
  onToggleTheme,
  speedScale,
  onChangeSpeedScale,
}: SettingsScreenProps) {
  return (
    <Paper sx={{ p: 3, borderRadius: 3 }}>
      <Stack spacing={1.5}>
        <Typography
          variant="h6"
          sx={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 1 }}
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
            <Switch checked={themeMode === "dark"} onChange={onToggleTheme} color="primary" />
          }
          label="ダークテーマ"
        />

        {/*
          読み上げ速度 (#242 / settings.mdx)。data-tts-speed-scale は「今この画面が
          持っている速度」の観測点 — スライダーの内部 input は値の表現がブラウザ依存で、
          E2E から読むと「動かしたのに保存されていない」を見逃す。
        */}
        <Box data-tts-speed-scale={speedScale} sx={{ pt: 1 }}>
          <Typography
            variant="subtitle2"
            sx={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 1 }}
          >
            <RecordVoiceOverRoundedIcon fontSize="small" />
            読み上げ速度
            <Typography component="span" variant="body2" color="text.secondary">
              {formatSpeedScale(speedScale)}
            </Typography>
          </Typography>
          <Slider
            aria-label="読み上げ速度"
            value={speedScale}
            min={SPEED_SCALE_MIN}
            max={SPEED_SCALE_MAX}
            step={SPEED_SCALE_STEP}
            marks={SPEED_MARKS}
            valueLabelDisplay="auto"
            valueLabelFormat={formatSpeedScale}
            onChange={(_event, value) => onChangeSpeedScale(value as number)}
            sx={{ maxWidth: 420 }}
          />
          <Typography variant="body2" color="text.secondary">
            ずんだもんの読み上げの速さです。次の読み上げから反映され、この端末に保存されます。
            音声合成が使えない環境のブラウザ読み上げにも同じ速さが効きます。
          </Typography>
        </Box>
      </Stack>
    </Paper>
  );
}
