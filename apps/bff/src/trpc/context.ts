import type { HttpRequest } from "@azure/functions";
import { resolveUserId } from "../auth/clientPrincipal";
import type { HistoryRepository } from "../repositories/historyRepository";
import type { ProblemRepository } from "../repositories/problemRepository";
import { createRepositories } from "../repositories/repositoryFactory";

/**
 * tRPC コンテキスト。
 * 各 repo は test で fresh InMemory に差し替えるため context 経由で渡す。
 * 将来的に認証トークンや相関 ID を追加する際はここに足す。
 *
 * **userId は repo のコンストラクタに束ねてある** (ADR 0030 D5)。ここで解決して
 * `createRepositories` に渡すので、`ProblemRepository` / `HistoryRepository` の
 * シグネチャにはユーザーの概念が現れない — router もテストも userId を知らない。
 */
export type TrpcContext = {
  req: HttpRequest;
  historyRepo: HistoryRepository;
  problemRepo: ProblemRepository;
};

export function createContext(req: HttpRequest): TrpcContext {
  const userId = resolveUserId(req);
  return { req, ...createRepositories(userId) };
}
