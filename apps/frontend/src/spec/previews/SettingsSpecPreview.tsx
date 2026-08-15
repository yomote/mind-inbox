import * as React from "react";
import { SettingsScreen } from "../../components/screens/SettingsScreen";
import { SPEED_SCALE_DEFAULT } from "../../voice/speedScale";

export function SettingsSpecPreview() {
  // プレビューは実設定を書き換えない (仕様を見に来ただけで読み上げ速度が変わらないように)。
  const [speedScale, setSpeedScale] = React.useState(SPEED_SCALE_DEFAULT);

  return (
    <SettingsScreen
      themeMode="light"
      onToggleTheme={() => {}}
      speedScale={speedScale}
      onChangeSpeedScale={setSpeedScale}
    />
  );
}
