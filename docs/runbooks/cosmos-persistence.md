# Cosmos DB 永続化の運用

## Trigger

- 困りごと / 履歴が「保存したのに消えている」と報告されたとき
- 永続化を初めてデプロイした / Cosmos アカウントを作り直したとき (無料枠の取得確認)
- **ユーザーから自分のデータを全部消してほしいと言われたとき** (NFR-1「ユーザーによる削除権」)
- `golden-path-monitor` の「永続化プローブ」ステップが赤くなったとき

方式の判断は [ADR 0030](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md)。ここは手順だけ。

## Prerequisites

- Azure CLI (`az`) でデプロイ先サブスクリプションにログイン済み
- リソースグループ (既定 `rg-dev-mind-inbox`) への Contributor 相当
- **data plane を直接叩く操作には別途 RBAC が要る** — アカウントキーは `disableLocalAuth: true` で無効化されており、Portal の Data Explorer も自分に data plane ロールを割り当てないと開けない (下の「Common Issues」参照)
- 環境変数の既定: `RG=rg-dev-mind-inbox` / `DEPLOYMENT=main-bootstrap`

## 構成の要点 (手順の前提)

|                    |                                                                   |
| ------------------ | ----------------------------------------------------------------- |
| アカウント         | `cosmos-dev-mindbox` (bicep が `cosmos-{env}-{appname}` で作る)   |
| リージョン         | **Japan East** (ADR 0030 D6 / NFR-1「保管リージョンの明確化」)    |
| DB / コンテナ      | `mindinbox` / `problems` `history`                                |
| パーティションキー | `/userId` (EasyAuth の oid、ローカルは `local`)                   |
| 認証               | **Managed Identity + data plane RBAC のみ**。アカウントキーは無効 |
| 触るサービス       | **BFF (Functions) だけ**。ai-agent からは繋がない (ADR 0030 D4)   |
| 暗号化             | 保存時暗号化は Cosmos の既定 (サービス管理キー。設定不要 / NFR-1) |

器の宣言はすべて `cicd/modules/bootstrap-core.bicep` にある。**`az cosmosdb` で手作りしない** — IaC の外で足した設定は再デプロイで消える。

## Steps

### 1. デプロイ状態と無料枠を確認する

```bash
RG=rg-dev-mind-inbox
az deployment group show -g "$RG" -n main-bootstrap \
  --query 'properties.outputs.{account:cosmosAccountName.value, endpoint:cosmosEndpoint.value, db:cosmosDatabaseName.value, region:cosmosLocation.value, freeTier:cosmosFreeTierEnabled.value}'
```

`enableFreeTier: true` で作ったアカウントは、無料枠が既に消費済みだと**作成そのものが失敗する**。つまり **デプロイが成功していれば無料枠は取れている**。実際の値も確認しておく:

```bash
az cosmosdb show -g "$RG" -n cosmos-dev-mindbox \
  --query '{freeTier:enableFreeTier, localAuthDisabled:disableLocalAuth, locations:locations[].locationName, totalRuLimit:capacity.totalThroughputLimit}'

# DB に張り付いている RU/s
az cosmosdb sql database throughput show -g "$RG" -a cosmos-dev-mindbox -n mindinbox \
  --query 'resource.{throughput:throughput, autoscaleMax:autoscaleSettings.maxThroughput}'
```

公表値は 1,000 RU/s + 25 GB。**実測が公表値と違ったら [ADR 0030](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md) の「実測」節に追記する。**

### 2. BFF の結線を確認する

```bash
az functionapp config appsettings list -g "$RG" -n func-dev-mindbox \
  --query "[?starts_with(name,'COSMOS_')].{name:name,value:value}" -o table
```

`COSMOS_ENDPOINT` が空 / 無いと、BFF は**黙って in-memory リポジトリで動く** (ローカルの既定と同じ挙動 / ADR 0030 D7)。エラーにならないので、「保存できているのに翌日消える」の第一容疑者はここ。

### 3. Managed Identity のロール割り当てを確認する

