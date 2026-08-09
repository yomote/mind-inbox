import { historyRepository, type HistoryRepository } from "../repositories/historyRepository";
import { problemRepository, type ProblemRepository } from "../repositories/problemRepository";

/**
 * tRPC コンテキスト。
 * 各 repo は test で fresh InMemory に差し替えるため context 経由で渡す。
 * 将来的に認証トークンや相関 ID を追加する際はここに足す。
 *
 * req は Web 標準の Request で持つ (Azure Functions の型に依存しない)。
 * BFF の入口は Functions だけではなく、ローカル配信サーバからも同じ router を叩く。
 */
export type TrpcContext = {
  req: Request;
  historyRepo: HistoryRepository;
  problemRepo: ProblemRepository;
};

export function createContext(req: Request): TrpcContext {
  return { req, historyRepo: historyRepository, problemRepo: problemRepository };
}
