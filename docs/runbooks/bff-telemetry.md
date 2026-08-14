# BFF のテレメトリ (Application Insights / Log Analytics)

## Trigger

- 実環境で何かがおかしいのに**ブラウザ側の証拠しか無い**とき (#293 の状況)。「/api/chat/stream は本当に呼ばれたのか」「BFF → ai-agent は何 ms で何を返したのか」に答えたいとき
- テレメトリの配線を変えた / デプロイした直後に、**ログが実際に流れているか**を確かめるとき
- BFF に新しいログを足すときに、**何を記録してよくて何を落とすか**を確認するとき (§ 何を記録し、何を落とすか)

## Prerequisites

- Azure CLI (`az login` 済み) と対象サブスクリプションへの Reader 相当 + `Log Analytics Reader`
- リソースグループ (既定: `rg-dev-mind-inbox`) と bootstrap デプロイ名を知っていること
- サンドボックス内のエージェントは直接 `az` を叩けない → [`ops-inspect.md`](ops-inspect.md) 経由で取る

```bash
RG=rg-dev-mind-inbox
DEPLOYMENT=main-bootstrap   # 実際の deployment 名は `az deployment group list -g "$RG" -o table` で確認
```

## Steps

### 1. どこに出ているかを確認する

テレメトリの経路は 2 本ある。**どちらも宣言 (bicep) 側で配られる** — `az` で手設定しない (appSettings は bicep で全置換されるため、手設定は次のデプロイで静かに消える)。

| 経路                                   | 宣言                                                                                           | 何が入るか                                    | テーブル                                                          |
| -------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------- |
| Application Insights (workspace-based) | `cicd/modules/bootstrap-core.bicep` の `appInsights` + `APPLICATIONINSIGHTS_CONNECTION_STRING` | requests / dependencies / exceptions / traces | `AppRequests` / `AppDependencies` / `AppExceptions` / `AppTraces` |
| 診断設定 (`diag-audit-func-…`)         | 同ファイルの `diagFunctionApp`                                                                 | Functions ホストの実行ログ                    | `FunctionAppLogs`                                                 |

```bash
# ワークスペースの GUID (KQL を打つのに要る)。#307 で main-bootstrap.bicep から re-export した
LAW_CUSTOMER_ID=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query "properties.outputs.logAnalyticsCustomerId.value" -o tsv)

# App Insights が在るか (宣言どおりに作られたか)
az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query "{enabled:properties.outputs.appInsightsEnabled.value, name:properties.outputs.appInsightsName.value}" -o json

# 接続文字列が Function App に配られているか (**値は表示しない** — 取り込みキーを含む)
az functionapp config appsettings list -g "$RG" -n func-dev-mindbox \
  --query "[?name=='APPLICATIONINSIGHTS_CONNECTION_STRING'].name" -o tsv
```

### 2. ゴールデンパスを 1 往復叩く

ログは「流れているか」を実際のリクエストでしか確かめられない。時刻を控えてから叩く。

```bash
T=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -sS -o /dev/null -w '%{http_code}\n' "https://func-dev-mindbox.azurewebsites.net/api/warmup"
echo "$T"
```

> `/api/chat/stream` と `/api/trpc/*` は Functions の EasyAuth の内側にあるので、認証なしの curl は 401 になる (それも `AppRequests` に残る)。UI から 1 往復する方が確実。

### 3. KQL で引く

```bash
q() { az monitor log-analytics query -w "$LAW_CUSTOMER_ID" --analytics-query "$1" -o table; }
```

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] **リクエストが記録されている** — 「そもそも呼ばれたのか」に答える最小の 1 本。#293 のときはこれが無かった

  ```kusto
  AppRequests
  | where TimeGenerated > ago(1h)
  | project TimeGenerated, Name, ResultCode, DurationMs, Success, OperationId
  | order by TimeGenerated desc
  ```

- [ ] **下流ホップが記録されている** — BFF → ai-agent / VOICEVOX / Cosmos が「呼んだ / 返った / 何 ms / ステータス」として残る (App Insights が自動計測する)

  ```kusto
  AppDependencies
  | where TimeGenerated > ago(1h)
  | project TimeGenerated, Type, Target, Name, ResultCode, DurationMs, Success, OperationId
  | order by TimeGenerated desc
  ```

- [ ] **1 往復が 1 本の線になる** — `OperationId` で request と dependency が並ぶ。並ばなければ相関が壊れている

  ```kusto
  AppRequests
  | where TimeGenerated > ago(1h)
  | project OperationId, ReqName = Name, ReqMs = DurationMs, ResultCode
  | join kind=leftouter (
      AppDependencies
      | where TimeGenerated > ago(1h)
      | project OperationId, DepTarget = Target, DepName = Name, DepMs = DurationMs, DepResult = ResultCode
    ) on OperationId
  | order by ReqMs desc
  ```

