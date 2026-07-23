import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "../../../bff/src/trpc/router";

/**
 * tRPC クライアント。
 *
 * dev: VITE_BFF_BASE_URL（デフォルト http://localhost:7071）に対して
 *      Vite proxy 経由で /api/trpc を叩く。
 * prod: 同一オリジン（SWA + linked Functions）で /api/trpc。
 */
function getBaseUrl(): string {
  if (import.meta.env.DEV) {
    return import.meta.env.VITE_BFF_BASE_URL ?? "";
  }
  return "";
}

const LOGIN_URL = "/.auth/login/aad";
// 認証切れ→login への自動リダイレクトが無限ループしないための番兵。
// 一度 login へ飛ばしたら sessionStorage に印を付け、戻ってきても再び 401 なら
// リダイレクトせず明示エラーを投げる（func 側の恒常的な 401 を検知するため）。
const REAUTH_FLAG = "mindbox.reauthRedirected";

/**
 * SWA の認証ゲート越しに /api/trpc を叩くための fetch ラッパ。
 *
 * 背景: SWA/Functions は未認証（またはセッション切れ）の API 呼び出しに対し
 *   401、あるいは login ページ(HTML)への 302 を返すことがある。素の fetch は
 *   302 を自動追従して HTML を掴み、tRPC が「Unexpected token '<'」で死ぬ →
 *   画面遷移もエラー表示もない“無反応”になる（過去の不具合）。
 * 対策: redirect:"manual" で 302 を追従させず、401/opaqueredirect を認証切れと
 *   みなして login へ一度だけ誘導する。再訪しても 401 なら明示エラーを投げて
 *   ループを断つ。
 */
async function authAwareFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const res = await fetch(input, { ...init, redirect: "manual" });

  const isAuthChallenge =
    res.status === 401 || res.status === 403 || res.type === "opaqueredirect";

  if (isAuthChallenge) {
    const alreadyTried =
      typeof sessionStorage !== "undefined" &&
      sessionStorage.getItem(REAUTH_FLAG) === "1";
    if (!alreadyTried && typeof window !== "undefined") {
      try {
        sessionStorage.setItem(REAUTH_FLAG, "1");
      } catch {
        // sessionStorage 不可でも致命ではない
      }
      const back = encodeURIComponent(
        window.location.pathname + window.location.search,
      );
      window.location.assign(`${LOGIN_URL}?post_login_redirect_uri=${back}`);
      // ページ遷移が始まるので、ここで解決しない Promise を返して
      // 呼び出し側が HTML を JSON パースしようとするのを防ぐ。
      return await new Promise<Response>(() => {});
    }
    throw new Error(
      "認証が必要です。ログインし直してください（セッションが切れているか、アクセス権がありません）。",
    );
  }

  // 認証済みで正常応答が返ったらフラグをクリア。
  if (typeof sessionStorage !== "undefined") {
    try {
      sessionStorage.removeItem(REAUTH_FLAG);
    } catch {
      // no-op
    }
  }
  return res;
}

export const trpc = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: `${getBaseUrl()}/api/trpc`,
      fetch: authAwareFetch,
    }),
  ],
});

export type { AppRouter };
