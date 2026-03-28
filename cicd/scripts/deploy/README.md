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
- `frontend/public/staticwebapp.config.json` の `<TENANT_ID>` は、配備時に以下の優先順で実値へ置換されます。
  1. `ENTRA_TENANT_ID`
  2. SWA の app setting `AZURE_TENANT_ID`
  3. `az account show --query tenantId`
- `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` が SWA の app settings に
  未設定の場合は、以下を指定すると Key Vault から取得して同時に設定します。

```bash
cd cicd
RG=<your-rg> \
DEPLOYMENT=<deployment-name> \
ENTRA_APP_KEYVAULT_NAME=<keyvault-name> \
ENTRA_APP_CLIENT_ID_SECRET_NAME=<client-id-secret-name> \
ENTRA_APP_CLIENT_SECRET_SECRET_NAME=<client-secret-secret-name> \
./scripts/deploy/deploy-frontend.sh
```

- 上記 Key Vault 指定時、`AZURE_TENANT_ID` も同時に SWA app settings に反映されます。

## Backend (Azure Functions)

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-backend.sh
```

- `backend/` をビルドし、production dependencies のみ残した zip を作成して
  `az functionapp deployment source config-zip` で反映します。

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

- `main-config` / `main-bootstrap` の outputs から、自動作成した Entra アプリ登録を検出できた場合は先に削除します。
- 既存の手動管理 Entra アプリを残したい場合は `DELETE_ENTRA_APP=false` を付けてください。
