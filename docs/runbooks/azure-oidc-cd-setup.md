# 常設 dev の CD（GitHub Actions + Azure OIDC）の設定と運用

## Trigger

`deploy.yml` を初めて使うとき、または常設 dev の運用（main マージの自動デプロイ / 手動 up / 一時的な down）を行うとき。

> 方針は [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md)（常設・待機ほぼ ¥0 + main マージ自動デプロイ）。
> [ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md) のオンデマンド teardown は supersede 済みで、
> **夜間の自動 teardown は廃止**。OIDC 認証・`provision.sh` / `cleanup-env.sh` の機構は引き続き使う。

## Prerequisites

- 対象サブスクリプションの**所有者/管理者相当**（ロール付与と Entra アプリ作成のため。初回設定のみ）
- このセッションから Azure を触る準備（[claude-web-azure-access.md](./claude-web-azure-access.md) = device-code）
- GitHub リポジトリの **Settings → Variables** を編集できる権限
- CD（Actions）経由なら追加ツール不要。**device-code セッションから `provision.sh` を直接叩く場合**は
  `az` / `node`(npm) / `pnpm` / `zip` / `curl` に加え **SWA CLI** が必要:
  `npm i -g @azure/static-web-apps-cli`

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

- 所要時間: **初回 ~15〜30 分**（IaC + Container Apps 反映 + BFF/SWA デプロイ）。image は ghcr の事前ビルド済み（#67）を差し替えるだけなので、デプロイ経路でのイメージビルドは無い。
- 完了後、ジョブログの `deploy-frontend` 出力に SWA の URL が出る。スマホからはそれを開く。

### 4. 自動デプロイを解禁する（常設運用の本番運転）

main マージで常設 dev に自動反映させるには、リポジトリ **Settings → Variables** に `AUTO_DEPLOY_ENABLED=true` を設定する。

> **先に認可を設定すること。** 未設定のまま解禁すると、認可の無いアプリが公開 URL に自動で出続ける。
> 手順: [entra-spa-auth-and-budget.md](./entra-spa-auth-and-budget.md)（Entra SPA 登録 → `applyFunctionAuthLockdown=true` → 未認証 401 の実測）。
> 変数が未設定の間、main への push は `notice` を出して**何もせず skip** する（安全側の既定）。

### 5. 撤収（down / ¥0 化）— 一時的に畳みたいとき

**Actions → "deploy" → Run workflow** → `action: down`（または私に「down して」）。
`cleanup-env.sh` が RG 削除 + soft-delete 残骸（Key Vault / OpenAI / Log Analytics）まで purge。

> 常設運用では通常使わない（待機コストは SQL / ACR / SWA Standard を外してほぼ ¥0）。
> 消し忘れ対策の夜間 teardown は廃止し、代わりに**予算アラート**（#69）で支出を見張る。

## read-only の識別を足す (#209 / ops-inspect 用)

**なぜ**: 上のデプロイ用 SP は `main` 限定のフェデレーション資格情報しか持たない。
そのため **Azure を「読む」だけの確認にも main へのマージが必要**だった
(2026-08-10 の 1 セッションで 6 往復)。読むだけの用途を、書き込み権を持つ SP から切り離す。

**ワイルドカードは使えない**。標準のフェデレーション資格情報はサブジェクトの完全一致なので、
`claude/*` のような指定は通らない。そこで **`ops/inspect` という専用ブランチ 1 本に寄せる**
(エージェントは調査のたびにこのブランチへ push して dispatch する)。

### 手順

1. **アプリ登録を作る** — 例 `gha-oidc-readonly-mind-inbox`。リダイレクト URI は空
2. **フェデレーション資格情報を 1 本**追加する
   - シナリオ: 「GitHub Actions が Azure リソースをデプロイする」
   - 組織 `yomote` / リポジトリ `mind-inbox`
   - エンティティ型: **ブランチ** / ブランチ名: `ops/inspect`
   - 生成されるサブジェクト: `repo:yomote/mind-inbox:ref:refs/heads/ops/inspect`
3. **ロールを 3 つ**割り当てる (サブスクリプション スコープ / **書き込み系は付けない**)
   - 閲覧者 / Cost Management 閲覧者 / Log Analytics 閲覧者
4. GitHub の **Actions Variables** に `AZURE_CLIENT_ID_RO` = アプリ登録のクライアント ID

テナントとサブスクリプションは既存の `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` を流用する。

上の 1〜4 は `ro.sh` が冪等にやる。Azure Cloud Shell (Bash) から、**必ず commit sha で固定して**実行する:

```bash
bash <(curl -sL https://raw.githubusercontent.com/yomote/mind-inbox/<commit-sha>/ro.sh)
```

sha は GitHub の `ro.sh` のページで `y` を押す (Copy permalink) と URL に入る。

