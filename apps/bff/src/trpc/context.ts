import type { HttpRequest } from "@azure/functions";

/**
 * tRPC コンテキスト。
 * 現時点では Azure Functions の HttpRequest を保持するだけ。
 * 将来的に認証トークンや相関 ID を追加する際はここに足す。
 */
export type TrpcContext = {
  req: HttpRequest;
};

export function createContext(req: HttpRequest): TrpcContext {
  return { req };
}
