# 0055. BFF のサーバ側観測性を workspace-based Application Insights で持つ (保持 30 日 / 日次上限つき)

- Status: Proposed
- Date: 2026-08-14
- Deciders: PO (yomote), PM セッション
- Consulted: Codex (PR #413 レビュー)
- Informed: —

Technical Story: <https://github.com/yomote/mind-inbox/issues/307> / <https://github.com/yomote/mind-inbox/pull/413>

## Context and Problem Statement

実環境で何かがおかしいとき、いま手元にあるのは**ブラウザ側の証拠だけ**だった。#293 では
「SSE がハングしている」という仮説で丸一日を溶かしたが、**サーバ側に記録が無かったので
「そもそもリクエストが BFF に届いていたのか」すら確定できなかった**。BFF (Azure Functions) は
相談フローの司令塔で、ai-agent / VOICEVOX / Cosmos への全ホップがここを通る。ここに記録が
無い限り、同じ誤診は繰り返される。

既にある診断設定 (`FunctionAppLogs`) では足りない。残るのは「実行された関数と例外」だけで、
**「下流を呼んだのか / 何 ms で何が返ったのか」に答えられない**。

一方でこのプロダクトが扱うのは**相談の本文** — ユーザーが誰にも言えていないことである。
サーバ側の記録を増やすということは、その本文が漏れうる新しい面を作ることでもある。
テレメトリ基盤に入った行は保持期間のあいだ残り、Azure のロールを持つ人なら誰でも読める。
Cosmos のアクセス制御 ([ADR 0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md))
を**回り込む**経路になりうる。

さらに、Log Analytics は「取り込んだ GB」で課金される。常設・待機最小コストの dev 環境
([ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md)) に、観測性のために
青天井の従量課金を持ち込むわけにはいかない。

よってこれは「ログを足す」実装詳細ではなく、**永続テレメトリ資源の新設 + 保持・コスト・
機微データの境界を決めるアーキテクチャ判断**である。PR #413 の Codex レビューで
「Bicep 内コメントと Runbook だけでは意思決定の不変記録にならない」と指摘され、本 ADR を置く。

## Decision Drivers

- **「サーバに証拠が無い」を終わらせる** — 少なくとも「犯人はサーバに居ない」を数分で言えること (#293)
- **相談の本文をテレメトリに載せない** — 「気をつけて書く」ではなく構造で守れること
- **コストの上限が宣言側にあること** — 手設定や運用の注意力に依存しない (次のデプロイで静かに消えない)
- **待機コストを増やさない** — scale-to-zero の前提 ([ADR 0002](0002-container-apps-not-aks.md) / ADR 0013) を崩さない
- **アプリのコード変更なしに下流ホップが取れること** — 計測コードの書き忘れが観測の穴にならない

## Considered Options

- Option A: workspace-based Application Insights を新設し、既存 LAW を実体とする (本 ADR)
- Option B: App Insights を作らず、診断設定 (`FunctionAppLogs`) + 自前の構造化ログだけで LAW に直送する
- Option C: サーバ側の観測性を導入しない (現状維持 — ブラウザ側の証拠だけで追う)
- Option D: classic Application Insights (workspace 無し) を作る

## Decision Outcome

Chosen option: **"Option A"**、理由は 3 つ。(1) HTTP の依存呼び出しが**アプリのコード変更なしに**
自動計測され、BFF → ai-agent が「呼んだ / 返った / 何 ms / ステータス」として残るのは既製品では
ここだけ。(2) workspace-based は取り込み・保持の課金が**既存 LAW 側**で起きるので、新しい課金面が
増えず、既にある `lawDailyQuotaGb` と 30 日保持がそのままブレーカーとして効く。(3) 待機課金が無く、
課金は取り込んだ GB だけなので scale-to-zero の前提を崩さない。

### 決めたこと (この ADR が固定する境界)

1. **受け皿は workspace-based App Insights 1 個** (`appi-<env>-<app>`)。実体は既存の
   Log Analytics ワークスペースに入る。`enableAppInsights` (既定 `true`) で切れる —
   `false` にすると診断設定の `FunctionAppLogs` だけが残る (可視性は落ちるが沈黙はしない)
2. **保持は 30 日** (`lawRetentionInDays`)。障害の切り分けに要る窓であり、それ以上
   相談に紐づくデータを持たない。**延ばすなら本 ADR を改訂する**
3. **日次取り込み上限 0.15 GB/日** (`lawDailyQuotaGb`)。31 日で約 4.65 GB < 無料枠 5 GB/月。
   これは**暴走時のブレーカーであって日常の調整弁ではない** — 当たると当日の収集が止まる
   (= 可視性を失うが請求は跳ねない、という向きに倒す)
4. **サンプリング方針**: `host.json` の適応サンプリング (`maxTelemetryItemsPerSecond: 5`) を効かせるが、
   **Request と Exception は `excludedTypes` で常に除外する = 常時記録する**。
   ここを間引くと「起きたのに記録が無い」が生まれ、**沈黙と正常が区別できなくなる**
   (CLAUDE.md の「取れなかったものを異常なしと書かない」がテレメトリ側に現れたもの)
5. **機微データは名前と値の両面で落とす**。片方だけでは守れない:
   - **名前**: `telemetry.ts` の `ALLOWED_FIELDS` に無いフィールドは値ごと捨て、`dropped=<名前>` だけを出す
     (黙って消すと「載せたつもり」と「落とされた」が区別できない)
   - **値**: 許可された名前に本文を入れれば素通りするので、**組み立て元で正規化する**。
     `url` はクエリと userinfo を落とす / `errorMessage` は 1 行化 + 上限 / `TRPCError.message` は
     `code` と値を落とした要約に潰す / **ID (`sessionId` / `userId`) は出口で必ずハッシュ化する**
     (`sessionId` はスキーマが長さしか見ていない自由文字列で、相関キーの顔をして本文を運べる)
   - クライアント IP は App Insights の既定どおりマスクされる。`DisableIpMasking` は**足さない**
6. **ホストの自動収集も同じ境界に入れる**。接続文字列を配ると Functions ホストは
   `telemetry.ts` を通さずに `AppRequests` を作り、**受信 URL をそのまま**記録する。
   tRPC の query 入力は `?input={"id":"…"}` として URL に載るので、アプリ側で塞いだ本文が
   ここから漏れる。よって `host.json` で
   `httpAutoCollectionOptions.enableHttpTriggerExtendedInfoCollection: false` にし、
   **受信 URL・HTTP メソッド・`ResultCode` を収集させない**。失う情報はアプリ自身が出す
   `event=request.*` (値は固定文字列) で代替する。「収集は許して取り込み時に `Url` 列を潰す」
   (workspace transformation DCR) は採らない — ワークスペースに 1 個しか置けない特異点を新設し、
   KQL を誤ると `AppRequests` が丸ごと落ちる沈黙を作るため
7. **接続文字列は bicep の output にしない**。取り込みキーを含むため、deployment の出力に
   残すと `az deployment group show` で誰でも読める。ローカルの `local.settings.json` にも入れない
   (入れるとローカルの相談内容が実環境のワークスペースへ飛ぶ)

### Positive Consequences

- 「そもそも呼ばれたのか」「下流は何 ms で何を返したのか」に**数分で**答えられる (#293 の再演を止める)
- 依存呼び出しの自動計測により、計測コードの書き忘れが観測の穴にならない
- 「本文を載せない」が**構造** (許可リスト + 出口での正規化) になり、レビューの注意力に依存しない
- 保持もコストも既存 LAW のブレーカーの内側に入るので、監視すべき課金面が増えない

### Negative Consequences

- **Azure リソースが 1 個増える** (`appi-*`)。宣言から作り直せる ([ADR 0046](0046-environment-rebuildable-from-declaration.md)) が、
  App Insights は同名で再作成しても**過去データは戻らない**
- **公開の ingestion / query 面が増える** (`publicNetworkAccessForIngestion/Query: Enabled`)。
  取り込みキーを持つ者はテレメトリを注入でき、Reader 相当を持つ者は読める
- **可視性とコストがトレードオフになる**。日次上限に当たった日は収集が止まり、その時間帯は
  「無音」になる — Runbook の Verification で**種類ごとの件数**を必ず併せて見る運用を要求する
- **サンプリングは traces を間引く**。BFF が自分で出した `event=…` 行は取りこぼしうるので、
  「1 件も無い = 起きていない」とは読めない (Request / Exception だけが常時記録)
- **`AppRequests` が痩せる**。ホストの拡張情報収集を切った代償で `Url` は空、`ResultCode` は常に `0`、
  `Name` は関数名だけになる。**`ResultCode` を成否の判定に使えない**ので、HTTP ステータスは
  サンプリングされうる `AppTraces` 側の `event=request.end` に依存する — 「Request は常時記録」という
  保険が status には効かない
- **関数に到達しなかったリクエスト** (認証拒否 / 未知のルート) は `AppRequests` に残らなくなる。
  ホスト側の実行ログ (`FunctionAppLogs` の `Executing/Executed HTTP request`) で見る。
  なおこちらは元からパスだけでクエリ文字列を含まない
- **ホスト側の境界が効いているかは自動テストでは守れない**。宣言が消えていないことしか見られないので、
  Runbook の「漏洩点検」の KQL (`AppRequests | where isnotempty(Url)`) を実測の側に置く

## Pros and Cons of the Options

### Option A: workspace-based Application Insights

Function App に `APPLICATIONINSIGHTS_CONNECTION_STRING` を配り、実体を既存 LAW に置く
(`cicd/modules/bootstrap-core.bicep` の `appInsights`)。

- Good, because HTTP 依存呼び出しが**アプリのコード変更なしに**自動計測される (`AppDependencies`)
- Good, because 1 往復が `OperationId` で 1 本の線になり、request と dependency を突き合わせられる
- Good, because 取り込み・保持の課金が既存 LAW 側で起き、`lawDailyQuotaGb` と 30 日保持がそのまま効く
- Good, because 待機課金が無い (課金は取り込んだ GB だけ) ので scale-to-zero の前提を崩さない
- Bad, because リソースと公開 ingestion/query 面が 1 つ増える
- Bad, because 適応サンプリングの挙動を理解していないと「traces が無い = 起きていない」と誤読しうる

### Option B: App Insights を作らず、診断設定 + 自前の構造化ログだけで LAW に直送する

`FunctionAppLogs` に出る `console` / `context.log` の行だけで追う。

- Good, because 新しいリソースも公開面も増えない (今ある LAW に閉じる)
- Good, because 何がログに出るかを 100% アプリ側で制御できる
- Bad, because **下流ホップの自動計測が無い**。BFF → ai-agent の所要 ms とステータスを
  取るには全呼び出し箇所に手で計測を書く必要があり、**書き忘れがそのまま観測の穴になる**
  (#293 で欲しかったのはまさにこの層)
- Bad, because request / dependency / exception の相関 (`OperationId`) を自前で組む必要がある
- Bad, because 例外のスタックが構造化されず、`AppExceptions` 相当の検索性が得られない

### Option C: サーバ側の観測性を導入しない (現状維持)

- Good, because コストも公開面もゼロ。追加の機微データ経路も生まれない
- Bad, because **#293 がそのまま再演する**。「サーバに届いていたか」に答えられない状態は、
  1 件の誤診で丸一日を溶かす実績がある (観測性の欠如のコストは既に支払い済み)
- Bad, because 本番障害の切り分けが「ブラウザ側の証拠 + 推測」に固定され、
  CLAUDE.md の「取れなかったものを異常なしと書かない」を守れない (そもそも取れない)

### Option D: classic Application Insights (workspace 無し)

- Good, because 構成が 1 リソースで完結する
- Bad, because **保持もコストも別勘定**になり、`lawDailyQuotaGb` / `lawRetentionInDays` の
  ブレーカーが効かない。上限を宣言側に置くという Decision Driver を満たせない
- Bad, because Microsoft が retirement を進めており、いずれ移行が必要になる

## Links

- Issue: <https://github.com/yomote/mind-inbox/issues/307> (この配線) / <https://github.com/yomote/mind-inbox/issues/293> (観測性が無くて丸一日溶かした実例)
- PR: <https://github.com/yomote/mind-inbox/pull/413>
- Runbook: [`docs/runbooks/bff-telemetry.md`](../runbooks/bff-telemetry.md) — **何を記録し何を落とすかの正典**と、流れているかの確かめ方
- コード: `apps/bff/src/observability/telemetry.ts` (アプリが出す行の唯一の出口) / `apps/bff/host.json` (サンプリング + ホストの自動収集の境界)
- IaC: `cicd/modules/bootstrap-core.bicep` (`appInsights` / `lawDailyQuotaGb`) / `cicd/iac/main-bootstrap.bicep`
- 関連 ADR: [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) (常設・低コスト dev) / [0046](0046-environment-rebuildable-from-declaration.md) (宣言から再構築) / [0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) (機微データの本来の置き場)
