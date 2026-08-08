import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "../../../bff/src/trpc/router";
import { bffAuthHeaders, bffBaseUrl } from "../api/http";

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
export const trpc = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: `${bffBaseUrl()}/api/trpc`,
      async headers() {
        return bffAuthHeaders();
      },
    }),
  ],
});

export type { AppRouter };
