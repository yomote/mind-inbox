/**
 * [L2] Cosmos リポジトリ (Problem) の結合テスト。
 *
 * Cosmos SDK の `Container` は差し替え可能な最小のテストダブルにする。ここで検証するのは
 * **BFF が Cosmos に何を渡し、返ってきたものをどう解釈するか**であって、Cosmos 自体の
 * 挙動ではない (それは実環境の動作検証 — ADR 0018 / docs/runbooks/cosmos-persistence.md)。
 *
 * 無いと何が静かに通るか:
 *   - userId をクエリにもドキュメントにも載せ忘れる → 全ユーザーのデータが混ざる /
 *     パーティションキー無しの書き込みで実行時に落ちる
 *   - ORDER BY を落とす → 一覧の並びが Cosmos の返却順任せになり「新しい順」が壊れる
 *   - Cosmos のシステム属性 (_rid / _ts) や userId を剥がし忘れる → tRPC の output
 *     スキーマ (strict ではない) を素通りしてフロントに謎のキーが漏れる
 */
import type { Container } from "@azure/cosmos";
import { describe, expect, it } from "vitest";
import { CosmosProblemRepository, isNotFound } from "./cosmosProblemRepository";
import type { Mention, Problem } from "../trpc/domain";

// ---- テストダブル ----------------------------------------------------------

type QueryCall = { query: unknown; options: unknown };
type ItemCall = { id: string; partitionKey: string };

function makeFakeContainer(options: { queryResults?: unknown[]; readResource?: unknown } = {}) {
  const calls = {
    queries: [] as QueryCall[],
    upserts: [] as unknown[],
    reads: [] as ItemCall[],
    deletes: [] as ItemCall[],
  };
  let deleteError: unknown = null;

  const container = {
    items: {
      query(query: unknown, opts: unknown) {
        calls.queries.push({ query, options: opts });
        return { fetchAll: async () => ({ resources: options.queryResults ?? [] }) };
      },
      async upsert(doc: unknown) {
        calls.upserts.push(doc);
        return { resource: doc };
      },
    },
    item(id: string, partitionKey: string) {
      return {
        async read() {
          calls.reads.push({ id, partitionKey });
          return { resource: options.readResource };
        },
        async delete() {
          calls.deletes.push({ id, partitionKey });
          if (deleteError) throw deleteError;
          return {};
        },
      };
    },
  };

  return {
    container: container as unknown as Container,
    calls,
    failDeleteWith(err: unknown) {
      deleteError = err;
    },
  };
}

// ---- fixtures --------------------------------------------------------------

function makeMention(overrides: Partial<Mention> = {}): Mention {
  return {
    id: "men-1",
    sessionId: "sess-1",
    dumpId: null,
    createdAt: "2026-01-01T00:00:00.000Z",
    statement: "転職すべきか迷っている",
    excerpt: "最近ずっと転職のことを考えていて",
    affect: { label: "不安", valence: "negative", intensity: 0.6 },
    proposedTheme: "仕事・キャリア",
    proposedTags: ["転職"],
    problemId: "prob-1",
    groupingConfidence: null,
    ...overrides,
  };
}

function makeProblem(overrides: Partial<Problem> = {}): Problem {
  const mentions = overrides.mentions ?? [makeMention()];
  return {
    id: "prob-1",
    title: "転職の迷い",
    summary: "転職すべきか迷っている",
    theme: "仕事・キャリア",
    tags: ["転職"],
    status: "open",
    mentions,
    mentionCount: mentions.length,
    plans: [],
    createdAt: "2026-01-01T00:00:00.000Z",
    lastMentionedAt: "2026-01-01T00:00:00.000Z",
    resolvedAt: null,
    shelvedAt: null,
    ...overrides,
  };
}

/** Cosmos が実際に返してくるシステム属性を模す。 */
function asStoredDocument(doc: object, userId: string): Record<string, unknown> {
  return {
    ...doc,
    userId,
    _rid: "rid-xxxx",
    _self: "dbs/xx/colls/yy/docs/zz",
    _etag: '"0000-0000"',
    _attachments: "attachments/",
    _ts: 1767225600,
  };
}

// ---- CosmosProblemRepository ----------------------------------------------

