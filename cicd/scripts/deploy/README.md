# Deploy scripts (manual)

IaC はリソース作成まで（SWA/Functions/SQL…）。
このフォルダは **フロント/バックエンド成果物を手動でデプロイ**するためのスクリプトです。

## 共通前提

- `az` (Azure CLI) でログイン済み
- リソースグループ `RG` と、IaC のデプロイ名 `DEPLOYMENT` が分かる（通常 `main-bootstrap`）

デフォルト:

- `RG=rg-dev-mind-inbox`
- `DEPLOYMENT=main-bootstrap`

## Frontend (SWA)

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-frontend.sh
```

- SWA の deployment token は `az staticwebapp secrets list` から取得し、`swa deploy` に渡します。
- SWA Free は静的ファイルの匿名配信のみ (#69 / ADR 0013)。認可の門は Functions EasyAuth 側にあり、
  SWA の app settings に認証情報は置きません（SWA Free はカスタム認証非対応）。
- 認証まわりのビルド時変数 `VITE_BFF_BASE_URL` / `VITE_ENTRA_CLIENT_ID` / `VITE_ENTRA_TENANT_ID` は
  deployment outputs から自動解決されます（env で明示指定して上書きも可）。
  Entra の値が解決できない場合は「認証無効ビルド」を警告付きで出します。
  詳細は runbook [`entra-spa-auth-and-budget.md`](../../../docs/runbooks/entra-spa-auth-and-budget.md)。

## Backend (Azure Functions)

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-backend.sh
```

- `backend/` をビルドし、production dependencies のみ残した zip を作成して
  `az functionapp deployment source config-zip` で反映します。

## Container Apps (ai-agent / voicevox-wrapper)

```bash
cd cicd
# IMAGE_TAG は sha-<full-sha> を明示指定すること (:latest は no-op になる, #107)
RG=<your-rg> DEPLOYMENT=<deployment-name> IMAGE_TAG=sha-<full-sha> ./scripts/deploy/deploy-ai-agent.sh
RG=<your-rg> DEPLOYMENT=<deployment-name> IMAGE_TAG=sha-<full-sha> ./scripts/deploy/deploy-voicevox-wrapper.sh
```

タグの決め方・ロールバック・据え置きの確認手順は runbook
[`ghcr-images.md`](../../../docs/runbooks/ghcr-images.md) が真実です。

## All

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-all.sh
```

`deploy-all.sh` は成果物デプロイ専用です（IaC は実行しません）。
Entra 認証の有効化/更新は、先に `main-config.bicep` デプロイを実行してください。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

- **既定では Entra アプリ登録を削除しません** (`DELETE_ENTRA_APP=false`)。アプリ登録は RG ではなく**テナントのオブジェクト**で、RG の撤収が持ち主ではないためです ([ADR 0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) D5)。
- **soft-delete の purge も既定で行いません** (同 D6)。purge は復旧手段を消すので、同名で作り直すために残骸を退ける必要が出たときだけ `PURGE_DELETED_KEYVAULTS=true PURGE_DELETED_COGNITIVE_SERVICES=true` を付けてください。
