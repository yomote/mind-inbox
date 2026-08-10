import { resolveUserId } from "../auth/clientPrincipal";
import type { ProblemRepository } from "../repositories/problemRepository";
import { createRepositories } from "../repositories/repositoryFactory";

/**
 * tRPC コンテキスト。
 * 各 repo は test で fresh InMemory に差し替えるため context 経由で渡す。
 * 将来的に認証トークンや相関 ID を追加する際はここに足す。
 *
 * req は Web 標準の Request で持つ (Azure Functions の型に依存しない)。
 * BFF の入口は Functions だけではなく、ローカル配信サーバからも同じ router を叩く。
 *
 * **userId は repo のコンストラクタに束ねてある** (ADR 0030 D5)。ここで解決して
 * `createRepositories` に渡すので、`ProblemRepository` の
 * シグネチャにはユーザーの概念が現れない — router もテストも userId を知らない。
 */
export type TrpcContext = {
  req: Request;
  problemRepo: ProblemRepository;
};

export function createContext(req: Request): TrpcContext {
  const userId = resolveUserId(req);
  return { req, ...createRepositories(userId) };
}
