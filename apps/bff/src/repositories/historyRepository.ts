// schema はドメイン層 (../trpc/domain.ts) が真実。ここは永続化実装のみを持つ
// (repositories → domain の一方向依存。#138)。
import type { HistoryItem } from "../trpc/domain";

export interface HistoryRepository {
  list(): Promise<HistoryItem[]>;
  save(item: HistoryItem): Promise<HistoryItem>;
}

/**
 * TODO(PoC): 再起動で履歴が消える。本番では Cosmos DB に差し替える。
 */
export class InMemoryHistoryRepository implements HistoryRepository {
  private store: HistoryItem[] = [];

  async list(): Promise<HistoryItem[]> {
    return [...this.store];
  }

  async save(item: HistoryItem): Promise<HistoryItem> {
    this.store.unshift(item);
    return item;
  }
}

export const historyRepository: HistoryRepository = new InMemoryHistoryRepository();