describe("[L2] CosmosProblemRepository", () => {
  it("list は userId で絞り lastMentionedAt 降順を SQL で明示する", async () => {
    const fake = makeFakeContainer({ queryResults: [] });
    const repo = new CosmosProblemRepository(fake.container, "user-a");

    await repo.list();

    const call = fake.calls.queries[0] as QueryCall;
    const spec = call.query as { query: string; parameters: { name: string; value: string }[] };
    expect(spec.query).toContain("c.userId = @userId");
    expect(spec.query).toContain("ORDER BY c.lastMentionedAt DESC");
    expect(spec.parameters).toContainEqual({ name: "@userId", value: "user-a" });
    // パーティションキーを渡さないとクロスパーティションクエリになり RU が跳ねる
    expect(call.options).toEqual({ partitionKey: "user-a" });
  });

  it("list の theme / status フィルタはパラメータ化して SQL に載せる", async () => {
    const fake = makeFakeContainer({ queryResults: [] });
    const repo = new CosmosProblemRepository(fake.container, "user-a");

    await repo.list({ theme: "お金", status: "open" });

    const spec = (fake.calls.queries[0] as QueryCall).query as {
      query: string;
      parameters: { name: string; value: string }[];
    };
    expect(spec.query).toContain("c.theme = @theme");
    expect(spec.query).toContain("c.status = @status");
    expect(spec.parameters).toContainEqual({ name: "@theme", value: "お金" });
    expect(spec.parameters).toContainEqual({ name: "@status", value: "open" });
  });

  it("list は Cosmos のシステム属性と userId を剥がしてドメイン型で返す", async () => {
    const problem = makeProblem();
    const fake = makeFakeContainer({ queryResults: [asStoredDocument(problem, "user-a")] });
    const repo = new CosmosProblemRepository(fake.container, "user-a");

    const [got] = await repo.list();

    expect(got).toEqual(problem);
    expect(Object.keys(got as object)).not.toContain("userId");
    expect(Object.keys(got as object)).not.toContain("_ts");
  });

  it("get は id + パーティションキーで点引きし、無ければ null", async () => {
    const missing = makeFakeContainer({ readResource: undefined });
    const repo = new CosmosProblemRepository(missing.container, "user-a");

    expect(await repo.get("prob-1")).toBeNull();
    expect(missing.calls.reads[0]).toEqual({ id: "prob-1", partitionKey: "user-a" });

    const problem = makeProblem();
    const found = makeFakeContainer({ readResource: asStoredDocument(problem, "user-a") });
    const repo2 = new CosmosProblemRepository(found.container, "user-a");
    expect(await repo2.get("prob-1")).toEqual(problem);
  });

  it("upsert は userId を載せて保存し、保存した値を返す", async () => {
    const fake = makeFakeContainer();
    const repo = new CosmosProblemRepository(fake.container, "user-a");
    const problem = makeProblem();

    const returned = await repo.upsert(problem);

    expect(fake.calls.upserts[0]).toEqual({ ...problem, userId: "user-a" });
    expect(returned).toEqual(problem);
  });

  it("remove は既に無いドキュメント (404) を成功として扱う", async () => {
    const fake = makeFakeContainer();
    fake.failDeleteWith({ code: 404, message: "Entity with the specified id does not exist" });
    const repo = new CosmosProblemRepository(fake.container, "user-a");

    await expect(repo.remove("prob-1")).resolves.toBeUndefined();
    expect(fake.calls.deletes[0]).toEqual({ id: "prob-1", partitionKey: "user-a" });
  });

  it("remove は 404 以外のエラーを握り潰さない", async () => {
    const fake = makeFakeContainer();
    fake.failDeleteWith({ code: 403, message: "Forbidden" });
    const repo = new CosmosProblemRepository(fake.container, "user-a");

    await expect(repo.remove("prob-1")).rejects.toMatchObject({ code: 403 });
  });

  it("isNotFound は 404 だけを true にする", () => {
    expect(isNotFound({ code: 404 })).toBe(true);
    expect(isNotFound({ code: 403 })).toBe(false);
    expect(isNotFound(new Error("boom"))).toBe(false);
    expect(isNotFound(null)).toBe(false);
  });
});