```bash
FUNC_MI="$(az functionapp identity show -g "$RG" -n func-dev-mindbox --query principalId -o tsv)"
az cosmosdb sql role assignment list -g "$RG" -a cosmos-dev-mindbox \
  --query "[?principalId=='$FUNC_MI'].{role:roleDefinitionId, scope:scope}" -o table
```

`00000000-0000-0000-0000-000000000002` (Cosmos DB Built-in Data Contributor) が付いていること。**これは control plane の「共同作成者」とは別物**で、Portal のロール割り当て画面には出てこない。

### 4. 振る舞いで確かめる (ADR 0018)

「設定したか」ではなく振る舞いで確認する。

```bash
# 再起動で消えないこと
cicd/scripts/smoke-test/golden-path.sh                    # 相談 → 困りごとを作る
az functionapp restart -g "$RG" -n func-dev-mindbox
cicd/scripts/smoke-test/persistence-probe.sh              # 一覧に残っているか
```

翌日以降の残存 (Y1 のリサイクルを跨いだ実測) は `golden-path-monitor` の「永続化プローブ」ステップが毎朝自動で見る。手動で回すなら:

```bash
MIN_AGE_SEC=60 cicd/scripts/smoke-test/persistence-probe.sh
```

初回は「前回のマーカーが無い」ので seed を置いて終わる。**2 回目以降が本番の検証**。

### 5. ユーザーのデータを全部消す (NFR-1「ユーザーによる削除権」)

> [!IMPORTANT]
> **不可逆。** 実行前に本人からの依頼であることを確認する。UI からの削除は v1 のスコープ外なので、当面はこの手順が唯一の窓口。

データはすべて `userId` のパーティションに入っているので、**パーティションを消せばそのユーザーのデータは全消し**になる。

1. 対象の `userId` (Entra の oid) を確認する。本人の Entra オブジェクト ID:

   ```bash
   az ad user show --id "<user@example.com>" --query id -o tsv
   ```

   ローカル / 認証なしで作られたデータは `local` に入っている。

2. 実行主体に data plane のロールを一時的に付ける (キーが無いので、これをやらないと読み書きできない):

   ```bash
   ME="$(az ad signed-in-user show --query id -o tsv)"
   az cosmosdb sql role assignment create -g "$RG" -a cosmos-dev-mindbox \
     --role-definition-id 00000000-0000-0000-0000-000000000002 \
     --principal-id "$ME" \
     --scope "/"
   ```

3. パーティション単位で削除する。partition key delete は preview 機能なので、**確実なのは Data Explorer / SDK で 1 件ずつ消す**こと。データ量は 1 ユーザー 1 年で約 270 KB (数百件) なので、素朴なループで十分:

   ```bash
   # Portal の Data Explorer で以下を実行して件数と id を確認
   #   SELECT c.id FROM c WHERE c.userId = "<oid>"
   # そのうえで各コンテナ (problems / history) のアイテムを削除する
   ```

   一括で消してよいなら、**コンテナごと消して bicep で作り直す**のが最も確実:

   ```bash
   az cosmosdb sql container delete -g "$RG" -a cosmos-dev-mindbox \
     -d mindinbox -n problems --yes
   az cosmosdb sql container delete -g "$RG" -a cosmos-dev-mindbox \
     -d mindinbox -n history --yes
   # 器を宣言から作り直す (手で作らない)
   cicd/scripts/deploy/provision.sh
   ```

   **単一ユーザーのうちは後者で十分**。複数ユーザーになったら「他人のデータを巻き込む」ので使えなくなる — その時点でアイテム単位の削除経路 (できれば UI) を用意すること。

4. 手順 2 で付けた一時ロールを**必ず外す**:

   ```bash
   az cosmosdb sql role assignment list -g "$RG" -a cosmos-dev-mindbox \
     --query "[?principalId=='$ME'].id" -o tsv \
     | xargs -r -I{} az cosmosdb sql role assignment delete -g "$RG" -a cosmos-dev-mindbox --role-assignment-id {} --yes
   ```

5. 削除したことを記録に残す (依頼者 / 日時 / 対象 userId / 消した件数)。

## Verification

