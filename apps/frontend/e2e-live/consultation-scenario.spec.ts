import { expect, test, type Page } from "@playwright/test";

/**
 * [L4] 相談ユースケースのシナリオテスト — **デプロイ済みの実環境**。
 *
 * ゴールデンパス: (デプロイ済み SWA を開く) → ホーム → 相談開始 → AI の問いかけ →
 * 発話 → **実 AI の返事** → **実 VOICEVOX の音声取得**。
 *
 * 実際に配信されているバンドルを実オリジンで操作するため、以下の実事故クラスを検知する:
 *   - VITE_* 焼き込みミス (認証無効ビルドの出荷, #78)
 *   - フロント↔BFF の結線破壊 (TTS のオリジン違い, 2026-08-08)
 *   - 実オリジンの CORS / EasyAuth / 下流 Container Apps の門 (403/401 系)
 *
 * 認証: MSAL の localStorage キャッシュ形式で「アカウント + 実アクセストークン」を
 * 注入し、ログイン画面を迂回する (実ログイン往復は login-canary.spec.ts と人間の
 * 初回確認が担当)。トークンは実物なので EasyAuth の検証はフルに通る。
 */

const LIVE_APP_URL = process.env.LIVE_APP_URL ?? "";
const LIVE_BFF_URL = process.env.LIVE_BFF_URL ?? "";
const LIVE_BFF_TOKEN = process.env.LIVE_BFF_TOKEN ?? "";
const CLIENT_ID = process.env.LIVE_ENTRA_CLIENT_ID ?? "";
const TENANT_ID = process.env.LIVE_ENTRA_TENANT_ID ?? "";

test.skip(
  !LIVE_APP_URL || !LIVE_BFF_URL || !LIVE_BFF_TOKEN || !CLIENT_ID || !TENANT_ID,
  "LIVE_* env が未設定 (実環境向けのみ実行)",
);

/**
 * MSAL (msal-browser, cacheLocation=localStorage) のキャッシュ形式でアカウントと
 * アクセストークンを注入する。アプリの getAccount() が非 null を返し、
 * acquireTokenSilent がこの実トークンをキャッシュヒットで返すようになる。
 *
 * 形式が msal のバージョンで変わったらこのテストが落ちて気づく (それ自体が検知)。
 */
async function seedMsalCache(page: Page) {
  const uid = "11111111-2222-3333-4444-555555555555"; // 合成 (ログイン画面迂回用)
  const homeAccountId = `${uid}.${TENANT_ID}`;
  const environment = "login.microsoftonline.com";
  const target = `api://${CLIENT_ID}/.default`;

  // 実トークンの exp をそのままキャッシュ有効期限に使う
  const payload = JSON.parse(
    Buffer.from(LIVE_BFF_TOKEN.split(".")[1], "base64url").toString("utf8"),
  ) as { exp: number };

  const accountKey = `${homeAccountId}-${environment}-${TENANT_ID}`;
  const accountEntity = {
    homeAccountId,
    environment,
    realm: TENANT_ID,
    localAccountId: uid,
    username: "goldenpath-probe@e2e.local",
    name: "Golden Path Probe",
    authorityType: "MSSTS",
  };
  const atKey = `${homeAccountId}-${environment}-accesstoken-${CLIENT_ID.toLowerCase()}-${TENANT_ID}-${target.toLowerCase()}`;
  const atEntity = {
    homeAccountId,
    environment,
    credentialType: "AccessToken",
    clientId: CLIENT_ID,
    secret: LIVE_BFF_TOKEN,
    realm: TENANT_ID,
    target,
    cachedAt: String(Math.floor(Date.now() / 1000)),
    expiresOn: String(payload.exp),
    extendedExpiresOn: String(payload.exp),
    tokenType: "Bearer",
  };

  await page.addInitScript(
    ({ accountKey, accountEntity, atKey, atEntity, clientId, homeAccountId, uid, tenantId }) => {
      localStorage.setItem(accountKey, JSON.stringify(accountEntity));
      localStorage.setItem("msal.account.keys", JSON.stringify([accountKey]));
      localStorage.setItem(
        `msal.token.keys.${clientId}`,
        JSON.stringify({ idToken: [], accessToken: [atKey], refreshToken: [] }),
      );
      localStorage.setItem(atKey, JSON.stringify(atEntity));
      localStorage.setItem(
        `msal.${clientId}.active-account-filters`,
        JSON.stringify({ homeAccountId, localAccountId: uid, tenantId }),
      );
    },
    {
      accountKey,
      accountEntity,
      atKey,
      atEntity,
      clientId: CLIENT_ID,
      homeAccountId,
      uid,
      tenantId: TENANT_ID,
    },
  );
}

test("[L4] 相談 → 実AIの返事 → 実VOICEVOXの音声 まで デプロイ済み UI で通る", async ({ page }) => {
  await seedMsalCache(page);

  // 認証済みキャッシュがあるため "/" → home へ (実バンドルの認証ゲートを通過)
  await page.goto("/");
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
