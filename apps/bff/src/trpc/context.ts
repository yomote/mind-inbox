import type { HttpRequest } from "@azure/functions";
import { historyRepository, type HistoryRepository } from "../repositories/historyRepository";

/**
 * tRPC コンテキスト。
 * historyRepo は test で fresh InMemory に差し替えるため context 経由で渡す。
 * 将来的に認証トークンや相関 ID を追加する際はここに足す。
 */
export type TrpcContext = {
  req: HttpRequest;
  historyRepo: HistoryRepository;
};

export function createContext(req: HttpRequest): TrpcContext {
  return { req, historyRepo: historyRepository };
}
