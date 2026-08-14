# IaC deploy smoke test (connectivity + logging)

このドキュメントは、`main-bootstrap.bicep`（必要なら `main-config.bicep` も）をデプロイした後に「意図した接続が可能か」「それ以外が拒否されるか」「ログが残っているか」を確認するための手順です。

## 前提

- `az` (Azure CLI) が使えること
- `curl` が使えること
- Azure にログイン済み: `az login`

> 注意: **SQL 一式は `enableSql=true` の時だけ作られる**（既定 false / [ADR 0013](../../../docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md)）。既定構成では §2.4 以降の SQL 系チェックは自動 skip される。
> 有効にした場合、SQL の診断設定は作成されるが `logs/metrics` のカテゴリが **未指定**のため Log Analytics にデータが入らない可能性が高い（= テストで検出したいポイント）。

## 0. 必要情報

- リソースグループ: `$RG`
- デプロイ名: `$DEPLOYMENT`

デプロイ outputs から以下を取得します:

- `staticSiteDefaultHostname`
- `functionAppDefaultHostname`
- `sqlServerFqdn` — `enableSql=false`（既定, ADR 0013）だと空。SQL 系チェックは自動 skip される
- `sqlEnabled` — `enableSql` の真偽。`true` かつ `sqlServerFqdn` が空なら SQL provisioning 失敗とみなし **NG**（意図的 skip と本当の失敗を区別する）
- `functionEasyAuthEnabled` — Functions の認可 (#69)。`true` なら **未認証で 200 が返ったら NG**（門が開きっぱなし = 誰でも OpenAI を消費できる）。あわせて CORS preflight が 401 に巻き込まれていないかも確認する
- `staticSiteSkuName` — Free では linked backend を持たない設計のため、SWA 配下に API が無いのは正常（skip 扱い）
- `logAnalyticsCustomerId`

## 1. 速攻チェック（推奨）

スクリプトでまとめて実行:

```bash
cd cicd
chmod +x ./scripts/smoke-test/smoke-test.sh
RG=<your-rg> DEPLOYMENT=<your-deployment-name> ./scripts/smoke-test/smoke-test.sh
```

## 1.5 実態ダンプ（判定しない・PR に貼る用）

`smoke-test.sh` が「合否」を出すのに対し、`inspect-env.sh` は **「今どうなっているか」を read-only で吐くだけ**のスクリプト（[ADR 0018](../../../docs/adr/archive/operations/runtime-verification-in-the-loop.md)）。PR の `Verification` 欄にそのまま貼れる markdown を出す。

```bash
RG=<your-rg> DEPLOYMENT=<your-deployment-name> cicd/scripts/smoke-test/inspect-env.sh
# ファイルに落とす場合
RG=... DEPLOYMENT=... cicd/scripts/smoke-test/inspect-env.sh > /tmp/inspect.md
```

出す内容:

| 節     | 中身                                                                                                                                                                     |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 認可   | EasyAuth の実設定（`requireAuthentication` / `unauthenticatedClientAction` / `excludedPaths` / audience / issuer）と、**未認証 GET / CORS preflight を実際に叩いた結果** |
| 露出   | 各 Container App の `external` / IP 制限ルール数 / **FQDN への匿名アクセスの応答コード**                                                                                 |
| 配置   | 認識されている関数、`WEBSITE_RUN_FROM_PACKAGE`（SAS は伏せる）、アプリ最終更新                                                                                           |
| コスト | 予算アラートの上限と当月実績                                                                                                                                             |

### 読むときの注意

- **判定しない**。exit code は「ダンプできたか」だけで、中身の良し悪しは表さない。合否が要るなら `smoke-test.sh`
- **取得できなかった項目は `(未検証: 理由)` と出る**。ネットワーク制限のある環境（エージェントのコンテナ等）からだと到達確認だけ落ちるので、そこを「異常なし」と読み替えないこと
- **EasyAuth は `az webapp auth show` で見ない**。あれは V1 (classic) 射影を返し、実際が `Return401` でも `RedirectToLoginPage` と答える（実測）。スクリプトは `authsettingsV2` を ARM から直接読んでいる

環境変数:

| 変数                | 既定               | 用途                             |
| ------------------- | ------------------ | -------------------------------- |
| `RG` / `DEPLOYMENT` | （必須）           | 対象リソースグループとデプロイ名 |
| `CURL_TIMEOUT`      | `20`               | 到達確認のタイムアウト秒         |
| `CORS_ORIGIN`       | SWA の既定ホスト名 | preflight で送る `Origin`        |

## 2. 手動チェック（要点）

### 2.1 SWA (frontend) が公開されている

```bash
SWA_HOST=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.staticSiteDefaultHostname.value" -o tsv)
curl -fsS "https://$SWA_HOST" >/dev/null && echo OK
```

### 2.2 /api/health が動く（意図した接続）

フロントは同一オリジンの `/api/health` を叩く設計です（`VITE_API_BASE_URL` 未設定なら `""`）。

```bash
curl -fsS "https://$SWA_HOST/api/health" | head
```

### 2.3 Function App の直アクセス確認（※ブロックしたいなら要追加設定）

IaC には Function App へのアクセス制限（IP 制限 / Private Endpoint / Front Door 経由限定など）がありません。
そのため、直アクセスは **通ってしまう** 可能性があります。

```bash
FUNC_HOST=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.functionAppDefaultHostname.value" -o tsv)
curl -fsS "https://$FUNC_HOST/api/health" | head
```

### 2.4 SQL はパブリックから拒否される（意図した拒否）

SQL Server は `publicNetworkAccess: Disabled` なので、インターネット側からは接続できない想定です。

```bash
SQL_FQDN=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.sqlServerFqdn.value" -o tsv)
# TCP/1433 が開いていないことを確認（成功したらNG）
timeout 5 bash -lc "</dev/tcp/$SQL_FQDN/1433" && echo "NG: public connect succeeded" || echo "OK: blocked"
```

### 2.5 Private Endpoint / Private DNS の構成確認（設定の正しさ）

```bash
SQL_SERVER_NAME=${SQL_FQDN%%.*}
az network private-endpoint show -g "$RG" -n "pe-$SQL_SERVER_NAME" --query "properties.privateLinkServiceConnections[0].properties.privateLinkServiceConnectionState.status" -o tsv
az network private-endpoint dns-zone-group list -g "$RG" --endpoint-name "pe-$SQL_SERVER_NAME" -o table
```

### 2.6 Log Analytics にログが入っているか

現状 IaC のままだと、SQL 診断設定のカテゴリが空のため、基本的に何も入らないはずです。

```bash
LAW_CUSTOMER_ID=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.logAnalyticsCustomerId.value" -o tsv)
az monitor log-analytics query -w "$LAW_CUSTOMER_ID" --analytics-query "AzureDiagnostics | take 5" -o table
```

結果が 0 行なら「カテゴリ未設定」または「まだ流入前」の可能性があります。まずは SQL Server の Diagnostic settings の `logs/metrics` が enabled になっているか確認してください。
