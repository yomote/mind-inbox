import { expect, test, type Page } from "@playwright/test";

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
 * 認証: **Entra のプロトコル面 (authorize / token エンドポイント) を route で偽装**し、
 * access_token に実トークンを返す。msal は正規の手順で自分のキャッシュに書き込むため、
 * msal のキャッシュ形式 (v5 でスキーマ刷新 + 暗号化) に依存しない。
 * ※ 初版は localStorage へのキャッシュ直接注入だったが、v5 の暗号化で成立せず廃止。
 * 実ログイン往復そのものは login-canary.spec.ts と人間の初回確認が担当。
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

const b64url = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");

// 合成ユーザーの oid/sub (id_token) と client_info.uid は一致している必要がある
// (食い違うと msal がアカウントを組み立てられず、原因の分かりにくい落ち方をする)
const PROBE_UID = "11111111-2222-3333-4444-555555555555";

/** 署名なし JWT (msal-browser は id_token の署名をクライアントでは検証しない)。 */
function makeIdToken(nonce: string): string {
  const now = Math.floor(Date.now() / 1000);
  const uid = PROBE_UID;
  const header = { alg: "RS256", typ: "JWT" };
  const payload = {
    aud: CLIENT_ID,
    iss: `https://login.microsoftonline.com/${TENANT_ID}/v2.0`,
    iat: now,
    nbf: now,
    exp: now + 3600,
    nonce,
    oid: uid,
    sub: uid,
    tid: TENANT_ID,
    preferred_username: "goldenpath-probe@e2e.local",
    name: "Golden Path Probe",
    ver: "2.0",
  };
  return `${b64url(header)}.${b64url(payload)}.e2e-fake-signature`;
}

/**
 * Entra のログイン往復を route で偽装する。
 * - authorize: nonce/state を拾って即 redirect_uri へ #code=...&state=... で戻す
 * - token: access_token に **実トークン** を返す (BFF の EasyAuth はフルに検証される)
 */
async function fakeEntraLogin(page: Page) {
  let lastNonce = "";

  await page.route("**/oauth2/v2.0/authorize*", async (route) => {
    const url = new URL(route.request().url());
    lastNonce = url.searchParams.get("nonce") ?? "";
    const state = url.searchParams.get("state") ?? "";
    const redirectUri = url.searchParams.get("redirect_uri") ?? LIVE_APP_URL;
    // msal-browser は response_mode=fragment。code はダミー (token 側も偽装するため)
    const location = `${redirectUri}#code=e2e-fake-code&state=${encodeURIComponent(state)}&session_state=e2e`;
    await route.fulfill({ status: 302, headers: { Location: location } });
  });

  // token は必ず authorize の後に呼ばれる (OAuth code flow の順序) ため、
  // lastNonce はその時点で直前の authorize の値になっている
  await page.route("**/oauth2/v2.0/token", async (route) => {
    await route.fulfill({
      json: {
        token_type: "Bearer",
        scope: `api://${CLIENT_ID}/.default openid profile`,
        expires_in: 3600,
        ext_expires_in: 3600,
        access_token: LIVE_BFF_TOKEN,
        id_token: makeIdToken(lastNonce),
        client_info: b64url({ uid: PROBE_UID, utid: TENANT_ID }),
      },
    });
  });
}

test("[L4] 相談 → 実AIの返事 → 実VOICEVOXの音声 まで デプロイ済み UI で通る", async ({ page }) => {
  await fakeEntraLogin(page);

  // 実バンドルは未認証だと読み込み時にログインへリダイレクトされる
  // (偽装 Entra が即座に認証済みで返すため、そのままホームに到達する)
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

  // stub 応答 ("[stub] received: ...") も「ガイドの返事」として描画されるため、
  // 上の可視性チェックだけでは BASE_URL 結線切れが緑ですり抜ける (2026-08-08 実障害)
  await expect(page.getByText(/\[stub\]/)).toHaveCount(0);

  // 実 VOICEVOX: /api/tts が **実 BFF ホスト宛に** 呼ばれ、audio (WAV) が返ること。
  // 相対 /api/tts に退行すると waitForResponse がタイムアウトして落ちる
  const tts = await ttsResponse;
  expect(tts.status(), "tts status").toBe(200);
  const ttsBody = await tts.body();
  expect(ttsBody.length, "tts body size").toBeGreaterThan(1000);
  expect(ttsBody.subarray(0, 4).toString("ascii"), "WAV magic").toBe("RIFF");
});
