/**
 * 下流サービス (ai-agent / vv-wrap) の warmup ping (#120)。
 *
 * scale-to-zero (ADR 0013) の Container App はアイドル後の初回リクエストで
 * コールドスタート数十秒を食う。ホーム表示中にフロントが /api/warmup を
 * fire-and-forget で叩き、対話開始前にレプリカを起こしておく。
 *
 * 認証整合 (ADR 0017): /api/warmup 自体は Functions の EasyAuth の内側にあり
 * (フロントは Authorization を付けて呼ぶ)、下流へは BFF の Managed Identity
 * トークン (serviceHeaders) で「門」を通る。無認可の warm 経路は作らない。
 */

import { config } from "../config";
import { serviceHeaders } from "../clients/serviceToken";

export type WarmupTargetResult = {
  /** ok = 応答あり / stub = URL 未設定 (ローカル等) / error = 失敗 or タイムアウト */
  status: "ok" | "stub" | "error";
  /** 応答までの ms (stub は null)。コールドスタートの実測値として #120 の計測にも使える。 */
  ms: number | null;
  httpStatus: number | null;
};

export type WarmupResult = {
  aiAgent: WarmupTargetResult;
  voicevox: WarmupTargetResult;
};

/** コールドスタート (数十秒) を待ち切れる程度に長め。フロントは結果を待たなくてよい。 */
const WARMUP_TIMEOUT_MS = 60_000;

async function pingHealth(
  baseUrl: string | undefined,
  audience: string | undefined,
  timeoutMs: number,
): Promise<WarmupTargetResult> {
  if (!baseUrl) {
    return { status: "stub", ms: null, httpStatus: null };
  }

  const started = Date.now();
  try {
    const res = await fetch(`${baseUrl}/health`, {
      method: "GET",
      headers: await serviceHeaders(audience),
      signal: AbortSignal.timeout(timeoutMs),
    });
    return {
      status: res.ok ? "ok" : "error",
      ms: Date.now() - started,
      httpStatus: res.status,
    };
  } catch (err) {
    console.warn(`[warmup] ping ${baseUrl}/health failed: ${(err as Error).message}`);
    return { status: "error", ms: Date.now() - started, httpStatus: null };
  }
}

/** ai-agent / vv-wrap の /health を並行で叩いてコールドスタートを誘発する。 */
export async function warmupDownstreams(
  timeoutMs: number = WARMUP_TIMEOUT_MS,
): Promise<WarmupResult> {
  const [aiAgent, voicevox] = await Promise.all([
    pingHealth(config.aiAgentBaseUrl, config.aiAgentAudience, timeoutMs),
    pingHealth(config.voicevoxBaseUrl, config.voicevoxAudience, timeoutMs),
  ]);
  return { aiAgent, voicevox };
}
