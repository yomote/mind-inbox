/**
 * [L1] in-memory 履歴リポジトリの並び順。
 *
 * 無いと何が静かに通るか: `list()` が挿入順 (`unshift`) に暗黙依存したまま残り、
 * Cosmos 実装 (createdAt 降順の明示ソート) と並びがズレる。両実装が同じ契約を
 * 守っていることを固定する (#165)。
 */
import { describe, expect, it } from "vitest";
import { InMemoryHistoryRepository } from "./historyRepository";
import type { HistoryItem } from "../trpc/domain";

function item(id: string, createdAt: string): HistoryItem {
  return {
    id,
    title: id,
    createdAt,
    result: { summary: "s", emotions: [], priorities: [] },
    plan: { title: "p", steps: [] },
  };
}

describe("[L1] InMemoryHistoryRepository", () => {
  it("list は createdAt 降順で返す (保存順ではなく)", async () => {
    const repo = new InMemoryHistoryRepository();
    await repo.save(item("b", "2026-02-01T00:00:00.000Z"));
    await repo.save(item("c", "2026-03-01T00:00:00.000Z"));
    await repo.save(item("a", "2026-01-01T00:00:00.000Z"));

    expect((await repo.list()).map((i) => i.id)).toEqual(["c", "b", "a"]);
  });

  it("list はコピーを返す (呼び出し側の変更が内部状態を壊さない)", async () => {
    const repo = new InMemoryHistoryRepository();
    await repo.save(item("a", "2026-01-01T00:00:00.000Z"));

    (await repo.list()).pop();

    expect(await repo.list()).toHaveLength(1);
  });
});
