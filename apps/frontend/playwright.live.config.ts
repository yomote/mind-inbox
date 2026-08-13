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
    // 既定の actionTimeout は **0 (無制限)**。操作の actionability
    // (visible / enabled / editable) がいつまでも満たされないと、その操作は
    // 黙って待ち続け、**先に鳴った別の待受のタイムアウト**がエラーとして表に出る。
    // 2026-08-13 の deploy 連敗 (#262 / #287) が実例で、実際は
    // `composer.fill()` が 210 秒間完了していなかったのに、表に出たのは
    // 「`/api/chat/stream` の応答が来ない」という**無関係な**タイムアウトだった
    // (fill 側は "Target page ... has been closed" として 2 番目に出るだけ)。
    // 3 回の当番診断が原因の切り分けに失敗している。
    // 上限を入れると Playwright は「どの actionability を待っていたか」の call log
    // つきで落ちるので、**待っていた条件そのもの**がログに残る。
    // 60 秒は live の実操作 (fill / click) の余裕として十分 — 要素の出現待ちは
    // 各 spec が明示 timeout つきの expect / waitFor で先に取っている。
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
