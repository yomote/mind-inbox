import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * **ホスト側の自動収集** (`apps/bff/host.json`) を守るテスト。
 *
 * `telemetry.ts` の許可リストは**アプリが自分で書いた行**にしか効かない。
 * `APPLICATIONINSIGHTS_CONNECTION_STRING` が配られると、Functions ホストは
 * アプリのコードを一切通さずに `AppRequests` を作り、そこへ**受信 URL をそのまま**入れる。
 * tRPC の `httpBatchLink` は query の入力を `?input={"id":"…"}` として URL に載せるので、
 * この設定が戻ると、出口で塞いだはずの相談本文が別ルートで 30 日残る (#413 の Codex 指摘)。
 *
 * ## このテストが確かめていないこと
 *
 * 確かめているのは**宣言の側だけ**。「実際にホストが URL を記録していない」ことは
 * Azure 上でしか観測できないので、ここでは検証していない (= 緑でも実環境の証明にはならない)。
 * 実測は `docs/runbooks/bff-telemetry.md` の「漏洩点検」の KQL で行う。
 */
const RAW_HOST_JSON = readFileSync(new URL("../../host.json", import.meta.url), "utf8");
const hostJson = JSON.parse(RAW_HOST_JSON) as {
  logging?: { applicationInsights?: { httpAutoCollectionOptions?: Record<string, unknown> } };
};

const EXTENDED_INFO_KEY = "enableHttpTriggerExtendedInfoCollection";

describe("[単体] host.json — ホストの自動収集", () => {
  it("HTTP トリガの拡張情報を収集させない (受信 URL を記録させない)", () => {
    // 無いと何が静かに通るか: ホストが `AppRequests.Url` に受信 URL をそのまま焼き、
    // `problem.get?input={"id":"<相談本文>"}` が 30 日残る。アプリ側のログはきれいなままなので、
    // `telemetry.ts` のテストが全部緑でも誰も気づけない。
    // 既定は true (Azure/azure-webjobs-sdk の HttpAutoCollectionOptions.cs)。
    const options = hostJson.logging?.applicationInsights?.httpAutoCollectionOptions;

    expect(options?.[EXTENDED_INFO_KEY]).toBe(false);
  });

  it("設定が正しい階層に 1 つだけ置かれている", () => {
    // 無いと何が静かに通るか: ホストは知らないキーを**黙って無視する**ので、
    // 階層を 1 つ間違えた / 綴りを変えた設定は「書いてあるのに効いていない」になる。
    // 上のテストは書いた場所を読むだけなので、位置ズレを単独では検出できない。
    const occurrences = RAW_HOST_JSON.split(EXTENDED_INFO_KEY).length - 1;

    expect(occurrences).toBe(1);
    expect(
      Object.keys(hostJson.logging?.applicationInsights?.httpAutoCollectionOptions ?? {}),
    ).toContain(EXTENDED_INFO_KEY);
  });
});
