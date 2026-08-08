import type { Page } from "@playwright/test";

/**
 * e2e-live 共通: Entra ログイン往復の route 偽装ヘルパー。
 *
 * consultation-scenario.spec.ts と ux-probe.spec.ts が共有する。
 * 認証: **Entra のプロトコル面 (authorize / token エンドポイント) を route で偽装**し、
 * access_token に実トークンを返す。msal は正規の手順で自分のキャッシュに書き込むため、
 * msal のキャッシュ形式 (v5 でスキーマ刷新 + 暗号化) に依存しない。
 * ※ 初版は localStorage へのキャッシュ直接注入だったが、v5 の暗号化で成立せず廃止。
 * 実ログイン往復そのものは login-canary.spec.ts と人間の初回確認が担当。
 */

export const LIVE_APP_URL = process.env.LIVE_APP_URL ?? "";
export const LIVE_BFF_URL = process.env.LIVE_BFF_URL ?? "";
export const LIVE_BFF_TOKEN = process.env.LIVE_BFF_TOKEN ?? "";
export const CLIENT_ID = process.env.LIVE_ENTRA_CLIENT_ID ?? "";
export const TENANT_ID = process.env.LIVE_ENTRA_TENANT_ID ?? "";

/** LIVE_* env が揃っていない (= 実環境向け実行ではない) 場合 true。spec の skip 判定に使う。 */
export const liveEnvMissing =
  !LIVE_APP_URL || !LIVE_BFF_URL || !LIVE_BFF_TOKEN || !CLIENT_ID || !TENANT_ID;

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
export async function fakeEntraLogin(page: Page) {
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
  // lastNonce はその時点で直前の authorize の値になっている。
  // 注意: route.fulfill は fetch の CORS 検査を免除しない (Playwright の仕様)。
  // 実 Entra 同様に ACAO を返さないと、ブラウザが偽装応答をブロックする
  const corsHeaders = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
  };
  // glob "**/oauth2/v2.0/token" はクエリ付き URL (token?client-request-id=...) に
  // マッチせず、リクエストが実 Entra へ素通りして AADSTS9002313 (invalid_grant) で
  // 400 になる (deploy run #90 の実落ち方)。クエリに依存しない述語でマッチさせる
  await page.route(
    (url) => url.pathname.endsWith("/oauth2/v2.0/token"),
    async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 200, headers: corsHeaders });
        return;
      }
      await route.fulfill({
        headers: corsHeaders,
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
    },
  );
}
