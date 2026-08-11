# Infra 運用手順（Bootstrap / Config 分離）

このディレクトリでは、次の 2 つのエントリで Azure インフラを管理します。

- [main-bootstrap.bicep](main-bootstrap.bicep): 初回構築と基盤更新
- [main-config.bicep](main-config.bicep): 認証など後追い設定

対象スコープは resource group です。

## 目次

- [Infra 運用手順（Bootstrap / Config 分離）](#infra-運用手順bootstrap--config-分離)
  - [目次](#目次)
  - [0. 最短ルート（初回）](#0-最短ルート初回)
  - [1. 前提](#1-前提)
  - [2. Bootstrap（基盤作成 / 更新）](#2-bootstrap基盤作成--更新)
    - [命名規則（既定値）](#命名規則既定値)
    - [2-1. 事前確認（build + what-if）](#2-1-事前確認build--what-if)
    - [2-2. デプロイ](#2-2-デプロイ)
  - [3. Entra 認証を有効化する](#3-entra-認証を有効化する)
    - [3-1. UAMI を事前準備（自動アプリ登録する場合のみ）](#3-1-uami-を事前準備自動アプリ登録する場合のみ)
    - [3-2. main-config を実行](#3-2-main-config-を実行)
  - [4. アプリ成果物の反映](#4-アプリ成果物の反映)
  - [5. 更新（差分デプロイ）](#5-更新差分デプロイ)
  - [6. 削除](#6-削除)
    - [A. 環境ごと削除（推奨）](#a-環境ごと削除推奨)
    - [B. Complete モードで整理（要注意）](#b-complete-モードで整理要注意)
  - [7. よく使う確認コマンド](#7-よく使う確認コマンド)
  - [8. 関連手順](#8-関連手順)

---

## 0. 最短ルート（初回）

初回構築は `provision.sh` (up) に一本化しています。RG 作成 → bootstrap (IaC) →
コンテナ (voicevox-wrapper / ai-agent — **MI へのロール付与を含む**) → BFF / frontend を
正しい順序で一括実行します (CD の deploy.yml もこの経路)。

```bash
# 1) 前提 (成果物デプロイまで含むため az に加えて npm / pnpm / zip / curl / swa が必要。
#    不足があれば provision.sh が冒頭でまとめて検査して止まる)
az login
az account set --subscription "<subscription-name-or-id>"
az bicep version

# 2) まとめて構築 (既定: RG=rg-dev-mind-inbox / LOCATION=japaneast / ENVIRONMENT=dev。
#    有効化フラグ (VOICEVOX / OpenAI / Cosmos 等) は main-bootstrap.parameters.json が持つ)
RG=<rg-name> LOCATION=<location> ./cicd/scripts/deploy/provision.sh
```

注意:

- **bicep 単体 + `deploy-all.sh` では初回構築は完結しない** (#262 / PR #279 P1)。
  `deploy-all.sh` は BFF + frontend の成果物デプロイ専用で、ai-agent MI への
  Cognitive Services OpenAI User ロール付与を含まない。付与の唯一の所有者は
  `deploy-ai-agent.sh` (bicep には宣言しない — 理由は
  [`../modules/bootstrap-core.bicep`](../modules/bootstrap-core.bicep) のコメント参照)。
  この経路を飛ばすと ai-agent の LLM 呼び出しが認可エラーになる。
- 手動で分割実行したい場合も、bootstrap の後に必ず `deploy-voicevox-wrapper.sh` →
  `deploy-ai-agent.sh` → `deploy-all.sh` の順で実行すること（[§4](#4-アプリ成果物の反映)）。
- Entra 認証を有効化する場合は後述の `main-config.bicep` を追加実行します。

---

## 1. 前提

- Azure CLI ログイン済み
- サブスクリプション選択済み
- Bicep 利用可能

```bash
az login
az account set --subscription "<subscription-name-or-id>"
az bicep version
```

---

## 2. Bootstrap（基盤作成 / 更新）

### 命名規則（既定値）

`appName`（既定: `mind-box`）と `environmentName`（`dev`/`stg`/`prod`）から
リソース名を自動生成します。

例（`appName=mind-box`, `environmentName=dev`）:

- SWA: `swa-dev-mindbox`
- Function App: `func-dev-mindbox`
- Storage: `stdevmindboxfunc`
- Function Plan: `asp-dev-mindbox-func`
- SQL Server: `sql-dev-mindbox`
- SQL Database: `sqldb-dev-mindbox`
- Key Vault: `kv-dev-mindbox-sql`
- Log Analytics: `law-dev-mindbox-ops`

### 2-1. 事前確認（build + what-if）

```bash
cd cicd/iac
az bicep build --file main-bootstrap.bicep
az deployment group what-if \
  -g <rg-name> \
  -n main-bootstrap \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json
```

### 2-2. デプロイ

```bash
az deployment group create \
  -g <rg-name> \
  -n main-bootstrap \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json
```

VOICEVOX (ACA Serverless GPU) も同時に作る場合:

```bash
az deployment group create \
  -g <rg-name> \
  -n main-bootstrap \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json \
  -p appName='mind-box' environmentName='dev' \
  -p enableVoicevoxAca=true \
  -p voicevoxLocation='japaneast'
```

クォータ関連の注意:

- `SubscriptionIsOverQuotaForSku` が出る場合は `functionPlanSkuName='Y1'` を指定
- `Dynamic VMs` クォータ不足が出る場合は `functionLocation='eastasia'` など別リージョンを指定

---

## 3. Entra 認証を有効化する

Static Web Apps の Entra ID 認証を `main-config.bicep` で後追い反映できます。

### 3-1. UAMI を事前準備（自動アプリ登録する場合のみ）

`autoCreateStaticSiteEntraAppRegistration=true` を使う場合は、
環境外に共有の User Assigned Managed Identity (UAMI) を事前に作成します。

```bash
# 例: 共有 identity 用 Resource Group
az group create -n rg-platform-identity -l japaneast

# UAMI 作成
az identity create \
  -g rg-platform-identity \
  -n uami-entra-app-bootstrap

# 識別子の取得
IDENTITY_RESOURCE_ID="$(az identity show \
  -g rg-platform-identity \
  -n uami-entra-app-bootstrap \
  --query id -o tsv)"

IDENTITY_CLIENT_ID="$(az identity show \
  -g rg-platform-identity \
  -n uami-entra-app-bootstrap \
  --query clientId -o tsv)"
```

この UAMI の service principal には、テナント管理者が以下のロールを付与してください。

- `Application Administrator`

### 3-2. main-config を実行

```bash
az deployment group create \
  -g <rg-name> \
  -n main-config \
  -f main-config.bicep \
  -p @main-config.json \
  -p appName='mind-box' environmentName='dev' \
  -p enableStaticSiteEntraAuth=true \
  -p autoCreateStaticSiteEntraAppRegistration=true \
  -p staticSiteEntraAppDisplayName='app-dev-mindbox-swa' \
  -p staticSiteEntraBootstrapUserAssignedIdentityResourceId="$IDENTITY_RESOURCE_ID" \
  -p staticSiteEntraBootstrapUserAssignedIdentityClientId="$IDENTITY_CLIENT_ID"
```

既存の Entra アプリを使う場合は UAMI 不要です。

- `autoCreateStaticSiteEntraAppRegistration=false`
- `staticSiteEntraClientId=<existing-client-id>`
- `staticSiteEntraClientSecret=<existing-client-secret>`

---

## 4. アプリ成果物の反映

この Bicep はインフラ作成までです。frontend/backend/コンテナの成果物反映は別手順です。

- [../scripts/deploy/README.md](../scripts/deploy/README.md)

bootstrap 後に分割実行する場合の順序 (provision.sh の [3/5]〜[5/5] と同じ):

```bash
cd cicd
# コンテナ (BFF より先に。ai-agent は MI への OpenAI ロール付与を含む — §0 の注意参照)
RG=<rg-name> DEPLOYMENT=main-bootstrap IMAGE_TAG=sha-<full-sha> ./scripts/deploy/deploy-voicevox-wrapper.sh
RG=<rg-name> DEPLOYMENT=main-bootstrap IMAGE_TAG=sha-<full-sha> ./scripts/deploy/deploy-ai-agent.sh
# BFF + frontend
RG=<rg-name> DEPLOYMENT=main-bootstrap ./scripts/deploy/deploy-all.sh
```

---

## 5. 更新（差分デプロイ）

```bash
cd cicd/iac
az deployment group what-if \
  -g <rg-name> \
  -n main-bootstrap \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json

az deployment group create \
  -g <rg-name> \
  -n main-bootstrap \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json
```

必要に応じて `-p key=value` で上書きします。

bicep 単独適用は稼働 image を既定タグへ戻す等の副作用があるため
(`bootstrap-core.bicep` の `containerImageTag` コメント参照)、適用後は §0 の
`provision.sh` (または §4 の deploy スクリプト群) を続けて実行して収束させてください。

---

## 6. 削除

### A. 環境ごと削除（推奨）

```bash
cd cicd
RG=<rg-name> ./scripts/env/cleanup-env.sh
```

- 自動作成した Entra アプリ登録を検出できた場合は先に削除
- 既定で Key Vault の purge まで実行
- 手動指定した既存 Entra アプリと共有 UAMI は削除しない

オプション:

```bash
# Entra アプリを残す
cd cicd
RG=<rg-name> DELETE_ENTRA_APP=false ./scripts/env/cleanup-env.sh

# Key Vault purge を無効化
cd cicd
RG=<rg-name> PURGE_DELETED_KEYVAULTS=false ./scripts/env/cleanup-env.sh
```

### B. Complete モードで整理（要注意）

```bash
az deployment group create \
  -g <rg-name> \
  -n main-complete \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json \
  --mode Complete
```

`Complete` は破壊的です。必ず `what-if` で影響確認してから実行してください。

補足 (#262 / PR #279):

- ai-agent MI の OpenAI ロール (スクリプト管理 — §0 の注意参照) は Complete モードでも
  **削除されない**。`Microsoft.Authorization/roleAssignments` は complete mode deletion の
  対象外 ([公式の対象一覧](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/complete-mode-deletion)
  の Microsoft.Authorization 表で "No")。
- ただし bicep 単独適用 (このモードを含む) には稼働 image を既定タグへ戻す等の副作用がある
  (`bootstrap-core.bicep` の `containerImageTag` コメント参照)。適用後は §0 の `provision.sh`
  (または §4 の deploy スクリプト群) を続けて実行して収束させること。ロール付与も
  `deploy-ai-agent.sh` が冪等に再確認するため、万一 (手動削除等で) 消えていても
  この手順で復旧する。

---

## 7. よく使う確認コマンド

```bash
# デプロイ結果（outputs含む）
az deployment group show -g <rg-name> -n main-bootstrap -o jsonc
az deployment group show -g <rg-name> -n main-config -o jsonc

# Resource Group 内のリソース確認
az resource list -g <rg-name> -o table
```

`main-bootstrap` の outputs には、VOICEVOX を有効化した場合 `voicevoxBaseUrl` が出力されます。
frontend 側で `VITE_VOICEVOX_BASE_URL` に設定してください。

---

## 8. 関連手順

- Entra ユーザー登録（CSV一括）: [../../operation/automation/identity/README.md](../../operation/automation/identity/README.md)
- ローカル音声合成（VOICEVOX）: [../scripts/local-voicevox/README.md](../scripts/local-voicevox/README.md)
- Azure Container Apps で VOICEVOX（Serverless GPU）: [../scripts/aca-voicevox/README.md](../scripts/aca-voicevox/README.md)

---
