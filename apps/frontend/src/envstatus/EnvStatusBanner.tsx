import { Alert, Link } from "@mui/material";
import { statusPageUrl } from "./useEnvStatus";
import type { EnvStatus } from "./useEnvStatus";

/**
 * 環境の不安定さをユーザーが触る前に伝えるバナー (env-status-banner.mdx)。
 * ok / unknown では何も描画しない — 正常時に常設 UI を増やさない。
 */
export function EnvStatusBanner({ status }: { status: EnvStatus }) {
  if (status !== "deploying" && status !== "broken") return null;

  const repo = import.meta.env.VITE_ENV_STATUS_REPO ?? "";

  if (status === "deploying") {
    return (
      <Alert severity="warning" data-env-status="deploying" sx={{ borderRadius: 0 }}>
        デプロイ進行中です — 数分間、応答が不安定になることがあります
      </Alert>
    );
  }

  return (
    <Alert severity="error" data-env-status="broken" sx={{ borderRadius: 0 }}>
      直近のデプロイ検証が失敗しています — 一部の機能が動かない可能性があります（
      <Link href={statusPageUrl(repo)} target="_blank" rel="noreferrer">
        状況ページ
      </Link>
      ）
    </Alert>
  );
}
