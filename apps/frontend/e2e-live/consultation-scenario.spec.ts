import { expect, test } from "@playwright/test";
import { isTtsSynthesisBody } from "../src/api/ttsCallKind";
import { fakeEntraLogin, LIVE_BFF_URL, liveEnvMissing } from "./entra-login";

/**
 * [L4] 相談ユースケースのシナリオテスト — **デプロイ済みの実環境**。
 *
 * ゴールデンパス: (デプロイ済み SWA を開く) → 認証 → 相談開始 → AI の問いかけ →
 * 発話 → **実 AI の返事** → **実 VOICEVOX の音声取得**。
 *
 * 実際に配信されているバンドルを実オリジンで操作するため、以下の実事故クラスを検知する:
 *   - VITE_* 焼き込みミス (認証無効ビルドの出荷, #78)
 *   - フロント↔BFF の結線破壊 (TTS のオリジン違い, 2026-08-08)
 *   - 実オリジンの CORS / EasyAuth / 下流 Container Apps の門 (403/401 系)
 *
 * 認証の偽装は entra-login.ts (ux-probe.spec.ts と共通)。
 * 実ログイン往復そのものは login-canary.spec.ts と人間の初回確認が担当。
 */

test.skip(liveEnvMissing, "LIVE_* env が未設定 (実環境向けのみ実行)");

test("[L4] 相談 → 実AIの返事 → 実VOICEVOXの音声 まで デプロイ済み UI で通る", async ({ page }) => {
  // 落ちたときの一次情報。CORS ブロック等はブラウザ console にしか出ず、
  // CI では trace を開けないためログへ流しておく
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log(`[browser:error] ${msg.text()}`);
  });
  page.on("pageerror", (err) => console.log(`[browser:pageerror] ${err.message}`));

  await fakeEntraLogin(page);

  // 未認証で開くとオンボーディングが表示される (#112 / onboarding.mdx)。
  // 「はじめる」でサインインが始まり、偽装 Entra が即座に認証済みで返すため
  // そのままホームに到達する
  await page.goto("/");
  await page.getByRole("button", { name: "はじめる" }).click({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible({
    timeout: 60_000,
  });

  // ホーム → 直接セッション開始 (home.mdx)。開始時の問いかけは AI 非呼び出しで即表示
  await page.getByRole("button", { name: "新しい相談を始める" }).click();
  await expect(page).toHaveURL(/\/consultations\/current$/);
  await expect(page.getByText(/気になっていること|気になっています/)).toBeVisible({
    timeout: 60_000,
  });

  // 発話 → 実 AI の返事。TTS (実 VOICEVOX) は返事の描画後に非同期で走るため、
  // 先にレスポンス待ちを仕掛けてから送信する
  // `POST /api/tts` は 3 種類ある — 本合成 (audio/wav) / `plan: true` (文分割だけ・
  // application/json, #185) / `prefetch: true` (先行合成・202, #132)。待ちたいのは
  // **本合成だけ**。
  //
  // ここは「既知の変種を除外する」書き方で **2 回落ちている** — #132 の prefetch を
  // 除外した後に #185 の plan が増え、それを掴んで `audio/wav` を期待して落ちた
  // (deploy 3 連敗 / golden-path-monitor 2 晩)。除外リストは新しい変種が増えるたびに
  // 黙って古くなるので、**本合成の形を積極的に指定する**方式に変える。
  // 判定は `src/api/ttsCallKind.ts` が唯一の定義 (L1 テストが、実際のクライアント
  // 3 種のうち本合成はちょうど 1 つ、を固定している)。
  const ttsResponse = page.waitForResponse(
    (res) =>
      res.url().startsWith(`${LIVE_BFF_URL}/api/tts`) &&
      res.request().method() === "POST" &&
      isTtsSynthesisBody(res.request().postData()),
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

  // stub 応答 ("[stub] received: ...") も「ガイドの返事」として描画されるため、
  // 上の可視性チェックだけでは BASE_URL 結線切れが緑ですり抜ける (2026-08-08 実障害)
  await expect(page.getByText(/\[stub\]/)).toHaveCount(0);

  // 実 VOICEVOX: /api/tts が **実 BFF ホスト宛に** 呼ばれ、audio (WAV) が返ること。
  // 相対 /api/tts に退行すると waitForResponse がタイムアウトして落ちる。
  // 検証はステータス + Content-Type まで: 200 + audio/wav は BFF が実 wrapper から
  // 音声を得た場合のみ返る (未結線 = 204 / wrapper 失敗 = 502)。バイト列の実体
  // (RIFF マジック・サイズ) は curl 版 golden-path hop5 が毎回検証しており、
  // ここで response.body() を読むとページ側の blob 消費と競合して 0 バイトになる
  // (deploy run #93 の実落ち方 — status/headers の検証は競合しない)
  const tts = await ttsResponse;
  expect(tts.status(), "tts status").toBe(200);
  expect(tts.headers()["content-type"] ?? "", "tts content-type").toContain("audio/wav");

  // ここまでは「WAV が返った」だけで、**実際にずんだもんの声で鳴ったか**は別問題。
  // 2026-08-08 の実障害は 200 + audio/wav のまま、無言でブラウザ読み上げ (別の声)
  // に置き換わっていた (#150)。アプリが公開する実際の出力経路を検証して、
  // この「静かな置換」を検知する。
  await expect(
    page.locator("[data-voice-output]"),
    "実際の音声出力経路 (browser-fallback = ずんだもん以外の声に落ちている)",
  ).toHaveAttribute("data-voice-output", "voicevox", { timeout: 30_000 });
});
