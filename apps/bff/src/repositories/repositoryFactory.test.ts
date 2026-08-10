/**
 * [L1] リポジトリ実装の選択 (ADR 0030 D7)。
 *
 * 無いと何が静かに通るか: COSMOS_ENDPOINT 未設定のローカルで Cosmos 実装が選ばれて
 * 起動しなくなる、あるいは in-memory 実装を毎回 new してしまい、リクエストをまたいだ
 * 状態が消える (ローカルで「保存したのに一覧に出ない」)。
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("./cosmosClient", () => ({ getCosmosContainers: vi.fn() }));

import { getCosmosContainers } from "./cosmosClient";
import { CosmosProblemRepository } from "./cosmosProblemRepository";
import { problemRepository } from "./problemRepository";
import { createRepositories } from "./repositoryFactory";

const getCosmosContainersMock = vi.mocked(getCosmosContainers);

describe("[L1] createRepositories", () => {
  it("Cosmos 未設定なら in-memory の module singleton をそのまま返す", () => {
    getCosmosContainersMock.mockReturnValue(null);

    const repos = createRepositories("local");

    // new せず singleton を返すこと自体が仕様 (リクエストをまたいで状態が残る)
    expect(repos.problemRepo).toBe(problemRepository);
  });

  it("Cosmos 設定済みなら userId を束ねた Cosmos 実装を返す", () => {
    const containers = { problems: {} };
    getCosmosContainersMock.mockReturnValue(containers as never);

    const repos = createRepositories("oid-123");

    expect(repos.problemRepo).toBeInstanceOf(CosmosProblemRepository);
  });
});
