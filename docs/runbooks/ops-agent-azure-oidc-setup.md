# 運用保守エージェント — Azure OIDC フェデレーション初期セットアップ

## Trigger

`.github/workflows/ops-agent.yml` (運用保守エージェント) を初めて有効化するとき。
Entra ID にアプリ登録 + フェデレーション資格情報を作り、`rg-dev-mind-inbox` への Reader 権限を割り当て、
GitHub 側に環境・変数・最小限のシークレットを登録する一度きりの手順。

判断の背景は ADR [0006](../adr/0006-autonomous-ops-agent-via-github-oidc.md) を参照。

## Prerequisites

- Entra ID でアプリ登録を作れる権限 (Application Administrator 相当)
- 対象サブスクリプションで role assignment を作れる権限 (Owner / User Access Administrator)
- `az` (Azure CLI) でログイン済み (`az login`)
- GitHub リポジトリ `yomote/mind-inbox` の admin 権限
- Anthropic API キー (エージェント実行用)

## Steps

### 1. アプリ登録 + サービスプリンシパルを作る

```bash
SUB_ID="<your-subscription-id>"
RG="rg-dev-mind-inbox"
APP_NAME="mindbox-ops-agent"
REPO="yomote/mind-inbox"
ENV_NAME="azure-ops"

az ad app create --display-name "$APP_NAME"
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
az ad sp create --id "$APP_ID"
```

### 2. フェデレーション資格情報を作る (GitHub OIDC・environment スコープ)

`subject` を GitHub 環境 `azure-ops` に縛る。これで他リポ・他環境からは
このトークン交換ができない (漏れても使い回せない)。

```bash
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-${ENV_NAME}\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${REPO}:environment:${ENV_NAME}\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"
```

### 3. Reader 権限を dev リソースグループだけに割り当てる

```bash
az role assignment create \
  --assignee "$APP_ID" \
  --role "Reader" \
  --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}"
```

> エージェントは Azure への**書き込み権限を持たない** (ADR 0006 / B3 不採用)。
> 変更は必ず PR の merge → 既存 CD を通す。

### 4. GitHub に識別子を「変数」として登録する (シークレットではない)

`client-id` / `tenant-id` / `subscription-id` は機密ではないので **Variables** で良い。

```bash
TENANT_ID=$(az account show --query tenantId -o tsv)
echo "AZURE_CLIENT_ID       = $APP_ID"
echo "AZURE_TENANT_ID       = $TENANT_ID"
echo "AZURE_SUBSCRIPTION_ID = $SUB_ID"
```

GitHub → Settings → Secrets and variables → Actions → **Variables** タブで上記 3 つを登録。

### 5. GitHub 環境を作る

GitHub → Settings → Environments → **New environment** → `azure-ops`。

- 任意で **Required reviewers** を付けると、ワークフロー実行に承認を挟める (より安全)
- 任意で **Deployment branches** を default branch のみに制限

### 6. 唯一のシークレットを登録する

エージェント実行のために `ANTHROPIC_API_KEY` だけ **Secrets** に登録する。

> これが現状ただ 1 つの保存シークレット。Azure 側はゼロ。
> 将来 Bedrock/Vertex の OIDC に寄せればこれも消せる (ADR 0006 Negative Consequences 参照)。

## Verification

- [ ] `az ad app federated-credential list --id "$APP_ID"` に `github-azure-ops` が出る
- [ ] `az role assignment list --assignee "$APP_ID" --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}"` に Reader が出る
- [ ] GitHub の Variables に `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` がある
- [ ] GitHub の環境 `azure-ops` が存在し、`ANTHROPIC_API_KEY` シークレットが登録済み
- [ ] `ops-agent.yml` を **Run workflow** (workflow_dispatch) で手動実行 → `azure/login` が成功し、Azure リソース一覧が取れる

## Rollback

エージェントを無効化したい場合:

1. `ops-agent.yml` の `schedule` をコメントアウト (or ワークフロー削除)
2. ロール割当を外す: `az role assignment delete --assignee "$APP_ID" --role Reader --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}"`
3. 完全撤去なら: `az ad app delete --id "$APP_ID"`

## Common Issues

### `AADSTS70021: No matching federated identity record found`

- 原因: ワークフローのトークン `subject` と、登録した `subject` が不一致。environment 名 / リポジトリ名のズレ、または job に `environment: azure-ops` を書き忘れ。
- 対処: ワークフローの job に `environment: azure-ops` があるか確認。`az ad app federated-credential list` の subject と突き合わせる。

### `az login` の OIDC で `AADSTS700016` / audience エラー

- 原因: フェデレーション資格情報の `audiences` が `api://AzureADTokenExchange` でない。
- 対処: Step 2 の audiences を確認して作り直す。

### エージェントが Azure を読めるが Issue/PR を作れない

- 原因: ワークフローの `permissions` 不足。
- 対処: `issues: write` / `pull-requests: write` / `contents: write` / `id-token: write` が job に付いているか確認。

## Related

- ADR: [0006 運用保守エージェントを GitHub Actions + OIDC で動かす](../adr/0006-autonomous-ops-agent-via-github-oidc.md)
- ワークフロー: `.github/workflows/ops-agent.yml`
- 関連 Runbook: [deploy.md](./deploy.md)