> **ブランチ名 (`ops/inspect`) で取ってはいけない。** このスクリプトは PO 自身の Azure 権限
> ([ADR 0006](../adr/0006-azure-access-via-device-code.md) の device-code ログイン) の下で走る一方、
> `ops/inspect` は調査のたびに直 push する運用のため**ブランチ保護が無い**。ブランチ名で
> always-latest を取ると、このブランチに push できる者が中身を差し替えた瞬間、次の実行で
> read-only の枠を超えて PO の全権限で任意コマンドが走る。

### この構成のトレードオフ ([ADR 0047](../adr/0047-readonly-investigation-identity-on-unprotected-branch.md) — Proposed)

`ops/inspect` に保護を掛けると「調査のたびに直 push して dispatch する」という用途が成立しないため、
**保護なしのまま受容している**。つまり**このリポジトリに push できる主体は、read-only の Azure
資格情報 (Reader / Cost Management Reader / Log Analytics Reader) を実質的に取得できる**。
Log Analytics には相談ログや例外詳細が載りうるので、「read-only だから無害」ではない。

受容の条件は ADR 0047 に 4 つ書いてある (書き込みロールを足さない / workflow は読むだけ /
`ro.sh` は sha 固定 / **共同作業者が増えたら再判断**)。条件を外すときは ADR を先に改訂すること。

### どう使われるか

`ops-inspect.yml` の guard が ref を見て使い分ける。

| ref | ログインに使う識別 |
| --- | --- |
| `main` | 従来のデプロイ SP (`AZURE_CLIENT_ID`) |
| それ以外 | **read-only ID** (`AZURE_CLIENT_ID_RO`) |

### 動作検証

`ops/inspect` ブランチから `ops-inspect` を dispatch し、`check: azure-resources` が
🟢 になること。ログの guard ステップに `ログインに使う識別: read-only ID (ops/inspect)`
が出ていれば、read-only 側で通っている。

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

> **マージ前のブランチで実環境を検証することはできない** (2026-08-08 に実際に踏んだ / #150)
>
> `workflow_dispatch` は `ref` にブランチを指定するとそのブランチ版の workflow ファイルで走るため、
> 「マージせずに実環境へテストを当てられる」と考えたくなるが、**Azure login がここで落ちる**。
> subject が `refs/heads/<ブランチ名>` になり、`main` 用の credential と一致しないため。
>
> 結果として、**実環境に対する検証 (golden-path / live E2E) はマージ後にしか実行できない**。
> 「実環境で確かめてからマージする」は成立しないので、実環境の挙動に依存する修正は
> 「マージ → 自動デプロイ → 実測」の順になることを前提に計画すること。
> どうしてもマージ前に実行したい場合は、そのブランチ分の federated credential を追加する
> (Azure 側の設定変更 = 人間の作業)。

### `AuthorizationFailed`（デプロイ中に権限エラー）

- 原因: SP のロール不足。RG 作成/削除や Key Vault purge にはサブスクリプションスコープの権限が要る。
- 対処: `setup-oidc.sh` の `ROLE=Contributor`（既定）でサブスクリプションに付与されているか `az role assignment list --assignee <AZURE_CLIENT_ID>` で確認。

### main にマージしたのにデプロイされない

- 原因: `AUTO_DEPLOY_ENABLED` が未設定（安全側の既定）。または OIDC の 3 変数が未登録。
- 確認: Actions の該当 run に `自動デプロイは未解禁のため skip しました` の notice が出ているか。
- 対処: 認可の設定（[entra-spa-auth-and-budget.md](./entra-spa-auth-and-budget.md)）を終えてから `AUTO_DEPLOY_ENABLED=true` を登録する。

### 連続でマージしたときのデプロイ順

- 同一 concurrency group + `cancel-in-progress: false` で直列化される。走行中のデプロイは中断されず、後続はキューイングされて順に流れる（中途半端な状態を残さないため意図的にこの設定）。
- GitHub Actions の仕様上、**pending は最新 1 件のみ保持**されるため、短時間に何度もマージすると中間のコミットはデプロイをスキップして最新だけが反映される。常設 dev の用途では問題にならない。

### up が遅い

- image は ghcr に事前ビルド済み（#67）なので、デプロイ経路でのイメージビルドは無い。撤収しても ghcr の image は残るため、再 up でビルドし直す必要はない（`deploy-*.sh` は ghcr のタグ差し替えのみ）。
- 詳細: [ghcr images runbook](./ghcr-images.md)

## Related

- ADR: [0009 オンデマンド CD](../adr/0009-on-demand-cd-via-github-actions-oidc.md) / [0006 device-code](../adr/0006-azure-access-via-device-code.md)
- ワークフロー: `.github/workflows/deploy.yml`
- スクリプト: `cicd/scripts/cloud-env/setup-oidc.sh` / `cicd/scripts/deploy/provision.sh` / `cicd/scripts/env/cleanup-env.sh`
- 関連 Runbook: [claude-web-azure-access.md](./claude-web-azure-access.md) / [local-fullstack-dev.md](./local-fullstack-dev.md)
- IaC 手順: [`cicd/iac/README.md`](../../cicd/iac/README.md)
