import { useSyncExternalStore } from "react";
import { Alert } from "@mui/material";
import { getStubbedResponse, subscribeStubbedResponse } from "../api/stubStatus";

/**
 * BFF が stub 応答 (AI サービス未結線のフォールバック) を返しているときの警告バナー
 * (#146 / dialogue-session.mdx §5.7)。「本物のふりをした偽応答」を無言で表示しない。
 *
 * 正常時 (実応答 / mock デモ) は何も描画しない — EnvStatusBanner と同じ流儀で、
 * 正常時に常設 UI を増やさない。
 */
export function StubResponseBanner() {
  const stubbed = useSyncExternalStore(
    subscribeStubbedResponse,
    getStubbedResponse,
    getStubbedResponse,
  );
  if (!stubbed) return null;

  return (
    <Alert severity="warning" data-stub-banner="visible" sx={{ borderRadius: 0 }}>
      AI サービス未接続 — スタブ応答を表示中
    </Alert>
  );
}
