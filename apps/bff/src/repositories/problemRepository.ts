import type { Problem, ProblemStatus, Theme } from "../trpc/domain";

/**
 * Problem リポジトリ（Phase B）。
 *
 * ADR 0007 の集約ルート = Problem。Mention は Problem に内包される（独立集約にしない）ため、
 * Mention 専用リポジトリは設けず Problem 経由でアクセスする。
 *
 * historyRepository と同じ in-memory パターン（interface + InMemory 実装 + module singleton）。
 * TODO(PoC): 本番は Cosmos DB に置き換える。再起動で消える。
 */

export type ProblemFilter = {
  theme?: Theme;
  status?: ProblemStatus;
};

export interface ProblemRepository {
  /** 直近言及順（lastMentionedAt 降順）で返す。theme / status で絞れる。 */
  list(filter?: ProblemFilter): Promise<Problem[]>;
  get(id: string): Promise<Problem | null>;
  /** id 単位の upsert（新規作成 / 既存置換の両方）。 */
  upsert(problem: Problem): Promise<Problem>;
  remove(id: string): Promise<void>;
}

export class InMemoryProblemRepository implements ProblemRepository {
  private store = new Map<string, Problem>();

  async list(filter?: ProblemFilter): Promise<Problem[]> {
    let items = [...this.store.values()];
    if (filter?.theme) items = items.filter((p) => p.theme === filter.theme);
    if (filter?.status) items = items.filter((p) => p.status === filter.status);
    // UI デフォルトの「直近言及順」。lastMentionedAt は ISO 文字列なので辞書順 = 時系列順。
    return items.sort((a, b) => b.lastMentionedAt.localeCompare(a.lastMentionedAt));
  }

  async get(id: string): Promise<Problem | null> {
    return this.store.get(id) ?? null;
  }

  async upsert(problem: Problem): Promise<Problem> {
    this.store.set(problem.id, problem);
    return problem;
  }

  async remove(id: string): Promise<void> {
    this.store.delete(id);
  }
}

export const problemRepository: ProblemRepository = new InMemoryProblemRepository();
