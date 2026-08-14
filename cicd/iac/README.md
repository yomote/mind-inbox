# Infra 運用手順（Bootstrap / Config 分離）

このディレクトリでは、次のエントリで Azure インフラを管理します。

- [main-mgmt.bicep](main-mgmt.bicep): **管理系**（運用のためのもの / 別 RG に一度きり）
- [main-bootstrap.bicep](main-bootstrap.bicep): 初回構築と基盤更新（**アプリ系**）
- [main-config.bicep](main-config.bicep): 認証など後追い設定

対象スコープはいずれも resource group です。**適用順は mgmt → bootstrap → config**
（[ADR 0003](../../docs/adr/0003-two-phase-bicep.md) の 2 フェーズはアプリ系の中の話で、
その前段に管理系が 1 つ増えた形。順序を入れ替えない）。

## 目次

- [Infra 運用手順（Bootstrap / Config 分離）](#infra-運用手順bootstrap--config-分離)
  - [目次](#目次)
  - [0. 最短ルート（初回）](#0-最短ルート初回)
  - [1. 前提](#1-前提)
  - [1-5. 管理系レイヤ（rg-mgmt-mindbox / 一度きり）](#1-5-管理系レイヤrg-mgmt-mindbox--一度きり)
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
  - [8. bicep で管理していないもの（宣言の外にある設定）](#8-bicep-で管理していないもの宣言の外にある設定)
  - [9. 関連手順](#9-関連手順)

---

## 0. 最短ルート（初回）

まずはこれだけで環境を起動できます。

```bash
# 1) 前提
az login
az account set --subscription "<subscription-name-or-id>"
az bicep version

# 2) RG 作成
az group create -n <rg-name> -l <location>

# 3) インフラ作成（VOICEVOX を同時に作る場合）
cd cicd/iac
az deployment group create \
  -g <rg-name> \
  -n main-bootstrap \
  -f main-bootstrap.bicep \
  -p @main-bootstrap.parameters.json \
  -p appName='mind-box' environmentName='dev' \
  -p enableVoicevoxAca=true \
  -p voicevoxLocation='japaneast'

# 4) アプリ成果物反映（frontend + backend）
cd ..
RG=<rg-name> DEPLOYMENT=main-bootstrap ./scripts/deploy/deploy-all.sh
```

注意:

- `deploy-all.sh` は成果物デプロイ専用で、IaC は実行しません。
- Entra 認証を有効化する場合は後述の `main-config.bicep` を追加実行します。

---

## 1. 前提

- Azure CLI ログイン済み
- サブスクリプション選択済み
- Bicep 利用可能
- **既存の手動ロール割り当てが残っていないこと** — スクリプト時代（`az role assignment create`）に
  作られた ai-agent MI → Cognitive Services OpenAI User の割り当てが残っていると、bicep の
  宣言が `RoleAssignmentExists` で拒否され bootstrap ごと落ちる。あれば **1 回だけ手で削除**する
  （Issue #297 / 手順は [`scripts/deploy/README.md`](../scripts/deploy/README.md#前提条件-古い手動割り当てが残っていないこと-297)）。
  ロール割り当ての持ち主は bicep 1 本で、シェルからは作らない

```bash
az login
az account set --subscription "<subscription-name-or-id>"
az bicep version
```

---

## 1-5. 管理系レイヤ（rg-mgmt-mindbox / 一度きり）

**システムを運用するためのものを、アプリ系とは別の RG に置きます**
（[ADR 0056](../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 /
[#302](https://github.com/yomote/mind-inbox/issues/302)）。宣言は
[main-mgmt.bicep](main-mgmt.bicep)、値は [main-mgmt.parameters.json](main-mgmt.parameters.json)。

**分ける軸は「消えると困るか」ではなく「運用のためか / アプリそのものか」です。**

| 層             | RG                     | 中身                                                                                  | 撤収              |
| -------------- | ---------------------- | ------------------------------------------------------------------------------------- | ----------------- |
| **管理系**     | `rg-mgmt-mindbox`      | Key Vault（+ E2E trace 復号鍵）/ バックアップ Storage / Log Analytics / 予算          | ❌ **触らない**   |
| **アプリ系**   | `rg-dev-mind-inbox`    | Cosmos / OpenAI / Speech / Container Apps / Functions / SWA                           | ✅ 壊して作り直す |
| **デプロイ層** | （リソースを作らない） | image の sha 差し替え / zip deploy / 静的配信                                         | —                 |

**Cosmos / OpenAI / Speech はアプリ系に残します。** アプリそのものであって運用の道具ではないためです。
消えると困る Cosmos のデータは、**RG を移して守るのではなくバックアップで戻せるようにします** —
管理系 RG の非公開 Storage へ自前エクスポート（[ADR 0056](../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D2 / 経路の実装は [ADR 0046](../../docs/adr/0046-environment-rebuildable-from-declaration.md) D9）。
これで「アプリ系 RG は使い捨て」を貫けます。**このリポジトリは public なので、ユーザーデータを
git データブランチには出しません**（[#302](https://github.com/yomote/mind-inbox/issues/302) コメント）。

**適用手順は [`docs/runbooks/mgmt-layer-apply.md`](../../docs/runbooks/mgmt-layer-apply.md)**（一度きりの手動オペ。管理系の RG は `provision.sh` / `cleanup-env.sh` のどちらも触りません）。ここには構成と `enable*` の考え方だけを置きます。

### 段階適用（`enable*` の既定がなぜこうなっているか）

**既定で作るのは「まだどこにも無いもの」だけ**（Key Vault + 鍵 + バックアップ Storage）。
`enableLogAnalytics` は**既定 `false`** です — ops workspace が現在 `rg-dev-mind-inbox` に
実在しており、移行が済むまで二重に作らないためです。
**RG 間の移動そのものは本テンプレートの担当ではありません**（[#302](https://github.com/yomote/mind-inbox/issues/302) の「段階的に切り出す」）。

### E2E trace の復号鍵（[#301](https://github.com/yomote/mind-inbox/issues/301) / ADR 0045 D5）

Key Vault に **secret ではなく「鍵オブジェクト」を非エクスポートで**宣言しています
（`attributes.exportable: false`）。復号は `az keyvault key decrypt` で **Key Vault の中**で
行い、秘密鍵は一度も外に出ません。鍵を使う人には
**Key Vault Crypto User** だけを与えます（`keyVaultCryptoUserPrincipalIds` に object ID を渡す）。
**封筒暗号のスクリプト実装は別 PR**です（ここは器だけ）。

鍵の URI（kid）の取り出し方は [runbook](../../docs/runbooks/mgmt-layer-apply.md#steps) にあります（output `e2eTraceKeyUri`）。

### 撤収との関係

`cleanup-env.sh` は**管理系 RG を削除できません**（どのフラグでも通りません）。
またアプリ系 RG の中に管理系のリソースが残っている間も、**何も消さずに拒否**します。
詳細は [`../scripts/env/README.md`](../scripts/env/README.md#層ガード--何も消さずに拒否する条件-adr-0056--302)。

このテンプレートが作るリソースには**層タグ `mindInboxLayer=management`（`managementLayerTags`）が刻まれ**、撤収ガードはそれを見て層を判定します。Key Vault / Storage / Log Analytics はアプリ系にも同じ型が居るため、**型ではなくタグが根拠**です。ここにリソースを足すときは `tags: managementLayerTags` も付けてください — 付け忘れると、そのリソースはアプリ系と見なされて撤収で消えます。

**Cosmos が居るアプリ系 RG の撤収も、当面は拒否されます**（判定コード `data-restore-unproven`）。これは「Cosmos の置き場所が間違っている」という意味ではなく、**バックアップからの復元をまだ 1 回も通していない**ための暫定措置です（[ADR 0056](../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D3 / [ADR 0018](../../docs/adr/archive/operations/runtime-verification-in-the-loop.md)）。復元を通したら、この一律拒否はバックアップ鮮度の確認に差し替えます（[runbook](../../docs/runbooks/mgmt-layer-apply.md#撤収ガードとの関係-いま暫定なのはどこか)）。

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

この Bicep はインフラ作成までです。frontend/backend の成果物反映は別手順です。

- [../scripts/deploy/README.md](../scripts/deploy/README.md)

ワンショットで成果物まで反映する場合:

```bash
cd cicd
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

---

## 6. 削除

### A. 環境ごと削除（推奨）

```bash
cd cicd
RG=<rg-name> ./scripts/env/cleanup-env.sh
```

- **管理系 RG (`rg-mgmt-mindbox`) は削除できない**（どのフラグでも通らない）
- **アプリ系 RG に管理系のリソースが残っている間は、何も消さずに拒否する**（[#302](https://github.com/yomote/mind-inbox/issues/302)）
- **Cosmos が居る RG も当面は拒否する**（復元を 1 回通すまでの暫定 / ADR 0056 D3）
- RG を削除する
- **soft-delete の purge は既定で行わない**（Key Vault / Cognitive Services の救済を残す）
- **Entra アプリ登録も既定で削除しない**（テナントのオブジェクトであり RG の持ち物ではない）
- 手動指定した既存 Entra アプリと共有 UAMI は削除しない

破壊系の既定が off である理由は [ADR 0046](../../docs/adr/0046-environment-rebuildable-from-declaration.md) D5/D6、
層ガードは [ADR 0056](../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md)（判定は [`../scripts/env/persistent_layer_guard.py`](../scripts/env/persistent_layer_guard.py)）。

オプション:

```bash
# 再 provision が名前衝突で失敗したときの手当て。
# ★ 衝突した種類のフラグ「だけ」を立てる — 巻き添えで purge すると、
#   衝突していない種類の soft-delete による救済まで永久に失う
cd cicd

# OpenAI / Speech (Cognitive Services) が衝突した場合
RG=<rg-name> PURGE_DELETED_COGNITIVE_SERVICES=true ./scripts/env/cleanup-env.sh

# Key Vault が衝突した場合
RG=<rg-name> PURGE_DELETED_KEYVAULTS=true ./scripts/env/cleanup-env.sh
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

## 8. bicep で管理していないもの（宣言の外にある設定）

**「消えると困る設定」は、正確には「宣言されていない設定」**です。ここには **bicep の外にある設定を全部**列挙します。**新しく宣言の外に設定を作ったら、必ずここに 1 行足すこと。**

> 方針として、**設定は bicep に一本化する**のが目標です（[#303](https://github.com/yomote/mind-inbox/issues/303)）。下の「デプロイスクリプトが設定するもの」と「Entra のアプリ登録」は**将来 bicep へ移す対象**で、恒久的な例外ではありません。**恒久的な例外は「GitHub 側の設定」だけ**です (Azure ではないため bicep の管轄外)。

### 8-1. GitHub Actions Variables（恒久的な例外 — Azure ではない）

**欠けると deploy はガードで静かに skip します**（run は成功のまま）。「成功 run = デプロイ済み」ではない原因がこれです。

| 変数                                                            | 用途                                                                                                       | 欠けるとどうなるか                                                     |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` | OIDC ログイン（[ADR 0009](../../docs/adr/0009-on-demand-cd-via-github-actions-oidc.md)）                   | `Guard — OIDC 設定済みか` が false → **全ステップ skip（run は成功）** |
| `AUTO_DEPLOY_ENABLED`                                           | push での自動デプロイ解禁（[ADR 0013](../../docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md)） | `true` 以外だと push デプロイを skip（run は成功）                     |
| `REVIEW_GATE_REQUIRE_CODEX`                                     | コード PR に Codex レビューを必須にするか                                                                  | 門の判定が緩む                                                         |
| `CODEX_RETRIGGER_MINUTES`                                       | Codex 再依頼の間隔                                                                                         | 既定値で動く                                                           |

**確認する**（設定画面: `Settings > Secrets and variables > Actions > Variables`）:

```bash
# 一覧（gh CLI がある環境で）
gh variable list -R yomote/mind-inbox

# workflow がどれを使っているか（リポジトリ側から逆引き）
grep -rhoE 'vars\.[A-Z_]+' .github/workflows/*.yml | sort -u
```

**設定する**: `gh variable set AUTO_DEPLOY_ENABLED --body true -R yomote/mind-inbox`

### 8-2. GitHub Actions Secrets

**自前で登録した秘密はゼロ**です。使っているのは GitHub が自動発行する `GITHUB_TOKEN` のみ（[ADR 0009](../../docs/adr/0009-on-demand-cd-via-github-actions-oidc.md) の "no stored secret" / [ADR 0031](../../docs/adr/archive/operations/agent-reaches-outside-via-github-actions.md)）。

```bash
gh secret list -R yomote/mind-inbox   # GITHUB_TOKEN は表示されない（自動発行のため）
```

**ここに秘密を足す前に ADR を書くこと。** 現状ゼロであること自体が設計判断です。

### 8-3. Entra のアプリ登録（**恒久的な例外ではない** — Graph Bicep で宣言できる）

> **2026-08-12 訂正**: 当初この節は「Entra は ARM リソースではないので bicep で宣言できない = 恒久的な例外」と書いていたが、**誤り**。[Microsoft Graph Bicep 拡張](https://learn.microsoft.com/en-us/graph/templates/bicep/overview-bicep-templates-for-graph)は **GA** しており、`Microsoft.Graph/applications` などを **Azure リソースと同じテンプレートに**書ける。

現状は `main-config.bicep` が `Microsoft.Resources/deploymentScripts`（宣言の中に命令を埋める形）で処理している。**これは移行対象**（[#303](https://github.com/yomote/mind-inbox/issues/303)）。

移行前に確認すべき制約（[一次ソース](https://learn.microsoft.com/en-us/graph/templates/bicep/limitations)）:

| 制約                                                                           | このリポジトリへの影響                                                                                                                              |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **アプリのパスワード（`passwordCredentials`）が非対応**。`keyCredentials` のみ | **ここが分かれ目。** クライアントシークレットが要る構成なら `DeploymentScript` が残る。SPA + フェデレーション資格情報 (OIDC) で済むなら宣言化できる |
| **what-if が使えない**（拡張リソース全般）                                     | [2-1. 事前確認](#2-1-事前確認build--what-if) の網から Graph 部分が外れる。事前確認の手順を見直す必要がある                                          |
| role-assignable group が非対応                                                 | 該当なし                                                                                                                                            |
| Deployment stacks 非対応                                                       | 該当なし                                                                                                                                            |

- 現行の手順: [3. Entra 認証を有効化する](#3-entra-認証を有効化する) / [Runbook](../../docs/runbooks/entra-spa-auth-and-budget.md)
- 確認: `az ad app list --display-name <app-name>` / Functions 側は `ops-inspect` の `azure-resources`（EasyAuth の実測値が出る）
- **RG を消しても Entra のアプリ登録は消えません**（テナントに属するため）。逆に言うと、環境を作り直したときに**古いアプリ登録が残って混乱する**ことがあります

### 8-4. デプロイスクリプトが設定するもの（**将来 bicep へ移す** / [#303](https://github.com/yomote/mind-inbox/issues/303)）

**現状ここが宣言の外にあるため、bicep から環境を作り直しても、デプロイが走るまで設定が入りません。**

| スクリプト                                       | 何を設定しているか                                                     | 持ち主                     |
| ------------------------------------------------ | ---------------------------------------------------------------------- | -------------------------- |
| `cicd/scripts/deploy/deploy-ai-agent.sh`         | Container App の環境変数（`--set-env-vars`）                           | **シェルのみ**（宣言の外） |
| `cicd/scripts/deploy/deploy-voicevox-wrapper.sh` | Container App の環境変数                                               | **シェルのみ**（宣言の外） |
| `cicd/scripts/deploy/deploy-backend.sh`          | Function App の appsettings（`az functionapp config appsettings set`） | **シェルのみ**（宣言の外） |

> **⚠️ 認証ゲート（[ADR 0017](../../docs/adr/0017-container-apps-access-via-auth-gate.md)）はこの表に入りません — 宣言の外ではなく「二重管理」です。**
> `bootstrap-core.bicep:1133-1187` の `aiAgentAuthConfig` / `voicevoxWrapperAuthConfig` が
> `containerAppsGateClientId` 非空のとき同じ `current` authConfig を**既に宣言しており**、
> かつ `deploy-ai-agent.sh` / `deploy-voicevox-wrapper.sh` も
> `az containerapp auth ... update` で設定しています。
> つまり「デプロイが走るまで設定が入らない」のではなく、**どちらが勝つかが曖昧**な状態です。
> 守るべき資源（OpenAI の課金）に直結する門なので、持ち主の一本化は [#303](https://github.com/yomote/mind-inbox/issues/303) の対象。
> **本 PR のスコープ外**（本 PR はロール割り当ての一本化のみ）。
>
> ---
>
> **ロール割り当てもこの表に載りません（本 PR で撤去済み）。** 以前は `deploy-ai-agent.sh` が
> `az role assignment create` で OpenAI User を付与し、`provision.sh` が
> `manageAiAgentOpenAiRoleAssignment=false` という「bicep に宣言させない逃げ道」を渡していました。
> **どちらも削除済み**で、**持ち主は bicep 1 本**です（`bootstrap-core.bicep` の
> `aiAgentOpenAiRoleAssignment` が `guid()` の決定的名で宣言する）。
> **この表を見てシェル側に付与を足し戻さないでください** — それが #262 で dev を 9 回落とした
> 二重宣言の再導入になります。前提は [1. 前提](#1-前提)（古い手動割り当てが残っていないこと）。

**確認する**（実環境の実際の値を読む）:

```bash
# エージェントから: ADR 0031 の経路（Actions 経由・read-only）
#   → ops-inspect workflow を check=azure-resources で dispatch
# 手元から: ADR 0006 の device-code
az login --use-device-code
az containerapp show -g rg-dev-mind-inbox -n ca-dev-mindbox-ai-agent \
  --query "properties.template.containers[0].env" -o table
az functionapp config appsettings list -g rg-dev-mind-inbox -n func-dev-mindbox -o table
az role assignment list --scope <openai-scope> -o table
```

### 8-5. 宣言の外にあるものを増やしたら

1. **この節に 1 行足す**（何を・どこで確認・どう設定するか）
2. 恒久的な例外にするなら **ADR に理由を書く**
3. 一時的なものなら **Issue を立てて期限を切る**

---

## 9. 関連手順

- Entra ユーザー登録（CSV一括）: [../../operation/automation/identity/README.md](../../operation/automation/identity/README.md)
- ローカル音声合成（VOICEVOX）: [../scripts/local-voicevox/README.md](../scripts/local-voicevox/README.md)
- Azure Container Apps で VOICEVOX（Serverless GPU）: [../scripts/aca-voicevox/README.md](../scripts/aca-voicevox/README.md)

---
