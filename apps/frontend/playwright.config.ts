import { defineConfig, devices } from "@playwright/test";

/**
 * L3 (ブラウザ) — ローカルで完結する UI 検証 (ADR 0018)。
 *
 * 前提を 2 つ固定している:
 *
 * 1. **mock モードの production ビルドを配信する** (dev サーバではない)。
 *    dev では `import.meta.env.DEV` が真になり、`VITE_USE_MOCK` の値に関係なく
 *    認証ゲートがスキップされる (Layout.tsx の `standalone`)。つまり dev サーバを
 *    使うと「mock フラグが効いているか」を検証できない。ここで検証したいのは
 *    GitHub Pages に出している mock デモと同じ経路なので、build → preview にする。
 *    production バンドルを実際に起動するので、「import 時に window を触る」類の
 *    バンドル固有の事故 (#69 で実際に踏んだ) もここで落ちる。
 *
 * 2. **認証環境変数を渡さない**。`VITE_ENTRA_*` が未設定なら MSAL は初期化されず、
 *    ネットワークにも Entra にも触らない。CI・ローカルどちらでも同じ結果になる。
 *
 * 検証できないもの (人の目が必要): MSAL の実ログイン往復 / 実 AI 応答 / VOICEVOX 音声。
 */

const PORT = Number(process.env.E2E_PORT || 4173);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  // 各テストは独立した状態から始まる (mock は module スコープの配列を持つため、
  // 副作用のある操作を並列で走らせると互いのデータを見てしまう)。
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  timeout: 30_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: BASE_URL,
    // 失敗時だけ証跡を残す (成功時のスクリーンショットは screens.spec.ts が明示的に撮る)。
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    locale: "ja-JP",
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // 既定は Playwright 管理のブラウザ (`pnpm exec playwright install chromium`)。
        // ブラウザを自前で持っている環境 (CDN に出られないコンテナ等) は
        // E2E_CHROMIUM_PATH に実行ファイルのパスを渡して差し替える。
        ...(process.env.E2E_CHROMIUM_PATH
          ? { launchOptions: { executablePath: process.env.E2E_CHROMIUM_PATH } }
          : {}),
      },
    },
  ],

  webServer: {
    // tsc は lint-and-build ジョブが見ているので、ここは vite build だけ回す (起動を速く保つ)。
    // --host 127.0.0.1 は必須。既定 (localhost) だと OS / Node の DNS 解決順で
    // `::1` だけにバインドされることがあり、Playwright が待つ 127.0.0.1 に繋がらない
    // (実際 GitHub runner でだけ webServer 起動待ちが 120s タイムアウトした)。
    command: `pnpm exec vite build --mode production && pnpm exec vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      VITE_USE_MOCK: "true",
      // VITE_ENTRA_CLIENT_ID / VITE_ENTRA_TENANT_ID は **意図的に渡さない** (認証なしの検証のため)。
    },
  },
});
