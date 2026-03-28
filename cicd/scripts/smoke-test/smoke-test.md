# IaC deploy smoke test (connectivity + logging)

このドキュメントは、`main-bootstrap.bicep`（必要なら `main-config.bicep` も）をデプロイした後に「意図した接続が可能か」「それ以外が拒否されるか」「ログが残っているか」を確認するための手順です。

## 前提

- `az` (Azure CLI) が使えること
- `curl` が使えること
- Azure にログイン済み: `az login`

> 注意: 現状の IaC では、SQL の診断設定は作成されますが `logs/metrics` のカテゴリが **未指定** です。そのため Log Analytics にデータが入らない可能性が高いです（= テストで検出したいポイント）。

## 0. 必要情報

- リソースグループ: `$RG`
- デプロイ名: `$DEPLOYMENT`

デプロイ outputs から以下を取得します:

- `staticSiteDefaultHostname`
- `functionAppDefaultHostname`
- `sqlServerFqdn`
- `logAnalyticsCustomerId`

## 1. 速攻チェック（推奨）

スクリプトでまとめて実行:

```bash
cd cicd
chmod +x ./scripts/smoke-test/smoke-test.sh
RG=<your-rg> DEPLOYMENT=<your-deployment-name> ./scripts/smoke-test/smoke-test.sh
```

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
