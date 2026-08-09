// schema はドメイン層 (../trpc/domain.ts) が真実。ここは永続化実装のみを持つ
// (repositories → domain の一方向依存。#138)。
import type { HistoryItem } from "../trpc/domain";

export interface HistoryRepository {
  /** createdAt 降順 (新しい順) で返す。 */
  list(): Promise<HistoryItem[]>;
  save(item: HistoryItem): Promise<HistoryItem>;
}

/**
 * ローカル開発とテストの既定 (ADR 0030 D7)。**残す実装であって PoC の残骸ではない。**
 * 本番の永続化は `COSMOS_ENDPOINT` が設定されたときに `CosmosHistoryRepository` が担う
 * (`repositoryFactory.ts`)。この実装はプロセスが落ちれば消える — それでよい。
 */
export class InMemoryHistoryRepository implements HistoryRepository {
  private store: HistoryItem[] = [];

  async list(): Promise<HistoryItem[]> {
    // 挿入順に頼らず createdAt で明示ソートする。外部ストア実装 (Cosmos) は
    // 挿入順を保証しないので、ここで暗黙依存を作ると両実装の並びが静かにズレる (#165)。
    // createdAt は ISO 8601 文字列なので辞書順 = 時系列順。
    return [...this.store].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  async save(item: HistoryItem): Promise<HistoryItem> {
    this.store.push(item);
    return item;
  }
}

export const historyRepository: HistoryRepository = new InMemoryHistoryRepository();
