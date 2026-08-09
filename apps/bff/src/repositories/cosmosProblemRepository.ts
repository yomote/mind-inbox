/**
 * Problem リポジトリの Cosmos DB 実装 (ADR 0030)。
 *
 * ## 設計上の約束
 *
 * - **`userId` はコンストラクタで束ねる**。`ProblemRepository` の interface は
 *   in-memory 実装と 1 文字も変わらない — router もテストも userId を知らなくてよい。
 *   リクエストごとに `createContext` が userId を解決して new する (`trpc/context.ts`)。
 * - **パーティションキーは `/userId`**。クエリは「自分の全件」と「id 点引き」の 2 種類だけで、
 *   クロスパーティションクエリは 1 つも出さない (ADR 0030 の Option A の根拠)。
 * - **Mention は Problem に内包される** (ADR 0007) ので 1 ドキュメントで完結する。
 *   mention 50 件でも約 24 KB で、Cosmos の 1 ドキュメント 2 MB 上限に対して十分小さい。
 * - **単一ユーザー・シングルライター前提。etag による楽観ロックは持たない。**
 *   `upsert` は全ドキュメント置換 (read-modify-write) で、`relink` / `merge` は
 *   2 ドキュメントをまたぐがトランザクション境界が無い。同一ユーザーの書き込みが
 *   同時に走る前提になったら (複数タブ / 複数デバイスの同時操作)、ここは
 *   **etag 条件付き置換 + 競合時リトライに作り替える必要がある** (Issue #165 で合意した前提)。
 * - **DB / コンテナは作らない**。器の宣言は bicep (`cicd/modules/bootstrap-core.bicep`)。
 */
import type { Container } from "@azure/cosmos";
import { ProblemSchema, type Problem } from "../trpc/domain";
import type { ProblemFilter, ProblemRepository } from "./problemRepository";

/** Cosmos に置く形。ドメイン型 + パーティションキー。 */
type ProblemDocument = Problem & { userId: string };

/** Cosmos の応答から `_rid` などのシステム属性を落としてドメイン型に戻す。 */
function toDomain(doc: unknown): Problem {
  // z.object は既定で未知のキーを落とすので、`userId` / `_rid` / `_ts` はここで消える。
  return ProblemSchema.parse(doc);
}

export class CosmosProblemRepository implements ProblemRepository {
  // NOTE: パラメータプロパティ (`constructor(private readonly x: T)`) は使わない。
  // フロントの `trpc/client.ts` が BFF のソースを type-only import しているため、
  // このファイルは `erasableSyntaxOnly: true` のフロント側 `tsc -b` でも型検査される。
  // パラメータプロパティは型を消すだけでは JS にならないので TS1294 で落ちる
  // (BFF 単体の tsc は通るのにフロントのビルドだけ赤くなる、という見つけにくい形)。
  private readonly container: Container;
  private readonly userId: string;

  constructor(container: Container, userId: string) {
    this.container = container;
    this.userId = userId;
  }

  async list(filter?: ProblemFilter): Promise<Problem[]> {
    // 並び順は SQL 側で明示する (挿入順に依存しない)。
    // lastMentionedAt は ISO 8601 文字列なので辞書順 = 時系列順。
    const conditions = ["c.userId = @userId"];
    const parameters: { name: string; value: string }[] = [
      { name: "@userId", value: this.userId },
    ];
    if (filter?.theme) {
      conditions.push("c.theme = @theme");
      parameters.push({ name: "@theme", value: filter.theme });
    }
    if (filter?.status) {
      conditions.push("c.status = @status");
      parameters.push({ name: "@status", value: filter.status });
    }

    const { resources } = await this.container.items
      .query<unknown>(
        {
          query: `SELECT * FROM c WHERE ${conditions.join(" AND ")} ORDER BY c.lastMentionedAt DESC`,
          parameters,
        },
        { partitionKey: this.userId },
      )
      .fetchAll();

    return resources.map(toDomain);
  }

  async get(id: string): Promise<Problem | null> {
    // item().read() は 404 を投げずに resource undefined を返す。
    const { resource } = await this.container.item(id, this.userId).read<ProblemDocument>();
    return resource ? toDomain(resource) : null;
  }

  async upsert(problem: Problem): Promise<Problem> {
    const doc: ProblemDocument = { ...problem, userId: this.userId };
    await this.container.items.upsert(doc);
    // 保存した値をそのまま返す (in-memory 実装と同じ契約)。読み直しは RU の無駄。
    return problem;
  }

  async remove(id: string): Promise<void> {
    try {
      await this.container.item(id, this.userId).delete();
    } catch (err) {
      // 既に無いなら成功と同じ (in-memory の Map.delete と揃える)。
      if (isNotFound(err)) return;
      throw err;
    }
  }
}

/** Cosmos の 404。SDK は `ErrorResponse` に `code` を載せる。 */
export function isNotFound(err: unknown): boolean {
  return typeof err === "object" && err !== null && (err as { code?: unknown }).code === 404;
}