- [ ] **例外が引ける**

  ```kusto
  AppExceptions
  | where TimeGenerated > ago(24h)
  | project TimeGenerated, ProblemId, OuterType, OuterMessage, OperationId
  | order by TimeGenerated desc
  ```

- [ ] **BFF が自分で出した構造化ログ (`event=…`) が引ける** — 自動計測では出ない「縮退したか」「stub に落ちたか」がここに出る

  ```kusto
  AppTraces
  | where TimeGenerated > ago(1h)
  | where Message startswith "event="
  | extend ev = extract(@"event=(\S+)", 1, Message)
  | project TimeGenerated, ev, Message, OperationId
  | order by TimeGenerated desc
  ```

- [ ] **「呼んだが返ってこなかった」が名指しできる** — `dependency.start` に対応する `dependency.end` が無いものを出す。**これが #293 の再演を止める 1 本**

  ```kusto
  AppTraces
  | where TimeGenerated > ago(6h)
  | where Message has "event=dependency."
  | extend ev = extract(@"event=(\S+)", 1, Message),
           target = extract(@"target=(\S+)", 1, Message),
           sessionId = extract(@"sessionId=(\S+)", 1, Message)
  | summarize starts = countif(ev == "dependency.start"), ends = countif(ev == "dependency.end")
      by sessionId, target
  | where starts > ends
  ```

- [ ] **「無音」と「正常」を取り違えていない** — 0 行を「異常なし」と読まないため、**種類ごとの件数**を必ず併せて見る。全部 0 ならテレメトリ側が死んでいる

  ```kusto
  union withsource = Table AppRequests, AppDependencies, AppTraces, AppExceptions
  | where TimeGenerated > ago(1h)
  | summarize rows = count() by Table
  ```

- [ ] **診断設定側 (`FunctionAppLogs`) も生きている** — App Insights を落としてもここは残る想定なので、両方の生死を分けて見る

  ```kusto
  FunctionAppLogs
  | where TimeGenerated > ago(1h)
  | summarize count() by Category, Level
  ```

- [ ] **機微データが漏れていない** — `dropped=` は「許可されていないフィールドを載せようとして、テレメトリ層が落とした」痕跡。0 件が正常、出ていたらそのコードを直す (落とされてはいるが、書いた人の意図はズレている)

  ```kusto
  AppTraces
  | where TimeGenerated > ago(7d)
  | where Message has "dropped="
  | extend dropped = extract(@"dropped=(\S+)", 1, Message)
  | summarize count() by dropped
  ```

- [ ] **コストが宣言の内側にいる** — 日次上限 0.15 GB/日 (`lawDailyQuotaGb`) に対する実測

  ```kusto
  Usage
  | where TimeGenerated > ago(7d)
  | where IsBillable
  | summarize GB = sum(Quantity) / 1024.0 by DataType, bin(TimeGenerated, 1d)
  | order by TimeGenerated desc
  ```

## 何を記録し、何を落とすか

**この節が方針の正典。** 実装は `apps/bff/src/observability/telemetry.ts` が**フィールド名の許可リスト**で強制する — 許可されていない名前は値ごと捨てられ、`dropped=<名前>` だけが残る。

| 種別            | 例                                                                                             | 扱い                                                                                                                        |
| --------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 相談の本文      | ユーザー発話 / AI の応答 / 抽出された statement・excerpt / Problem の title・summary / concern | **載せない**。長さ (`chars`) と件数 (`count`) だけ                                                                          |
| 個人の識別子    | `userId` (Cosmos のパーティションキー)                                                         | **生では載せない**。`hashIdentifier()` の出力 (`userHash`) だけ                                                             |
| 不透明な ID     | `sessionId` / `approvalRequestId`                                                              | 載せてよい (生成された値で中身を含まない)。相関に要る                                                                       |
| 経路と結果      | route / method / status / outcome / 所要 ms / 下流の target・operation                         | 載せる。**これが無いと障害を追えない**                                                                                      |
| URL             | 下流の呼び先                                                                                   | `origin + pathname` だけ。**クエリ文字列は自動で落ちる**                                                                    |
| 認証情報        | Authorization ヘッダ / MI トークン / App Insights 接続文字列                                   | **一切載せない**。接続文字列は bicep の output にもしない                                                                   |
| 例外            | 種別 (`errorType`) と文面 (`errorMessage`)                                                     | 載せる。ただし**文面に payload を連結しない** — zod の失敗は `schemaIssues.ts` の `summarizeIssues` (場所と種別だけ) を通す |
| クライアント IP | —                                                                                              | App Insights の既定どおりマスクされる。`DisableIpMasking` は**足さない**                                                    |