- [ ] `az cosmosdb show ... --query disableLocalAuth` が `true`
- [ ] アカウントキーで data plane を叩くと**拒否される** (キー認証が死んでいることの実測)。`disableLocalAuth: true` ならキーの取得自体が失敗する:

  ```bash
  az cosmosdb keys list -g "$RG" -n cosmos-dev-mindbox --type keys
  ```

- [ ] `az cosmosdb sql role assignment list` に Function App の MI が Data Contributor で載っている
- [ ] Function App の app settings に `COSMOS_ENDPOINT` がある
- [ ] `cicd/scripts/smoke-test/persistence-probe.sh` が PASS (2 回目以降の run で `SURVIVED` が出る)
- [ ] `az functionapp restart` 後も困りごと一覧に同じ id が残っている

## Rollback

**永続化を止めて in-memory に戻す** (Cosmos が原因の障害を切り分けるとき):

```bash
az functionapp config appsettings delete -g "$RG" -n func-dev-mindbox \
  --setting-names COSMOS_ENDPOINT
az functionapp restart -g "$RG" -n func-dev-mindbox
```

BFF は `COSMOS_ENDPOINT` が無ければ in-memory にフォールバックするので、**この 1 手で切り戻せる** (データは Cosmos に残ったまま。書き戻せば復帰する)。恒久的に戻すなら bicep の `enableCosmos: false` にすること — app settings の手動削除は再デプロイで元に戻る。

## Common Issues

### 一覧が毎日空になる / 保存したのに翌日消える

- 原因: `COSMOS_ENDPOINT` が Function App に設定されていない。BFF は未設定だと in-memory にフォールバックし、**エラーを出さない**。Functions Y1 はアイドルでリサイクルされるので翌日には空になる
- 対処: 手順 2 で結線を確認 → 無ければ `provision.sh` で bicep から再デプロイ (`az` で手当てしない)

### BFF が 403 (Forbidden) を返す

- 原因: Managed Identity への data plane ロール割り当てが無い / 反映前。control plane の Contributor があっても data plane は通らない
- 対処: 手順 3 で確認。作り直した直後はロール割り当ての反映に数分かかることがある

### Portal の Data Explorer が開けない / キーが取れない

- 原因: `disableLocalAuth: true` なので**これが正常**。アカウントキーは発行されない (ADR 0030 D3)
- 対処: 自分の Entra プリンシパルに data plane ロールを一時的に付ける (手順 5-2)。**作業後に必ず外す**

### デプロイが `Free tier has already been applied` 等で失敗する

- 原因: サブスクリプションで既に別のアカウントが無料枠を使っている (1 サブスクに 1 つ)
- 対処: 既存アカウントを消して枠を返す (再取得できる) か、`enableCosmosFreeTier: false` + `cosmosServerless: true` でフォールバックする ([ADR 0030](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md) D2)。**無料枠は後から有効化できない** — 逃したら作り直すしかない

### 一覧の並びがおかしい

- 原因: 並び順はストア任せにしていない (`ORDER BY c.lastMentionedAt DESC` / `ORDER BY c.createdAt DESC` を SQL で明示している) ため、順序が崩れているなら実装かインデックスの問題
- 対処: `persistence-probe.sh` が `ORDER` を報告する。既定のインデックスポリシー (全プロパティ) を変えていないか確認する

## Related

- ADR: [0030 永続化は Cosmos DB 1 本に寄せる](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md) / [0013 常設 dev 環境の予算](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0018 動作検証をループに組み込む](../adr/0018-runtime-verification-in-the-loop.md) / [0017 Container Apps の認証の門](../adr/0017-container-apps-access-via-auth-gate.md)
- 要件: `docs/design/requirements.md` FR-4 / NFR-1
- 関連 Runbook: [`entra-spa-auth-and-budget.md`](entra-spa-auth-and-budget.md) / [`azure-oidc-cd-setup.md`](azure-oidc-cd-setup.md)
- スクリプト: `cicd/scripts/smoke-test/persistence-probe.sh`
- IaC: `cicd/modules/bootstrap-core.bicep` の「Cosmos DB」節
