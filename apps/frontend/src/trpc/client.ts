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

export const trpc = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: `${getBaseUrl()}/api/trpc`,
    }),
  ],
});

export type { AppRouter };
