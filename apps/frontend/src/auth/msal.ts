/**
 * Entra ID (Azure AD) 認証 — SPA 側 (#69 / ADR 0013)。
 *
 * SWA Free には linked backend が無く、フロントは BFF(Functions) を **別オリジンから直叩き** する。
 * SWA の認証は SWA が配る静的ファイルにしか効かないので、認可の門は Functions の EasyAuth 側にある。
 * ここでの役割は「その門を通れるアクセストークンを取ってくる」こと。
 *
 * 認証の有効/無効は env で決まる:
 *   - VITE_ENTRA_CLIENT_ID と VITE_ENTRA_TENANT_ID が両方あれば有効 (= デプロイ環境)
 *   - 片方でも無ければ無効 (= ローカル開発。Functions Core Tools に EasyAuth は無く、
 *     ローカル BFF は自分の PC 内にしか居ないため認証を挟まない)
 */
import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  type AccountInfo,
  type Configuration,
} from "@azure/msal-browser";

const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";
const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID ?? "";

/** 認証を実際に使うか。ローカル開発では false になり、トークン取得を一切しない。 */
export const authEnabled = Boolean(clientId && tenantId);

/**
 * BFF を呼ぶためのスコープ。EasyAuth の allowedAudiences (clientId / api://clientId) に
 * 合わせて、既定は自分自身のアプリ登録に対する `.default`。
 */
const apiScope = import.meta.env.VITE_ENTRA_API_SCOPE ?? `api://${clientId}/.default`;

function buildConfig(): Configuration {
  return {
    auth: {
      clientId,
      // 単一テナント: 他テナントのアカウントはここで弾かれる (IaC 側の issuer 固定と対)。
      authority: `https://login.microsoftonline.com/${tenantId}`,
      redirectUri: window.location.origin,
    },
    cache: {
      // リロードでログインが飛ばないようにする。個人利用の dev 環境なので localStorage で可。
      cacheLocation: "localStorage",
    },
  };
}

/**
 * MSAL インスタンスは **遅延生成** する。import しただけで window を触ると、
 * ブラウザ以外の文脈 (テスト・ツール実行) でモジュールの読み込み自体が落ちるため。
 */
let msalInstance: PublicClientApplication | null = null;

function getMsal(): PublicClientApplication | null {
  if (!authEnabled || typeof window === "undefined") return null;
  if (!msalInstance) msalInstance = new PublicClientApplication(buildConfig());
  return msalInstance;
}

let initialized = false;

/**
 * アプリ起動時に一度だけ呼ぶ。リダイレクト戻りの処理を先に済ませてから描画するため、
 * main.tsx で await する。
 */
export async function initAuth(): Promise<void> {
  const msal = getMsal();
  if (!msal || initialized) return;
  await msal.initialize();
  // ログインリダイレクトからの戻りをここで回収する (戻りでなければ null)。
  const result = await msal.handleRedirectPromise();
  if (result?.account) {
    msal.setActiveAccount(result.account);
  } else if (!msal.getActiveAccount()) {
    const [first] = msal.getAllAccounts();
    if (first) msal.setActiveAccount(first);
  }
  initialized = true;
}

export function getAccount(): AccountInfo | null {
  return getMsal()?.getActiveAccount() ?? null;
}

export async function login(): Promise<void> {
  const msal = getMsal();
  if (!msal) return;
  await msal.loginRedirect({ scopes: [apiScope] });
}

export async function logout(): Promise<void> {
  const msal = getMsal();
  if (!msal) return;
  await msal.logoutRedirect();
}

/**
 * BFF 呼び出し用のアクセストークンを返す。認証無効なら null (= Authorization を付けない)。
 *
 * silent で取れなければログインリダイレクトに倒す。リダイレクトするとこの Promise は
 * 解決しないまま画面が遷移するため、呼び出し側は「返ってこないことがある」前提で使う。
 */
export async function getAccessToken(): Promise<string | null> {
  const msal = getMsal();
  if (!msal) return null;
  if (!initialized) await initAuth();

  const account = msal.getActiveAccount();
  if (!account) {
    await login();
    return null;
  }

  try {
    const result = await msal.acquireTokenSilent({ scopes: [apiScope], account });
    return result.accessToken;
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      await msal.acquireTokenRedirect({ scopes: [apiScope], account });
      return null;
    }
    throw err;
  }
}
