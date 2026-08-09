/**
 * 履歴リポジトリの Cosmos DB 実装 (ADR 0030)。
 *
 * `CosmosProblemRepository` と同じ約束 (userId はコンストラクタ / パーティションキーは
 * `/userId` / 器は bicep が作る / 単一ユーザー・シングルライター前提) に従う。
 *
 * **並び順は `createdAt` 降順を SQL で明示する。** in-memory 実装は `unshift` の挿入順に
 * 暗黙依存していたが、外部ストアは挿入順を保証しないので、そのまま移すと
 * 「新しい順」が静かに壊れる (Issue #165 の指摘)。in-memory 側も明示ソートに揃えた。
 */
import type { Container } from "@azure/cosmos";
import { HistoryItemSchema, type HistoryItem } from "../trpc/domain";
import type { HistoryRepository } from "./historyRepository";

type HistoryDocument = HistoryItem & { userId: string };

export class CosmosHistoryRepository implements HistoryRepository {
  // パラメータプロパティを使わない理由は cosmosProblemRepository.ts の同じ位置を参照
  // (フロントの erasableSyntaxOnly で TS1294 になる)。
  private readonly container: Container;
  private readonly userId: string;

  constructor(container: Container, userId: string) {
    this.container = container;
    this.userId = userId;
  }

  async list(): Promise<HistoryItem[]> {
    const { resources } = await this.container.items
      .query<unknown>(
        {
          query: "SELECT * FROM c WHERE c.userId = @userId ORDER BY c.createdAt DESC",
          parameters: [{ name: "@userId", value: this.userId }],
        },
        { partitionKey: this.userId },
      )
      .fetchAll();
    // z.object が未知のキー (userId / _rid / _ts) を落とす。
    return resources.map((doc) => HistoryItemSchema.parse(doc));
  }

  async save(item: HistoryItem): Promise<HistoryItem> {
    const doc: HistoryDocument = { ...item, userId: this.userId };
    await this.container.items.upsert(doc);
    return item;
  }
}
