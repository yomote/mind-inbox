import { defineConfig, devices } from "@playwright/test";

/**
 * L4 live E2E — **実環境に対する UI シナリオテスト** (#108 / ADR 0018)。
 *
 * mock 版 (playwright.config.ts) との違い:
 *   - フロントは production ビルドをローカルで配信するが、**VITE_BFF_BASE_URL に
 *     実環境の Functions を焼き込む** → tRPC / TTS が本物の BFF → ai-agent (実 AI) →
 *     vv-wrap (実 VOICEVOX) に到達する
 *   - 認証: `VITE_ENTRA_*` は渡さず MSAL のログイン UI は無効化し、代わりに
 *     Playwright の route インターセプトで **実トークンを Authorization に注入**する
 *     (spec 側で実装)。CI は gha-oidc SP の client credentials で取得
 *     (runbook: container-apps-auth-gate.md)
 *
 * 検証できないもの: MSAL の実ログイン往復 (ブラウザ UI での Entra ログイン) と
 * SWA の静的配信そのもの。前者は人間の初回確認、後者は smoke-test が担う。
 *
 * 必要な env:
 *   LIVE_BFF_URL   — 実環境 BFF (例: https://func-dev-mindbox.azurewebsites.net)
 *   LIVE_BFF_TOKEN — その BFF の EasyAuth を通るアクセストークン
 */

const PORT = Number(process.env.E2E_PORT || 4180);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e-live",
  outputDir: "./test-results-live",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // 実 AI + コールドスタートを挟むため余裕を持たせる
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 30_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL: BASE_URL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    locale: "ja-JP",
  },

  projects: [
    {
      name: "chromium-live",
      use: {
        ...devices["Desktop Chrome"],
        ...(process.env.E2E_CHROMIUM_PATH
          ? { launchOptions: { executablePath: process.env.E2E_CHROMIUM_PATH } }
          : {}),
      },
    },
  ],

  webServer: {
    command: `pnpm exec vite build --mode production && pnpm exec vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      VITE_USE_MOCK: "false",
      VITE_BFF_BASE_URL: process.env.LIVE_BFF_URL ?? "",
      // VITE_ENTRA_* は意図的に渡さない (ログイン UI を無効化し、トークンは route 注入)
    },
  },
});
