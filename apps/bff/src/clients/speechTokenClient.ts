/**
 * Azure Speech (ADR 0022) 向けの一時 authorization token を発行する。
 *
 * 静的シークレットは配らない (ADR 0006 系譜)。Speech リソースは
 * `disableLocalAuth: true` で、認証は Functions の Managed Identity が取得する
 * Entra トークンのみ。Speech SDK にはこれを `aad#{resourceId}#{entraToken}`
 * 形式で渡す (custom subdomain 必須 — bicep 側で設定)。
 *
 * フォールバック設計:
 *   - SPEECH_RESOURCE_ID / SPEECH_REGION 未設定 (未プロビジョニング環境) → available: false
 *   - MSI が無い (ローカル開発) → available: false
 *   どちらもフロントが Web Speech API に静かにフォールバックするためのシグナル。
 *   MSI はあるのに取得に失敗した場合は**握り潰さず throw** する (serviceToken と同方針 —
 *   本番でトークンが取れないのは異常であり、黙って劣化させると原因が追えなくなる)。
 */

import { config } from "../config";
import { getServiceToken } from "./serviceToken";

/** Cognitive Services 共通の Entra トークン audience。 */
const COGNITIVE_SERVICES_RESOURCE = "https://cognitiveservices.azure.com";

export type SpeechTokenResult =
  | { available: false; authToken: null; region: null }
  | { available: true; authToken: string; region: string };

const UNAVAILABLE: SpeechTokenResult = {
  available: false,
  authToken: null,
  region: null,
};

export async function issueSpeechAuthToken(): Promise<SpeechTokenResult> {
  const { speechResourceId, speechRegion } = config;
  if (!speechResourceId || !speechRegion) {
    // Speech 未プロビジョニング環境。フロントは Web Speech へ。
    return UNAVAILABLE;
  }

  const entraToken = await getServiceToken(COGNITIVE_SERVICES_RESOURCE);
  if (!entraToken) {
    // ローカル (MSI 無し)。ローカルで Azure Speech は使わない。
    return UNAVAILABLE;
  }

  return {
    available: true,
    authToken: `aad#${speechResourceId}#${entraToken}`,
    region: speechRegion,
  };
}
