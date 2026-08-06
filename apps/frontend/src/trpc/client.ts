import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "../../../bff/src/trpc/router";
import { authEnabled, getAccessToken } from "../auth/msal";

/**
 * tRPC クライアント。
 *
 * dev:  VITE_BFF_BASE_URL（デフォルト空 = Vite proxy 経由で /api/trpc）。
 * prod: SWA Free には linked backend が無いので同一オリジンには BFF が居ない。
 *       ビルド時に注入された VITE_BFF_BASE_URL（Functions のホスト）を直叩きする (#69)。
 *
 * 認証が有効な環境では Entra のアクセストークンを Authorization に載せる。
 * これが無いと Functions の EasyAuth が 401 を返す（＝守りは CORS ではなくここ）。
 */
function getBaseUrl(): string {
  return import.meta.env.VITE_BFF_BASE_URL ?? "";
}

export const trpc = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: `${getBaseUrl()}/api/trpc`,
      async headers() {
        if (!authEnabled) return {};
        const token = await getAccessToken();
        return token ? { Authorization: `Bearer ${token}` } : {};
      },
    }),
  ],
});

export type { AppRouter };
