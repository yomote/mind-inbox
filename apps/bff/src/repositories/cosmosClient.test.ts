/**
 * [L1] Cosmos クライアントの遅延シングルトンと「Cosmos を使うか否か」の分岐。
 *
 * 無いと何が静かに通るか: **`COSMOS_ENDPOINT` が未設定なのに Cosmos を掴みにいく**
 * (ローカルが外部依存で起動しなくなる) か、**設定済みなのに in-memory のまま動く**
 * (本番でデータが翌日消えるのに、テストもログも緑のまま)。ADR 0030 が「真偽の分岐点」と
 * 呼んでいる箇所で、`repositoryFactory.test.ts` はここを丸ごと `vi.mock` している。
 *
 * 併せて module state (`cached`) の 2 点を固定する:
 *   - キャッシュが効くこと — リクエストごとに CosmosClient を作ると接続もトークンも
 *     取り直しになり、コールドスタートが重くなる
 *   - テスト間で漏れないこと — `config` は import 時に `process.env` を読む module
 *     singleton なので、`vi.resetModules()` を挟まないと前のテストの値を引きずり、
 *     実行順で結果が変わる (#145 で踏んだ順序依存 flaky と同型)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock は巻き上げられるので、参照する変数は `mock` 接頭辞で宣言する。
const mockCalls = {
  clients: [] as { endpoint: string; hasAadCredentials: boolean; hasKey: boolean }[],
  databases: [] as string[],
  containers: [] as string[],
};

vi.mock("@azure/cosmos", () => {
  class FakeCosmosClient {
    constructor(options: { endpoint: string; aadCredentials?: unknown; key?: unknown }) {
      mockCalls.clients.push({
        endpoint: options.endpoint,
        hasAadCredentials: options.aadCredentials !== undefined,
        hasKey: options.key !== undefined,
      });
    }

    database(id: string) {
      mockCalls.databases.push(id);
      return {
        container: (name: string) => {
          mockCalls.containers.push(name);
          return { id: name };
        },
      };
    }
  }
  return { CosmosClient: FakeCosmosClient };
});

const COSMOS_ENV_KEYS = [
  "COSMOS_ENDPOINT",
  "COSMOS_DATABASE",
  "COSMOS_PROBLEMS_CONTAINER",
  "COSMOS_HISTORY_CONTAINER",
] as const;

const ENDPOINT = "https://cosmos-dev-mindbox.documents.azure.com:443/";

let savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  savedEnv = {};
  for (const key of COSMOS_ENV_KEYS) {
    savedEnv[key] = process.env[key];
    delete process.env[key];
  }
  mockCalls.clients.length = 0;
  mockCalls.databases.length = 0;
  mockCalls.containers.length = 0;
});

afterEach(() => {
  for (const key of COSMOS_ENV_KEYS) {
    const original = savedEnv[key];
    if (original === undefined) delete process.env[key];
    else process.env[key] = original;
  }
  // 次のテストが自分の env で config を読み直せるようにする。
  vi.resetModules();
});

/**
 * `process.env` を反映した状態で cosmosClient を読み込む。
 *
 * `config.ts` は import 時に `process.env` を読むので、env を変えたあとは
 * module registry を捨てないと古い値のままになる。
 */
async function loadCosmosClient() {
  vi.resetModules();
  return await import("./cosmosClient");
}

describe("[L1] getCosmosContainers", () => {
  it("COSMOS_ENDPOINT 未設定なら null を返し、CosmosClient を作らない", async () => {
    const { getCosmosContainers } = await loadCosmosClient();

    expect(getCosmosContainers()).toBeNull();
    // 「作らない」まで見る — new するだけでも外部依存を掴んだことになる
    expect(mockCalls.clients).toHaveLength(0);
  });

  it("COSMOS_ENDPOINT 設定済みなら endpoint / database / container 名を配線する", async () => {
    process.env["COSMOS_ENDPOINT"] = ENDPOINT;
    process.env["COSMOS_DATABASE"] = "mydb";
    process.env["COSMOS_PROBLEMS_CONTAINER"] = "my-problems";
    process.env["COSMOS_HISTORY_CONTAINER"] = "my-history";

    const { getCosmosContainers } = await loadCosmosClient();
    const containers = getCosmosContainers();

    expect(containers).not.toBeNull();
    expect(mockCalls.clients[0]?.endpoint).toBe(ENDPOINT);
    expect(mockCalls.databases).toEqual(["mydb"]);
    // 順序込みで見る — problems と history が入れ替わると全データが逆のコンテナに入る
    expect(mockCalls.containers).toEqual(["my-problems", "my-history"]);
    expect(containers?.problems).toMatchObject({ id: "my-problems" });
    expect(containers?.history).toMatchObject({ id: "my-history" });
  });

  it("DB / コンテナ名が未設定なら bicep と同じ既定名にフォールバックする", async () => {
    process.env["COSMOS_ENDPOINT"] = ENDPOINT;

    const { getCosmosContainers } = await loadCosmosClient();
    getCosmosContainers();

    // ここが bicep (cicd/modules/bootstrap-core.bicep) の器名とズレると実行時 404 になる
    expect(mockCalls.databases).toEqual(["mindinbox"]);
    expect(mockCalls.containers).toEqual(["problems", "history"]);
  });

  it("キーではなく Managed ID (aadCredentials) でクライアントを作る", async () => {
    process.env["COSMOS_ENDPOINT"] = ENDPOINT;

    const { getCosmosContainers } = await loadCosmosClient();
    getCosmosContainers();

    // Cosmos 側は disableLocalAuth: true (ADR 0030 D3) なので、キーを渡す実装に
    // 戻ると本番だけ 401 になる
    expect(mockCalls.clients[0]?.hasAadCredentials).toBe(true);
    expect(mockCalls.clients[0]?.hasKey).toBe(false);
  });

  it("2 回目以降はキャッシュを返し、resetCosmosContainers() で作り直す", async () => {
    process.env["COSMOS_ENDPOINT"] = ENDPOINT;

    const { getCosmosContainers, resetCosmosContainers } = await loadCosmosClient();

    const first = getCosmosContainers();
    const second = getCosmosContainers();

    expect(second).toBe(first);
    expect(mockCalls.clients).toHaveLength(1);

    resetCosmosContainers();
    const third = getCosmosContainers();

    expect(third).not.toBe(first);
    expect(mockCalls.clients).toHaveLength(2);
  });

  it("「Cosmos 未設定」という判定もキャッシュする (毎回 config を読み直さない)", async () => {
    const { getCosmosContainers, resetCosmosContainers } = await loadCosmosClient();
    const { config } = await import("../config");

    expect(getCosmosContainers()).toBeNull();

    // config は import 時に env を読む const なので、実行時に書き換えて
    // 「読み直していたら結果が変わる」状況を作る。
    (config as { cosmosEndpoint?: string }).cosmosEndpoint = ENDPOINT;

    expect(getCosmosContainers()).toBeNull();
    expect(mockCalls.clients).toHaveLength(0);

    // キャッシュを捨てれば新しい値を拾う (= 恒久的に null と決めつけてはいない)
    resetCosmosContainers();
    expect(getCosmosContainers()).not.toBeNull();
  });
});
