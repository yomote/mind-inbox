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

/**
 * VOICEVOX 合成 (BFF /api/tts)。失敗時の判定は呼び出し側 (204 = stub 等)。
 *
 * `speedScale` (#242) は**必須引数**にしてある。省略可能にすると、呼び出し側が
 * 渡し忘れても等倍で普通に鳴るため「設定画面のスライダーが効かない」が型でも
 * テストでも捕まらない (音は出るので E2E も緑)。
 */
export async function ttsFetch(
  text: string,
  speaker: number,
  speedScale: number,
): Promise<Response> {
  return fetch(`${bffBaseUrl()}/api/tts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await bffAuthHeaders()),
    },
    body: JSON.stringify({ text, speaker, speedScale }),
  });
}

/**
 * 読み上げ単位の文分割だけを BFF に問い合わせる (BFF /api/tts plan=true / #185)。
 * 音声は返らない。200 = `{ sentences: string[] }` / 204 = VOICEVOX 未構成 (stub)。
 *
 * ここだけ `speedScale` を送らない (#242): 返るのは文字列の並びだけで、合成もキャッシュも
 * 起きないため速度は結果に影響しない。送ると「速度が効く呼び出し」が 1 つ増えたように見える。
 */
export async function ttsPlanFetch(text: string, speaker: number): Promise<Response> {
  return fetch(`${bffBaseUrl()}/api/tts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await bffAuthHeaders()),
    },
    body: JSON.stringify({ text, speaker, plan: true }),
  });
}

/**
 * TTS 文単位プリフェッチ (BFF /api/tts prefetch=true / #120)。
 * ストリーミング中に確定した文を BFF 側で先行合成・キャッシュさせる。音声は返らない (202/204)。
 *
 * `speedScale` を**本合成と同じ値で送る** (#242)。BFF のキャッシュキーは速度込みなので、
 * ここだけ等倍で焼くと本合成が全ミスし、先行合成が無言で無効化される。
 */
export async function ttsPrefetchFetch(
  text: string,
  speaker: number,
  speedScale: number,
): Promise<Response> {
  return fetch(`${bffBaseUrl()}/api/tts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await bffAuthHeaders()),
    },
    body: JSON.stringify({ text, speaker, speedScale, prefetch: true }),
  });
}

/** チャット応答の SSE ストリーミング (BFF /api/chat/stream / #120, ADR 0024)。 */
export async function chatStreamFetch(sessionId: string, message: string): Promise<Response> {
  return fetch(`${bffBaseUrl()}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(await bffAuthHeaders()),
    },
    body: JSON.stringify({ sessionId, message }),
  });
}

/** scale-to-zero の下流を温める warmup ping (BFF /api/warmup / #120)。 */
export async function warmupFetch(): Promise<Response> {
  return fetch(`${bffBaseUrl()}/api/warmup`, {
    method: "GET",
    headers: await bffAuthHeaders(),
  });
}
