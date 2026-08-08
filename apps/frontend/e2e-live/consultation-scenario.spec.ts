import { expect, test, type Page } from "@playwright/test";

/**
 * [L4] 相談ユースケースのシナリオテスト — 実環境 (実 BFF / 実 AI / 実 VOICEVOX)。
 *
 * ゴールデンパス: ホーム → 相談開始 → AI の問いかけ → 発話 → **実 AI の返事** →
 * **実 VOICEVOX の音声取得**。UI の実コード (production ビルド) を Playwright で
 * 操作するので、「API は生きているのにフロントの結線が壊れている」型のバグ
 * (例: TTS が SWA オリジンに投げていた 2026-08-08 の事象) を検知できる。
 *
 * 無いと何が静かに通るか: curl ベースの golden-path.sh は API ホップしか見ないため、
 * フロント↔BFF の結線 (ベース URL / Authorization / CORS / レスポンス整形) の破壊が
 * 全部緑のまま出荷される。
 */

const LIVE_BFF_URL = process.env.LIVE_BFF_URL ?? "";
const LIVE_BFF_TOKEN = process.env.LIVE_BFF_TOKEN ?? "";

test.skip(
  !LIVE_BFF_URL || !LIVE_BFF_TOKEN,
  "LIVE_BFF_URL / LIVE_BFF_TOKEN が未設定 (実環境向けのみ実行)",
);

/** 実 BFF 宛のリクエストに実トークンを注入する (MSAL ログイン UI の代替)。 */
async function injectAuth(page: Page) {
  await page.route(`${LIVE_BFF_URL}/**`, async (route) => {
    const headers = {
      ...route.request().headers(),
      authorization: `Bearer ${LIVE_BFF_TOKEN}`,
    };
    await route.continue({ headers });
  });
}

test("[L4] 相談 → 実AIの返事 → 実VOICEVOXの音声 まで UI で通る", async ({ page }) => {
  await injectAuth(page);

  // 認証なしビルドは "/" からホームへ (mock 版 e2e と同じ入口)
  await page.goto("/");
  await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible();

  // ホーム → 直接セッション開始 (home.mdx)。開始時の問いかけは AI 非呼び出しで即表示
  await page.getByRole("button", { name: "新しい相談を始める" }).click();
  await expect(page).toHaveURL(/\/consultations\/current$/);
  await expect(page.getByText(/気になっていること|気になっています/)).toBeVisible({
    timeout: 60_000,
  });

  // 発話 → 実 AI の返事。TTS (実 VOICEVOX) は返事の描画後に非同期で走るため、
  // 先にレスポンス待ちを仕掛けてから送信する
  const ttsResponse = page.waitForResponse(
    (res) => res.url().startsWith(`${LIVE_BFF_URL}/api/tts`) && res.request().method() === "POST",
    { timeout: 210_000 },
  );

  const composer = page.getByPlaceholder("ここに入力 / 話して入力");
  await composer.fill("ゴールデンパスのシナリオテストです。一言だけ返してください");
  await page.getByRole("button", { name: "送信" }).click();

  // 発話が消えない (送信後の state 更新) + 実 AI の返事が増える。
  // コールドスタート (ai-agent 起動 + OpenAI) を許容して長めに待つ
  await expect(
    page.getByText("ゴールデンパスのシナリオテストです。一言だけ返してください"),
  ).toBeVisible();
  await expect(page.locator("text=ガイド").nth(1)).toBeVisible({ timeout: 210_000 });

  // 実 VOICEVOX: /api/tts が **実 BFF ホスト宛に** 呼ばれ、audio (WAV) が返ること。
  // 相対 /api/tts に退行すると waitForResponse がタイムアウトして落ちる
  const tts = await ttsResponse;
  expect(tts.status(), "tts status").toBe(200);
  const ttsBody = await tts.body();
  expect(ttsBody.length, "tts body size").toBeGreaterThan(1000);
  expect(ttsBody.subarray(0, 4).toString("ascii"), "WAV magic").toBe("RIFF");
});
