/**
 * リクエストごとにリポジトリ実装を選ぶ (ADR 0030 D7)。
 *
 * `COSMOS_ENDPOINT` が設定されていれば Cosmos 実装、未設定なら **in-memory の
 * module singleton をそのまま返す**。singleton を返すのが重要 — ここで毎回 new すると
 * ローカル開発でリクエストをまたいだ状態が消え、mock 相当の体験が壊れる。
 *
 * Cosmos 実装は逆に「毎回 new」でよい。状態は Cosmos 側にあり、コンストラクタは
 * Container 参照と userId を束ねるだけ (Container / CosmosClient 自体はシングルトン)。
 */
import { getCosmosContainers } from "./cosmosClient";
import { CosmosHistoryRepository } from "./cosmosHistoryRepository";
import { CosmosProblemRepository } from "./cosmosProblemRepository";
import { historyRepository, type HistoryRepository } from "./historyRepository";
import { problemRepository, type ProblemRepository } from "./problemRepository";

export type Repositories = {
  historyRepo: HistoryRepository;
  problemRepo: ProblemRepository;
};

/** この userId 向けのリポジトリ一式を返す。 */
export function createRepositories(userId: string): Repositories {
  const containers = getCosmosContainers();
  if (!containers) {
    return { historyRepo: historyRepository, problemRepo: problemRepository };
  }
  return {
    historyRepo: new CosmosHistoryRepository(containers.history, userId),
    problemRepo: new CosmosProblemRepository(containers.problems, userId),
  };
}
