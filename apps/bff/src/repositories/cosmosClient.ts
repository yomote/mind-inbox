/**
 * Cosmos DB クライアントの遅延シングルトン (ADR 0030)。
 *
 * 方針:
 * - **COSMOS_ENDPOINT が未設定なら null を返す**。呼び出し側 (repositoryFactory) が
 *   in-memory 実装にフォールバックする。ローカル・テストは外部依存ゼロのまま (D7)。
 * - **キー / 接続文字列は一切持たない** (D3)。`disableLocalAuth: true` の口を
 *   Managed Identity + data plane RBAC だけで通す。
 * - **DB / コンテナは作らない**。宣言は bicep が持つ (`cicd/modules/bootstrap-core.bicep`)。
 *   data plane ロールには作成権限が無いので `createIfNotExists` は 403 になる。
 *   IaC の外で作った器は再デプロイで整合が壊れる (debrief #2 の教訓)。
 * - CosmosClient は接続とトークンをキャッシュするので **プロセスで 1 個**にする。
 *   Functions のワーカーが生きている間は使い回され、コールドスタート以外では再作成しない。
 */
import { CosmosClient, type Container } from "@azure/cosmos";
import { config } from "../config";
import { ManagedIdentityCosmosCredential } from "./cosmosCredential";

type Containers = {
  problems: Container;
  history: Container;
};

let cached: Containers | null | undefined;

/** テスト用: 次回アクセスでクライアントを作り直させる。 */
export function resetCosmosContainers(): void {
  cached = undefined;
}

/**
 * problems / history コンテナを返す。Cosmos 未設定なら null。
 *
 * @returns Cosmos が構成されていれば Container 一式、未設定なら null。
 */
export function getCosmosContainers(): Containers | null {
  if (cached !== undefined) return cached;

  const endpoint = config.cosmosEndpoint;
  if (!endpoint) {
    cached = null;
    return cached;
  }

  const client = new CosmosClient({
    endpoint,
    aadCredentials: new ManagedIdentityCosmosCredential(),
  });
  const database = client.database(config.cosmosDatabase);
  cached = {
    problems: database.container(config.cosmosProblemsContainer),
    history: database.container(config.cosmosHistoryContainer),
  };
  return cached;
}
