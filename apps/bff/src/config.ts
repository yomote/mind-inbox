/**
 * 環境変数を一元管理する設定モジュール。
 * 未設定の場合は undefined を返し、各 client が stub fallback を判断する。
 */
export const config = {
  aiAgentBaseUrl: process.env["AI_AGENT_BASE_URL"] || undefined,
  voicevoxBaseUrl: process.env["VOICEVOX_BASE_URL"] || undefined,
} as const;

export type Config = typeof config;
