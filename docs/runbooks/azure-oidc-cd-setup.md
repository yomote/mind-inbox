# オンデマンド CD（GitHub Actions + Azure OIDC）の設定と運用

## Trigger

`deploy.yml`（オンデマンド CD / [ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md)）を初めて使うとき、
または「使う時だけ環境を立てて（up）、終わったら ¥0 に戻す（down）」を運用するとき。

## Prerequisites

- 対象サブスクリプションの**所有者/管理者相当**（ロール付与と Entra アプリ作成のため。初回設定のみ）
- このセッションから Azure を触る準備（[claude-web-azure-access.md](./claude-web-azure-access.md) = device-code）
- GitHub リポジトリの **Settings → Variables** を編集できる権限

## Steps

### 1. 一度きり: OIDC 連携を作る（device-code セッションで）

```bash
az login --use-device-code
az account set --subscription "<subscription-name-or-id>"
REPO=yomote/mind-inbox ./cicd/scripts/cloud-env/setup-oidc.sh
```

スクリプトが Entra アプリ + federated credential + ロール付与（Contributor / サブスクリプションスコープ）を作り、
最後に **3 つの ID** を出力する。

### 2. 一度きり: GitHub に Variables を登録

リポジトリ **Settings → Secrets and variables → Actions → Variables** タブに（Secrets ではなく Variables）:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

> OIDC なのでクライアントシークレットは保存しない（[ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md)）。

### 3. 立ち上げ（up）

GitHub → **Actions → "deploy" → Run workflow** → `action: up` / `environment: dev`。
（または `gh workflow run deploy.yml -f action=up -f environment=dev`、あるいはセッションの私に「up して」と依頼）

- 所要時間: **初回 ~20〜40 分**（IaC + `az acr build` でイメージビルド + Container Apps 反映 + BFF/SWA デプロイ）。
- 完了後、ジョブログの `deploy-frontend` 出力に SWA の URL が出る。スマホからはそれを開く。

### 4. 撤収（down / ¥0 化）

**Actions → "deploy" → Run workflow** → `action: down`（または私に「down して」）。
`cleanup-env.sh` が RG 削除 + soft-delete 残骸（Key Vault / OpenAI / Log Analytics）まで purge。

> 夜間 schedule（JST 04:07）が自動で `down` を流すため、消し忘れても翌朝 ¥0 に戻る。

## Verification

- [ ] `setup-oidc.sh` が 3 つの ID を出力し、GitHub Variables に登録済み
- [ ] Actions の "deploy" 実行で `Azure login (OIDC)` ステップが成功（緑）
- [ ] up 後: `az resource list -g rg-dev-mind-inbox -o table` にリソースが並ぶ / SWA URL がスマホで開ける
- [ ] down 後: `az group show -n rg-dev-mind-inbox` が `NotFound`
- [ ] コスト: `./cicd/scripts/cost/show-cost.sh` で当月コストを確認

## Rollback

- up が途中失敗 → `action: down` で部分作成分ごと撤収してから再 up
- OIDC をやめる → GitHub Variables を削除し、`az ad app delete --id <AZURE_CLIENT_ID>`、ロール割当を削除

## Common Issues

### `Azure login (OIDC)` が失敗する（AADSTS700213 / no matching federated credential）

- 原因: federated credential の subject 不一致。`deploy.yml` は既定ブランチ `main` の ref で動くため、subject は `repo:yomote/mind-inbox:ref:refs/heads/main`。
- 対処: `setup-oidc.sh` を `BRANCH=main` で実行（既定）。別ブランチから動かすならそのブランチ分の credential を追加。

### `AuthorizationFailed`（デプロイ中に権限エラー）

- 原因: SP のロール不足。RG 作成/削除や Key Vault purge にはサブスクリプションスコープの権限が要る。
- 対処: `setup-oidc.sh` の `ROLE=Contributor`（既定）でサブスクリプションに付与されているか `az role assignment list --assignee <AZURE_CLIENT_ID>` で確認。

### 夜間に消えて困る（長時間使いたい）

- 原因: schedule teardown は毎日 `down` する設計。
- 対処: その日は使い終わりに手動運用で再 `up`、または一時的に `deploy.yml` の `schedule` をコメントアウト（使い終わったら戻す）。

### up が遅い / 毎回イメージビルドで時間がかかる

- 原因: 撤収で ACR ごと消えるため、再 up でイメージを `az acr build` し直す。
- 対処（任意の最適化）: ACR とイメージだけ別の永続 RG に分離して残す（少額の常時コストと引き換えに再 up を短縮）。将来検討。

## Related

- ADR: [0009 オンデマンド CD](../adr/0009-on-demand-cd-via-github-actions-oidc.md) / [0006 device-code](../adr/0006-azure-access-via-device-code.md)
- ワークフロー: `.github/workflows/deploy.yml`
- スクリプト: `cicd/scripts/cloud-env/setup-oidc.sh` / `cicd/scripts/deploy/provision.sh` / `cicd/scripts/env/cleanup-env.sh`
- 関連 Runbook: [claude-web-azure-access.md](./claude-web-azure-access.md) / [local-fullstack-dev.md](./local-fullstack-dev.md)
- IaC 手順: [`cicd/iac/README.md`](../../cicd/iac/README.md)