新しいログを足すときは、まず `telemetry.ts` の `ALLOWED_FIELDS` を見る。**足したい名前がそこに無いなら、それは本文である可能性が高い。**

## Rollback

テレメトリのコストが問題になった / 障害の切り分けで一時的に止めたい場合。**段階を踏む** — いきなり全部落とすと「沈黙」と「正常」が区別できなくなる。

1. **サンプリングを絞る** (最初の一手)。`apps/bff/host.json` の `maxTelemetryItemsPerSecond` を下げる。`excludedTypes: "Request;Exception"` は**外さない** — 外すとリクエストと例外が間引かれ、「起きたのに記録が無い」が起きる
2. **App Insights だけ落とす**。`enableAppInsights=false` で bootstrap を再適用する。診断設定 (`FunctionAppLogs`) は残るので、実行された関数と例外は引き続き残る

   ```bash
   az deployment group create -g "$RG" -f cicd/iac/main-bootstrap.bicep \
     -p enableAppInsights=false ...   # 他のパラメータは既存の呼び出しに合わせる
   ```

3. **取り込み自体を止める**。`lawDailyQuotaGb` に当たると当日の収集が止まる (既定 0.15 GB/日)。これは日常の調整弁ではなく暴走時のブレーカー

> リソースを手で削除しない。次の bicep 適用で作り直されるうえ、App Insights は同名で再作成しても過去データが戻らない。

## Common Issues

### KQL は通るが 0 行しか返らない

- 原因: **「引けなかった」と「無かった」を混同している。** クエリの時間窓に実際のリクエストが 1 件も無い、または `APPLICATIONINSIGHTS_CONNECTION_STRING` が配られていない
- 対処: Verification の「無音と正常を取り違えていない」の union クエリで**種類ごとの件数**を先に見る。全テーブルが 0 なら配線側 (Step 1)、`AppRequests` だけ 0 ならリクエストが来ていない (= それ自体が答え)

### `WARN- Skipping Log Analytics query (no logAnalyticsCustomerId output)` が出る

- 原因: bootstrap の output に `logAnalyticsCustomerId` が無い。#307 以前の `main-bootstrap.bicep` は re-export しておらず、**smoke の LA チェックは一度も実行されたことがなかった**
- 対処: `main-bootstrap.bicep` の output を確認する。古いデプロイの output を見ている場合は bootstrap を再適用する

### `AppTraces` に `event=…` の行が出ない

- 原因: ログレベルが絞られている (`host.json` の `logLevel`)、または該当コードが `console.*` で書かれている (`console` はどの invocation の話か紐づかない / app-level 扱い)
- 対処: `apps/bff/src/observability/telemetry.ts` の `logEvent` / `trackDependency` 経由に直す。テレメトリの出口はここ 1 箇所

### ローカルで動かしたら実環境のワークスペースにログが飛んだ

- 原因: `apps/bff/local.settings.json` に `APPLICATIONINSIGHTS_CONNECTION_STRING` を手で入れている
- 対処: 空にする。**ローカルは空が既定** — 空ならホストは何も送らず、ログは端末に出るだけ

### `AppRequests` の SSE の所要時間が短すぎる / 長すぎる

- 原因: `/api/chat/stream` は**ストリームを開いた時点で return する**。BFF 側の `event=request.end` は `outcome=stream-opened` であって「流し切った」ではない
- 対処: 「最後まで流れたか」はホストの `Executed …` 行 (`FunctionAppLogs`) / `AppRequests` の `DurationMs` を見る。BFF の構造化ログだけで判断しない

## Related

- Issue: [#307](https://github.com/yomote/mind-inbox/issues/307) (この配線) / [#293](https://github.com/yomote/mind-inbox/issues/293) (観測性が無くて丸一日溶かした実例) / [#303](https://github.com/yomote/mind-inbox/issues/303) (設定を宣言に一本化)
- ADR: [ADR 0013 常設・低コスト dev](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [ADR 0046 環境は宣言から再構築できる](../adr/0046-environment-rebuildable-from-declaration.md) / [ADR 0030 Cosmos 永続化](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md)
- 関連 Runbook: [`ops-inspect.md`](ops-inspect.md) (サンドボックスから Azure の実態を取る) / [`cosmos-persistence.md`](cosmos-persistence.md)
- コード: `apps/bff/src/observability/telemetry.ts` (テレメトリの唯一の出口) / `apps/bff/host.json` (サンプリング)
- IaC: `cicd/modules/bootstrap-core.bicep` (`appInsights` / `diagFunctionApp`) / `cicd/iac/main-bootstrap.bicep` (output の re-export)
