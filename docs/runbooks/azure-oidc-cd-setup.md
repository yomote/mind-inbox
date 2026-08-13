# 常設 dev の CD（GitHub Actions + Azure OIDC）の設定と運用

## Trigger

`deploy.yml` を初めて使うとき、常設 dev の運用（main マージの自動デプロイ / 手動 up / 一時的な down）を行うとき、
または **CD の権限を縮小するとき**（[#46](https://github.com/yomote/mind-inbox/issues/46)）。

> 方針は [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md)（常設・待機ほぼ ¥0 + main マージ自動デプロイ）。
> [ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md) のオンデマンド teardown は supersede 済みで、
> **夜間の自動 teardown は廃止**。OIDC 認証・`provision.sh` / `cleanup-env.sh` の機構は引き続き使う。

## 権限モデル（何が何をできるか）

CD の identity は **2 つ**。どちらも federated credential の subject は
`repo:yomote/mind-inbox:ref:refs/heads/main` で、**main 以外のブランチからは使えない**。

| identity                                | 使う workflow                                                               | ロール                                                                                        | スコープ                           |
| --------------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------- |
| `gha-oidc-mind-inbox-cd`（書き込み）    | `deploy.yml`                                                                | Contributor                                                                                   | **RG のみ**（`rg-dev-mind-inbox`） |
| 〃                                      | 〃                                                                          | RBAC Administrator（OpenAI User / Speech User しか付けられない ABAC 条件つき）                | **RG のみ**                        |
| `gha-oidc-mind-inbox-cd-ro`（読むだけ） | `ops-inspect.yml` / `refresh-infra-diagram.yml` / `golden-path-monitor.yml` | Reader                                                                                        | RG                                 |
| 〃                                      | 〃                                                                          | Cost Management Reader（課金データはサブスクリプション単位でしか読めない / **読み取り専用**） | subscription                       |

**サブスクリプションスコープの書き込み権限は誰も持たない。** これが #46 の是正点。

### なぜ RG スコープで足りるのか

以前は `Contributor` をサブスクリプションスコープで付けていた。理由は [ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md) の
オンデマンド CD、つまり「使う時だけ RG ごと建てて、終わったら RG ごと消す」運用で、
RG の作成・削除と soft-delete の purge がサブスクリプションレベルの操作だったため。

**その前提は [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) で消えている**（dev は常設・夜間 teardown 廃止）。
日常の CD 経路が触るのは RG の中身だけ:

- IaC は全て `targetScope = 'resourceGroup'`（`cicd/iac/main-bootstrap.bicep:1` / `cicd/modules/bootstrap-core.bicep:1`）
- `provision.sh` は **RG が既にあれば `az group create` を呼ばない**（`cicd/scripts/deploy/provision.sh` の [1/5]）
- RG の削除 + purge は `cicd/scripts/env/cleanup-env.sh` にしか無く、これは **手動 `down` のときだけ**呼ばれる
  （`.github/workflows/deploy.yml:125-129`）

### RBAC Administrator が要る理由

`Contributor` は `Microsoft.Authorization/*/write` を **持たない**。一方 bootstrap は
Managed Identity へ Cognitive Services のロールを付ける:

- `cicd/modules/bootstrap-core.bicep:973` — Functions MI → **Speech User**
- `cicd/modules/bootstrap-core.bicep:1223` — ai-agent MI → **OpenAI User**
- `cicd/scripts/deploy/deploy-ai-agent.sh:134` — 同じ割り当ての冪等な保険

そのため「ロールを付ける権限」だけを足す。ただし ABAC 条件で **付けられるロールをこの 2 つに限定**して
いるので、Owner や User Access Administrator を配る経路にはならない（`setup-oidc.sh` の `RBAC_CONDITION`）。

### 環境の作り直し（down）は人間の作業になった

RG 削除 + Key Vault / Cognitive Services の purge はサブスクリプションレベルの操作なので、
**CD の SP からは実行できない**（意図どおり — 不可逆な破壊操作を「main に書ける主体」に持たせない）。
`deploy.yml` の手動 `down` は権限不足で失敗する。畳みたいときは人間が device-code で:

```bash
az login --use-device-code
az account set --subscription "<subscription-name-or-id>"
RG=rg-dev-mind-inbox ./cicd/scripts/env/cleanup-env.sh
```

## Prerequisites

- 対象サブスクリプションの**所有者/管理者相当**（ロール付与と Entra アプリ作成のため。初回設定と権限変更のときだけ）
- このセッションから Azure を触る準備（[claude-web-azure-access.md](./claude-web-azure-access.md) = device-code）
- GitHub リポジトリの **Settings → Variables** を編集できる権限
- CD（Actions）経由なら追加ツール不要。**device-code セッションから `provision.sh` を直接叩く場合**は
  `az` / `node`(npm) / `pnpm` / `zip` / `curl` に加え **SWA CLI** が必要:
  `npm i -g @azure/static-web-apps-cli`
- **既存の手動ロール割り当てが残っていないこと**（初回のみ）— ロール割り当ての持ち主は
  bicep 1 本で、デプロイスクリプトからは作らない。スクリプト時代に作られた
  ai-agent MI → Cognitive Services OpenAI User の割り当てが残っていると、bicep の宣言が
  `RoleAssignmentExists` で拒否され bootstrap ごと落ちる（= dev が古いまま止まる / #262）。
  残っていれば **1 回だけ手で削除**する（削除権限が要るので人手 / Issue #297）。
  手順は [`cicd/scripts/deploy/README.md`](../../cicd/scripts/deploy/README.md#前提条件-古い手動割り当てが残っていないこと-297)

## Steps

### 1. 一度きり: OIDC 連携を作る（device-code セッションで）

```bash
az login --use-device-code
az account set --subscription "<subscription-name-or-id>"
REPO=yomote/mind-inbox ./cicd/scripts/cloud-env/setup-oidc.sh
```

スクリプトが以下を**冪等に**作る（既にあるものは再利用する / **何も削除しない**）:

1. RG `rg-dev-mind-inbox`（CD には作らせないので、ここで作る）
2. Entra アプリ + SP + federated credential（書き込み用 / 読み取り専用の 2 つ）
3. 上の表のロール割り当て

最後に GitHub Variables に入れる ID を出力する。

### 2. 一度きり: GitHub に Variables を登録

リポジトリ **Settings → Secrets and variables → Actions → Variables** タブに（Secrets ではなく Variables）:

- `AZURE_CLIENT_ID`（書き込み用）
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_READER_CLIENT_ID`（読み取り専用 / 下の「読み取り専用 identity へ切り替える」で使う。**登録だけしても何も変わらない**）

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

---

## 権限の縮小（移行）— サブスクリプション Contributor を外す

> **既に CD が動いている環境向けの手順**（#46）。新規構築ならこの節は不要
> （`setup-oidc.sh` が最初から最小権限で作る）。
>
> 原則: **新しい権限を付けてから、古い権限を外す。** 逆順にすると CD が
> `AuthorizationFailed` で止まる。

### 0. 前提と所要時間

- device-code で Owner 相当としてログイン済み
- 所要 ~30 分（うち deploy の実測が ~15〜25 分）
- 途中で失敗しても **1 コマンドで元に戻せる**（下の「Rollback」）

### 1. 今の状態を記録する（あとで戻すため）

```bash
az login --use-device-code
az account set --subscription "<subscription-name-or-id>"

SUB_ID="$(az account show --query id -o tsv)"
CLIENT_ID="<AZURE_CLIENT_ID の値>"

# 変更前の割り当てを保存しておく（Rollback の材料）
az role assignment list --all --assignee "$CLIENT_ID" \
  --query "[].{role:roleDefinitionName, scope:scope, id:id}" -o table \
  | tee ~/oidc-roles-before.txt
```

### 2. 新しい権限を **足す**（この時点では何も壊れない）

```bash
REPO=yomote/mind-inbox ./cicd/scripts/cloud-env/setup-oidc.sh
```

- RG スコープの Contributor / RBAC Administrator が付く
- 読み取り専用 identity ができる
- 古いサブスクリプションスコープの割り当ては **残ったまま**（スクリプトが警告として表示する）
- この状態でも CD はこれまでどおり動く（権限は和集合）

### 3. 足せたことを確認する

```bash
az role assignment list --scope "/subscriptions/$SUB_ID/resourceGroups/rg-dev-mind-inbox" \
  --query "[].{role:roleDefinitionName, principal:principalId, cond:condition}" -o table
```

`Contributor` と `Role Based Access Control Administrator` が並び、後者に `condition` が入っていること。

### 4. ⚠️ 古いサブスクリプションスコープの割り当てを外す（ここが不可逆寄りの操作）

> **⚠️ 警告**
>
> - この操作以降、CD は RG の外を触れなくなる。**`deploy.yml` の手動 `down` は失敗するようになる**（意図どおり）。
> - **`Cost Management Reader` は消さないこと**（読み取り専用で、ops-inspect の cost-summary が使う）。
> - RBAC の反映には **最大 5 分程度**かかる。消した直後のテストは「まだ古い権限が効いている」ことがある。
> - 消し間違えても Owner 権限があれば再付与できる（下の Rollback）。**Owner 権限を持つ人が居るうちに実行すること**。

```bash
# 消す対象を確認する（Cost Management Reader が混ざっていないか目視する）
az role assignment list --all --assignee "$CLIENT_ID" \
  --query "[?scope=='/subscriptions/$SUB_ID'].{role:roleDefinitionName, id:id}" -o table

# Contributor（または Owner）の id を控えて、それだけを消す
az role assignment delete --ids "<上で確認した Contributor の id>"

# 反映を待つ
sleep 300
```

### 5. 振る舞いで確認する（設定ではなく動作 / [ADR 0018](../adr/0018-runtime-verification-in-the-loop.md)）

**必ずこの順で、全部緑になるまで次に進まない。**

```bash
# (a) デプロイ経路が最小権限で通ることを実測する（最重要）
gh workflow run deploy.yml -f action=up -f environment=dev
gh run watch -R yomote/mind-inbox
```

緑にすべきステップと、それが証明すること:

| ステップ                                                      | 通れば証明されること                                                               |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `Azure login (OIDC...)`                                       | federated credential と SP が生きている                                            |
| `Provision + deploy (up)`                                     | RG スコープの Contributor で bicep 適用・Container Apps 更新・BFF/SWA 配信ができる |
| 同上のログ `既存の RG を再利用`                               | **RG 作成（サブスクリプションスコープ）を呼んでいない**                            |
| 同上のログ `ロール割り当て:`                                  | 条件つき RBAC Administrator で MI へのロール付与ができる                           |
| `Smoke test（認可と疎通の実測）`                              | 認可の門が生きたまま配信できた                                                     |
| `Golden path`（実 AI）/ `Golden path scenario`（UI 込み E2E） | 実環境の相談ユースケースが壊れていない                                             |

```bash
# (b) 読み取り系 workflow が壊れていないこと
gh workflow run ops-inspect.yml -f check=azure-resources -f environment=dev
gh workflow run ops-inspect.yml -f check=cost-summary          # Cost Management Reader の実測
gh workflow run refresh-infra-diagram.yml
```

`cost-summary` が `(未検証: ...)` になったら Cost Management Reader を消してしまっている。
手順 2 のスクリプトを再実行すれば付け直せる。

```bash
# (c) 縮まったことの確認（こちらは設定の確認 — 振る舞いの証拠ではない）
az role assignment list --all --assignee "$CLIENT_ID" \
  --query "[].{role:roleDefinitionName, scope:scope}" -o table
```

期待する出力は **3 行以内**:

- `Contributor` @ `.../resourceGroups/rg-dev-mind-inbox`
- `Role Based Access Control Administrator` @ 同上
- （移行期のみ）`Cost Management Reader` @ `/subscriptions/...`

> **否定側（RG の外を触れないこと）を振る舞いで測る手段は無い。**
> federated credential は GitHub Actions からしか使えず、シークレットが無いので
> 手元で SP になりすまして「拒否される」ことを実演できない。代わりに:
> **(a) が緑になった時点で、CD は RG スコープの権限だけで動いていることが実測されている**
> （広い権限はもう存在しないため）。これが最も強い証拠。

### 6. 読み取り専用 identity へ切り替える（任意 / 別 PR）

ここまでで #46 の High（サブスクリプション Contributor）は解消している。
さらに「読むだけの 3 本が書き込み権限を持ち歩く」状態も無くしたい場合:

1. Variables に `AZURE_READER_CLIENT_ID` を登録（未使用なので単独では無害）
2. 次の 3 ファイルの `client-id:` を `${{ vars.AZURE_READER_CLIENT_ID }}` に変える PR を出す
   （ガード条件の変数名も合わせる）:
   - `.github/workflows/ops-inspect.yml:99`
   - `.github/workflows/refresh-infra-diagram.yml:64`
   - `.github/workflows/golden-path-monitor.yml:50`
3. マージ後、3 本を手動 dispatch して緑を確認する
4. 書き込み用 identity から課金参照を外す:

   ```bash
   GRANT_COST_READER=false REPO=yomote/mind-inbox ./cicd/scripts/cloud-env/setup-oidc.sh
   az role assignment list --all --assignee "$CLIENT_ID" \
     --query "[?scope=='/subscriptions/$SUB_ID'].id" -o tsv   # 残っていれば delete
   ```

   これで書き込み用 identity は **サブスクリプションスコープの割り当てをひとつも持たなくなる**。

> **既知の劣化**: `ops-inspect` の `recent-errors` のうち **Container App のライブログ tail**
> （`az containerapp logs show`）は `Reader` では実行できない（`Microsoft.App/.../authtoken/action` が要る）。
> 同じチェックの中の **Log Analytics クエリ側は Reader で動く**し、コメントにあるとおり
> 「過去の障害はそこでしか追えない」ので、実用上の損失は小さい。失敗しても
> `(未検証: 理由)` として可視化される設計になっている。

## Verification

- [ ] `setup-oidc.sh` が ID を出力し、GitHub Variables に登録済み
- [ ] Actions の "deploy" 実行で `Azure login (OIDC)` ステップが成功（緑）
- [ ] up 後: `az resource list -g rg-dev-mind-inbox -o table` にリソースが並ぶ / SWA URL がスマホで開ける
- [ ] `deploy` の `Smoke test` / `Golden path` / `Golden path scenario` が緑
- [ ] `az role assignment list --all --assignee <AZURE_CLIENT_ID>` に **サブスクリプションスコープの書き込みロールが無い**
- [ ] コスト: `./cicd/scripts/cost/show-cost.sh` で当月コストを確認

## Rollback

- **権限縮小で CD が壊れた** → 元の割り当てを戻す（`~/oidc-roles-before.txt` を見ながら）:

  ```bash
  az role assignment create --assignee "$CLIENT_ID" \
    --role Contributor --scope "/subscriptions/$SUB_ID"
  ```

  反映まで数分待ってから `gh workflow run deploy.yml -f action=up -f environment=dev` で再確認する。
  そのうえで、何が足りなかったのかを Issue #46 に記録する（次の縮小の材料になる）。

- up が途中失敗 → 人間が `cleanup-env.sh` で撤収してから再 up
- OIDC をやめる → GitHub Variables を削除し、`az ad app delete --id <AZURE_CLIENT_ID>`（読み取り専用も同様）、ロール割当を削除

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
> (Azure 側の設定変更 = 人間の作業)。これは **CD の資格情報を main 以外へ開くこと**なので、
> 使い終わったら消す (`az ad app federated-credential delete`)。`setup-oidc.sh` は
> 想定外の subject が残っていると警告を出す。

### `AuthorizationFailed`（デプロイ中に権限エラー）

CD の SP は **RG スコープしか持たない**（#46）。メッセージの `scope` を読んで切り分ける:

| scope                                                  | 意味                              | 対処                                                                                                 |
| ------------------------------------------------------ | --------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `/subscriptions/<id>` （RG より上）                    | RG の外を触ろうとしている         | **その操作は CD から外す**。人間が device-code でやる（RG 作成・削除・purge がこれ）                 |
| `.../resourceGroups/rg-dev-...`                        | ロールが付いていない / 反映待ち   | `setup-oidc.sh` を再実行。RBAC 反映に数分かかる                                                      |
| `Microsoft.Authorization/roleAssignments/write` を含む | MI へのロール付与が条件に弾かれた | 付けようとしたロール GUID が OpenAI User / Speech User 以外。`setup-oidc.sh` の condition を更新する |

確認: `az role assignment list --all --assignee <AZURE_CLIENT_ID> -o table`

### 手動 `down` が失敗する

**仕様**（#46 以降）。RG 削除と purge はサブスクリプションレベルの操作で、CD の SP には権限が無い。
人間が device-code で `RG=rg-dev-mind-inbox ./cicd/scripts/env/cleanup-env.sh` を実行する。

### main にマージしたのにデプロイされない

- 原因: `AUTO_DEPLOY_ENABLED` が未設定（安全側の既定）。または OIDC の 3 変数が未登録。
- 確認: Actions の該当 run に `自動デプロイは未解禁のため skip しました` の notice が出ているか。
- 対処: 認可の設定（[entra-spa-auth-and-budget.md](./entra-spa-auth-and-budget.md)）を終えてから `AUTO_DEPLOY_ENABLED=true` を登録する。

### GitHub Environment を使わないのはなぜか

federated credential の subject は `environment:<name>` にもできるが、**今回は採らない**:

- 目的は「**資格情報が任意のブランチから使えないこと**」で、それは現在の
  `ref:refs/heads/main` subject が既に満たしている（main 以外からは token 交換自体が失敗する）
- Environment を使うなら **required reviewers を付けてはいけない**。付けると
  main マージのたびに承認待ちで止まり、[ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) の
  「main マージ → dev へ自動デプロイ」が毎回停止する。承認ゲートと subject の絞り込みは別物
- 4 本の workflow が同じ client id を共有しているため、subject を environment 型に
  **置き換える**と、`environment:` を宣言していない 3 本が即座に落ちる

将来 Environment を導入するなら「承認者なし・deployment branch を `main` に限定」の
Environment を作り、4 本すべてに `environment:` を宣言してから subject を差し替えること。

### 連続でマージしたときのデプロイ順

- 同一 concurrency group + `cancel-in-progress: false` で直列化される。走行中のデプロイは中断されず、後続はキューイングされて順に流れる（中途半端な状態を残さないため意図的にこの設定）。
- GitHub Actions の仕様上、**pending は最新 1 件のみ保持**されるため、短時間に何度もマージすると中間のコミットはデプロイをスキップして最新だけが反映される。常設 dev の用途では問題にならない。

### up が遅い

- image は ghcr に事前ビルド済み（#67）なので、デプロイ経路でのイメージビルドは無い。撤収しても ghcr の image は残るため、再 up でビルドし直す必要はない（`deploy-*.sh` は ghcr のタグ差し替えのみ）。
- 詳細: [ghcr images runbook](./ghcr-images.md)

## Related

- Issue: [#46 OIDC CD の SP ロールスコープ最小化](https://github.com/yomote/mind-inbox/issues/46)
- ADR: [0009 オンデマンド CD](../adr/0009-on-demand-cd-via-github-actions-oidc.md) / [0013 常設 dev](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0006 device-code](../adr/0006-azure-access-via-device-code.md) / [0018 動作検証](../adr/0018-runtime-verification-in-the-loop.md)
- ワークフロー: `.github/workflows/deploy.yml`
- スクリプト: `cicd/scripts/cloud-env/setup-oidc.sh` / `cicd/scripts/deploy/provision.sh` / `cicd/scripts/env/cleanup-env.sh`
- 関連 Runbook: [claude-web-azure-access.md](./claude-web-azure-access.md) / [local-fullstack-dev.md](./local-fullstack-dev.md) / [refresh-infra-diagram.md](./refresh-infra-diagram.md)
- IaC 手順: [`cicd/iac/README.md`](../../cicd/iac/README.md)
