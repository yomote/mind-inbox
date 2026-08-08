import { authEnabled, getAccessToken } from "../auth/msal";

/**
 * mock モード (VITE_USE_MOCK=true): BFF を呼ばず mockApi で自己完結するデモビルド (ADR 0004)。
 * 判定はビルド時 (Vite の静的置換)。宣言はここ 1 箇所 — api 各モジュールはこれを import する。
 */
export const useMock = import.meta.env.VITE_USE_MOCK === "true";

/**
 * BFF (Functions) への素の fetch を行うための共通ヘルパー。
 *
 * SWA Free には linked backend が無いため、フロントは **常に BFF のホストを前置**して
 * 直叩きする (#69)。相対パスのまま fetch すると SWA オリジンに飛んで必ず失敗する
 * (TTS で実際に起きた事故: 2026-08-08)。tRPC (trpc/client.ts) と非 tRPC (tts) の
 * 両方がここを使うことで、結線ロジックの二重実装を防ぐ。
 */
export function bffBaseUrl(): string {
  return import.meta.env.VITE_BFF_BASE_URL ?? "";
}

/** EasyAuth の門を通るための Authorization ヘッダ (認証無効ビルドでは空)。 */
export async function bffAuthHeaders(): Promise<Record<string, string>> {
  if (!authEnabled) return {};
  const token = await getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** VOICEVOX 合成 (BFF /api/tts)。失敗時の判定は呼び出し側 (204 = stub 等)。 */
export async function ttsFetch(text: string, speaker: number): Promise<Response> {
  return fetch(`${bffBaseUrl()}/api/tts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await bffAuthHeaders()),
    },
    body: JSON.stringify({ text, speaker }),
  });
}
