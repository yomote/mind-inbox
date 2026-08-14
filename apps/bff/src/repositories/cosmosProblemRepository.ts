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
import { hashIdentifier, logErrorEvent, trackDependency } from "../observability/telemetry";
import { summarizeIssues } from "../schemaIssues";
import { ProblemSchema, type Problem } from "../trpc/domain";
import type { ProblemFilter, ProblemRepository } from "./problemRepository";

/** Cosmos に置く形。ドメイン型 + パーティションキー。 */
type ProblemDocument = Problem & { userId: string };

/**
 * Cosmos の応答から `_rid` などのシステム属性を落としてドメイン型に戻す。
 * 契約に反するドキュメントは **例外にせず `null`** を返す (#313 B-3)。
 *
 * 無いと何が起きるか: 1 件でも契約に反する doc が入ると `list()` の parse が投げ、
 * **Problem 一覧が丸ごと 500 になる**。一覧も詳細も開けないので、画面からその doc を
 * 消すこともできない (= 自力で復旧できない / poison document)。壊れた 1 件は
 * 「無かったこと」にして残りを返す方が、可用性としても復旧手段としても上。
 *
 * 書き込み側は `aiAgentClient.extract` が保存前に検証するので、ここは最後の防壁。
 */
function toDomainOrNull(doc: unknown): Problem | null {
  // z.object は既定で未知のキーを落とすので、`userId` / `_rid` / `_ts` はここで消える。
  const parsed = ProblemSchema.safeParse(doc);
  if (parsed.success) return parsed.data;

  // **ドキュメントの中身をログに出さない** — Problem の title / summary / mentions は
  // 相談の本文そのもの。出すのは「どの doc の」「どこが」「どう壊れているか」だけ
  // (id は生成された識別子で本文を含まない)。
  logErrorEvent("cosmos.invalid-document", {
    kind: describeId(doc),
    errorType: "SchemaViolation",
    errorMessage: summarizeIssues(parsed.error),
  });
  return null;
}

/** ログ用。id が文字列でない壊れ方もあるので、その場合は形だけ出す。 */
function describeId(doc: unknown): string {
  const id = (doc as { id?: unknown } | null)?.id;
  return typeof id === "string" ? id : `(${typeof id})`;
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

  /**
   * Cosmos への 1 ホップを開始 / 終了 / 所要 ms / 結果として残す (#307)。
   *
   * **userId は生で出さない** — Cosmos のパーティションキー = 「誰の相談か」そのもの。
   * 相関には要るのでハッシュだけ載せる (`Runbook: bff-telemetry.md`)。
   * ドキュメントの中身 (title / summary / mentions) は 1 文字も出さない。
   */
  private track<T>(
    operation: string,
    run: () => Promise<T>,
    describeResult?: (result: T) => Record<string, string | number | boolean | null | undefined>,
  ): Promise<T> {
    return trackDependency(
      { target: "cosmos", operation, sessionId: undefined },
      run,
      (result) => ({ userHash: hashIdentifier(this.userId), ...describeResult?.(result) }),
    );
  }

  async list(filter?: ProblemFilter): Promise<Problem[]> {
    // 並び順は SQL 側で明示する (挿入順に依存しない)。
    // lastMentionedAt は ISO 8601 文字列なので辞書順 = 時系列順。
    const conditions = ["c.userId = @userId"];
    const parameters: { name: string; value: string }[] = [{ name: "@userId", value: this.userId }];
    if (filter?.theme) {
      conditions.push("c.theme = @theme");
      parameters.push({ name: "@theme", value: filter.theme });
    }
    if (filter?.status) {
      conditions.push("c.status = @status");
      parameters.push({ name: "@status", value: filter.status });
    }

    const resources = await this.track("query problems", async () => {
      const page = await this.container.items
        .query<unknown>(
          {
            query: `SELECT * FROM c WHERE ${conditions.join(" AND ")} ORDER BY c.lastMentionedAt DESC`,
            parameters,
          },
          { partitionKey: this.userId },
        )
        .fetchAll();
      return page.resources;
    });

    // 壊れた doc は落とす (throw しない)。1 件の poison document で一覧全体を失わない。
    return resources.flatMap((doc) => {
      const problem = toDomainOrNull(doc);
      return problem ? [problem] : [];
    });
  }

  async get(id: string): Promise<Problem | null> {
    // item().read() は 404 を投げずに resource undefined を返す。
    const resource = await this.track("read problem", async () => {
      const read = await this.container.item(id, this.userId).read<ProblemDocument>();
      return read.resource;
    });
    // 壊れた doc は「無い」と同じ扱い (NOT_FOUND)。list から消えたものが詳細では 500、
    // という食い違いを作らない。`remove` は parse を通らないので削除経路は残っている。
    return resource ? toDomainOrNull(resource) : null;
  }

  async upsert(problem: Problem): Promise<Problem> {
    const doc: ProblemDocument = { ...problem, userId: this.userId };
    await this.track("upsert problem", () => this.container.items.upsert(doc));
    // 保存した値をそのまま返す (in-memory 実装と同じ契約)。読み直しは RU の無駄。
    return problem;
  }

  async remove(id: string): Promise<void> {
    try {
      await this.track("delete problem", () => this.container.item(id, this.userId).delete());
    } catch (err) {
      // 既に無いなら成功と同じ (in-memory の Map.delete と揃える)。
      // 注意: この 404 は `dependency.end outcome=failure` として**先にログに出ている**。
      // 「消えていた」を記録に残したうえで成功扱いにする、という順序は意図的。
      if (isNotFound(err)) return;
      throw err;
    }
  }
}

/** Cosmos の 404。SDK は `ErrorResponse` に `code` を載せる。 */
export function isNotFound(err: unknown): boolean {
  return typeof err === "object" && err !== null && (err as { code?: unknown }).code === 404;
}
