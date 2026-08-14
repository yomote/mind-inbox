import { defineConfig, devices } from "@playwright/test";

/**
 * L4 live E2E — **デプロイ済みの実環境 (SWA) に対する UI シナリオテスト** (#108 / ADR 0018)。
 *
 * mock 版 (playwright.config.ts) との違い:
 *   - **実際にデプロイされた SWA の配信バンドル**を開く (ローカルビルドではない)。
 *     これにより VITE_* の焼き込みミス (認証無効ビルドの出荷, #78 の事故クラス) や
 *     SWA 配信・実オリジン CORS の破壊も検知範囲に入る
 *   - 認証: MSAL の localStorage キャッシュに実トークンを注入してログイン画面を迂回する
 *     (spec 参照)。**ログイン往復そのもの**は login-canary が Entra のログイン画面表示まで
 *     を検証し、完走は人間の初回確認と分担する
 *
 * 必要な env:
 *   LIVE_APP_URL         — デプロイ済み SWA (例: https://calm-field-xxx.azurestaticapps.net)
 *   LIVE_BFF_URL         — 実環境 BFF (例: https://func-dev-mindbox.azurewebsites.net)
 *   LIVE_BFF_TOKEN       — BFF の EasyAuth を通るアクセストークン
 *   LIVE_ENTRA_CLIENT_ID — SPA の client ID (MSAL キャッシュ注入に使用)
 *   LIVE_ENTRA_TENANT_ID — テナント ID (同上)
 */

export default defineConfig({
  testDir: "./e2e-live",
  outputDir: "./test-results-live",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // 実 AI + コールドスタートを挟むため余裕を持たせる
  timeout: 240_000,
  expect: { timeout: 30_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL: process.env.LIVE_APP_URL ?? "",
    // **アクションに上限を持たせる** (#287)。既定 (未設定) は 0 = 無期限で、
    // `fill` / `click` が actionable 待ちのまま止まっても**そのアクションは落ちない**。
    // 落ちるのは同時に走っている別の待受で、赤の名前が止まった場所とずれる —
    // 2026-08-11〜13 の golden-path-monitor / deploy の連敗は、送信手前で止まったものが
    // 「SSE `/api/chat/stream` が返らない」として報告され、2 日間 SSE を疑わせた (#293)。
    // 有限にすると Playwright の call log (どの actionability を待っていたか) が
    // job ログに出る。trace は ADR 0045 で暗号化されていて agent は読めないので、
    // 平文のログに理由が出るかどうかが「調査できる / できない」を分ける。
    // 長時間の待ちは各 spec が明示 timeout で持っている (実 AI 応答・コールドスタート)。
    actionTimeout: 60_000,
    screenshot: "only-on-failure",
    // sources: false — trace に **spec のソースコードを同梱しない** (ADR 0045 D8)。
    // 2026-08-12 の実測で、trace の resources/src@*.txt にテストの中身がそのまま
    // 入っていることを確認した。spec にハードコードされた秘密はそれだけで trace に
    // 載るので、実トークンを扱う live 設定では落とす。デバッグに要るのは実行時の
    // DOM とアクション記録で、ソースはリポジトリ側で読める。
    trace: { mode: "retain-on-failure", snapshots: true, screenshots: true, sources: false },
    locale: "ja-JP",
  },

  projects: [
    {
      name: "chromium-live",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          // Playwright の既定は --autoplay-policy=no-user-gesture-required で、
          // **実ブラウザでは起きうる自動再生ブロックが原理的に再現しない**。
          // 2026-08-08 に「/api/tts は 200 なのに実機ではずんだもんではない声になる」
          // 障害が E2E 全緑のまますり抜けた (#150)。実ブラウザと同じポリシーに戻し、
          // 再生ブロックがテストで踏めるようにする。
          args: ["--autoplay-policy=user-gesture-required"],
          ...(process.env.E2E_CHROMIUM_PATH
            ? { executablePath: process.env.E2E_CHROMIUM_PATH }
            : {}),
        },
      },
    },
  ],
});
