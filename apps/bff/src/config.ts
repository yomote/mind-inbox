/**
 * 環境変数を一元管理する設定モジュール。
 * 未設定の場合は undefined を返し、各 client が stub fallback を判断する。
 */
export const config = {
  aiAgentBaseUrl: process.env["AI_AGENT_BASE_URL"] || undefined,
  voicevoxBaseUrl: process.env["VOICEVOX_BASE_URL"] || undefined,
  // 下流 Container Apps の組み込み認証 (#86 / ADR 0017) 向け audience。
  // 未設定なら Authorization を付けない = 門を立てていない環境・ローカルではそのまま通る。
  aiAgentAudience: process.env["AI_AGENT_AUDIENCE"] || undefined,
  voicevoxAudience: process.env["VOICEVOX_AUDIENCE"] || undefined,
  // Azure Speech (ADR 0023)。未設定なら speech.issueToken が available:false を返し、
  // フロントは Web Speech にフォールバックする。
  speechResourceId: process.env["SPEECH_RESOURCE_ID"] || undefined,
  speechRegion: process.env["SPEECH_REGION"] || undefined,
  // Cosmos DB (ADR 0030)。**COSMOS_ENDPOINT が真偽の分岐点** — 未設定なら in-memory
  // リポジトリのまま動く (ローカル / テストの既定 = ADR 0030 D7)。
  // 接続文字列やキーは持たない (disableLocalAuth: true / D3)。認証は Functions の
  // Managed Identity + data plane RBAC のみ。
  cosmosEndpoint: process.env["COSMOS_ENDPOINT"] || undefined,
  cosmosDatabase: process.env["COSMOS_DATABASE"] || "mindinbox",
  cosmosProblemsContainer: process.env["COSMOS_PROBLEMS_CONTAINER"] || "problems",
} as const;

export type Config = typeof config;
