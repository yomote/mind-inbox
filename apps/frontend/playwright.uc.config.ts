import { defineConfig, devices } from "@playwright/test";

/**
 * L3-real — **ユースケース受け入れテスト** (UC-01〜UC-05 / ADR 0032)。
 *
 * mock 版 (playwright.config.ts) との決定的な違いは、**mockApi を通らないこと**。
 *
 *   mock 版:  ブラウザ ── mockApi (フロント内の配列)
 *   ここ:     ブラウザ ── 実フロント ── 実 BFF (tRPC / SSE) ── ai-agent ダブル
 *
 * mock 版が緑でも「real の api 層 (src/api/*.ts) → tRPC → BFF の router →
 * Problem リポジトリ」は一度も動いていない。実際、実環境で踏んだ
 * 「ボタンを押しても何も起きない」(2026-08-09) は mock 版・単体テストの両方が
 * 緑のまますり抜けた。この層が埋めるのはその穴。
 *
 * 差し替えているのは **LLM の判断だけ** (e2e-uc/fake-ai-agent.mjs)。
 * 実 AI の応答品質・実 VOICEVOX の音声・Entra の実ログインは L4 (playwright.live.config.ts) が見る。
 *
 * 検証できないもの: 永続化 (BFF は in-memory / #165)、Azure 固有の配信・認証。
 */

const AI_AGENT_PORT = Number(process.env.E2E_UC_AI_AGENT_PORT || 8099);
const BFF_PORT = Number(process.env.E2E_UC_BFF_PORT || 7097);
const APP_PORT = Number(process.env.E2E_UC_APP_PORT || 4175);

const AI_AGENT_URL = `http://127.0.0.1:${AI_AGENT_PORT}`;
const BFF_URL = `http://127.0.0.1:${BFF_PORT}`;
const APP_URL = `http://127.0.0.1:${APP_PORT}`;

export default defineConfig({
  testDir: "./e2e-uc",
  outputDir: "./test-results-uc",
  // BFF の Problem リポジトリはプロセス 1 本の in-memory を全 spec で共有する。
  // 並列で走らせると互いの困りごとを見てしまうため直列に固定する
  // (spec ごとに題材トピックを分けているので、直列なら順序には依存しない)。
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: APP_URL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    locale: "ja-JP",
  },

  projects: [
    {
      name: "chromium-uc",
      use: {
        ...devices["Desktop Chrome"],
        ...(process.env.E2E_CHROMIUM_PATH
          ? { launchOptions: { executablePath: process.env.E2E_CHROMIUM_PATH } }
          : {}),
      },
    },
  ],

  webServer: [
    {
      // LLM の代役。決定的にグルーピングするので UC-03 (再出現) / UC-04 (再燃) が踏める。
      command: `node e2e-uc/fake-ai-agent.mjs`,
      url: `${AI_AGENT_URL}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: { PORT: String(AI_AGENT_PORT) },
    },
    {
      // 本物の BFF。Azure Functions Core Tools は使わず、同じ http/handlers.ts を
      // 素の node で配信する (apps/bff/scripts/local-server.mjs)。
      command: `npm --prefix ../bff run build && node ../bff/scripts/local-server.mjs`,
      url: `${BFF_URL}/api/trpc/health.ping`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        PORT: String(BFF_PORT),
        AI_AGENT_BASE_URL: AI_AGENT_URL,
        // VOICEVOX_BASE_URL は渡さない = /api/tts は 204。
        // 音声はこの層の関心ではない (L4 が見る)。
      },
    },
    {
      // **VITE_USE_MOCK を渡さない** のがこの層の肝。mockApi ではなく tRPC 経由で BFF を叩く。
      // VITE_ENTRA_* も渡さないので認証ゲートは開き、ユースケースだけに集中できる。
      command:
        `pnpm exec vite build --mode production --outDir dist-e2e-uc && ` +
        `pnpm exec vite preview --host 127.0.0.1 --port ${APP_PORT} --strictPort --outDir dist-e2e-uc`,
      url: APP_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { VITE_BFF_BASE_URL: BFF_URL },
    },
  ],
});
