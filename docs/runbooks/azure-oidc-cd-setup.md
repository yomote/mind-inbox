# 常設 dev の CD（GitHub Actions + Azure OIDC）の設定と運用

## Trigger

`deploy.yml` を初めて使うとき、常設 dev の運用（main マージの自動デプロイ / 手動 up / 一時的な down）を行うとき、
または **CD の権限を縮小するとき**（[#46](https://github.com/yomote/mind-inbox/issues/46)）。

> 方針は [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md)（常設・待機ほぼ ¥0 + main マージ自動デプロイ）。
> [ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md) のオンデマンド teardown は supersede 済みで、
> **夜間の自動 teardown は廃止**。OIDC 認証・`provision.sh` / `cleanup-env.sh` の機構は引き続き使う。

## 権限モデル（何が何をできるか）

GitHub Actions から Azure に入る identity は **2 つ**。

| identity                                               | federated credential の subject                    | 使う workflow                                                                                                                                  | ロール                                                                         | スコープ                           |
| ------------------------------------------------------ | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------- |
| `gha-oidc-mind-inbox-cd`（書き込み / `setup-oidc.sh`） | `...:ref:refs/heads/main`（main 以外から使えない） | `deploy.yml`、および `main` ref で動く読み取り系（`ops-inspect.yml` の `ref: main` / `refresh-infra-diagram.yml` / `golden-path-monitor.yml`） | Contributor                                                                    | **RG のみ**（`rg-dev-mind-inbox`） |
| 〃                                                     | 〃                                                 | 〃                                                                                                                                             | RBAC Administrator（OpenAI User / Speech User しか付けられない ABAC 条件つき） | **RG のみ**                        |
| 〃                                                     | 〃                                                 | 〃                                                                                                                                             | Cost Management Reader（**読み取り専用** / `cost-summary` 用・移行期のみ）     | subscription                       |
| `gha-oidc-readonly-mind-inbox`（読むだけ / `ro.sh`）   | `...:ref:refs/heads/ops/inspect`                   | `ops-inspect.yml`（`ref` が `main` 以外 = 普段の調査経路）                                                                                     | Reader / Cost Management Reader / Log Analytics Reader（**書き込み系なし**）   | subscription                       |

**サブスクリプションスコープの書き込み権限は誰も持たない。** これが #46 の是正点。

> `setup-oidc.sh` がかつて作っていた読み取り専用 identity `gha-oidc-mind-inbox-cd-ro`
> （変数名 `AZURE_READER_CLIENT_ID`）は、**どの workflow からも一度も使われないまま
> `AZURE_CLIENT_ID_RO` 系に一本化して廃止した** (#397)。Azure 側にアプリ登録が残っていれば
> 削除してよい（読み取り系ロールのみなので残っていても書き込みはできない。実在は未検証 —
> 確認は人間の Portal / device-code 作業）。

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
2. Entra アプリ + SP + federated credential（書き込み用。read-only 識別は別手順 `ro.sh` — 下の「read-only の識別を足す」）
3. 上の表のロール割り当て（書き込み用の分）

最後に GitHub Variables に入れる ID を出力する。

### 2. 一度きり: GitHub に Variables を登録

リポジトリ **Settings → Secrets and variables → Actions → Variables** タブに（Secrets ではなく Variables）:

- `AZURE_CLIENT_ID`（書き込み用）
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

read-only 用の `AZURE_CLIENT_ID_RO` はここでは登録しない — 下の「read-only の識別を足す」の手順（`ro.sh`）が出す ID を登録する。

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

### 5. 振る舞いで確認する（設定ではなく動作 / [ADR 0018](../adr/archive/operations/runtime-verification-in-the-loop.md)）

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

### 6. 読むだけの workflow と書き込み identity の分離（現状と今後）

かつてここに「読み取り系 3 本の `client-id:` を読み取り専用 identity（`AZURE_READER_CLIENT_ID`）へ
置換する」手順があったが、**一度も実行されないまま、`ops-inspect` の ref 分岐
（`AZURE_CLIENT_ID_RO` / #209）と両立しなくなったため廃止した**（#397 — 置換すると ref 分岐が壊れる）。
read-only 識別の正典は `AZURE_CLIENT_ID_RO` 系（下の「read-only の識別を足す」）に一本化している。現状:

- `ops-inspect` は `ref: ops/inspect` で起動すれば read-only 識別で走る（[ops-inspect.md](ops-inspect.md)）
- `refresh-infra-diagram` / `golden-path-monitor` と `main` ref の `ops-inspect` は今もデプロイ SP で動く。
  read-only 識別の federated credential は `ops/inspect` ブランチ紐づけなので、`main` で走るこの 2 本には**流用できない**
- 「読むだけの workflow が書き込み identity を持ち歩かない」形への恒久解は
  [#405](https://github.com/yomote/mind-inbox/issues/405)（GitHub Environments — ブランチ名依存そのものを消す）が持つ

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

**失敗したら client ID を登録しない**。`ro.sh` はどこか 1 ステップでも失敗すると、末尾に
「❌ 未完了のステップがあります」と失敗一覧を出して**非ゼロで終了し、登録案内を出さない**。
資格情報やロールが欠けたまま `AZURE_CLIENT_ID_RO` を登録すると、次の `ops-inspect` が
Azure login やクエリで落ちるまで不完全な構成に気づけないため。原因を直してそのまま再実行してよい
(全ステップ冪等)。

### この構成のトレードオフ

`ops/inspect` に保護を掛けると「調査のたびに直 push して dispatch する」という用途が成立しないため、
**保護なしのまま受容している**。つまり**このリポジトリに push できる主体は、read-only の Azure
資格情報 (Reader / Cost Management Reader / Log Analytics Reader) を実質的に取得できる**。
Log Analytics には相談ログや例外詳細が載りうるので、「read-only だから無害」ではない。

**恒久解は [#405](https://github.com/yomote/mind-inbox/issues/405)** — フェデレーション資格情報の
サブジェクトを `environment:` 型にすると、ブランチ名への依存 (= この構図の原因) が消える。
移行が終わるまでは下の 4 条件で受容する。

### read-only 識別の受容条件 (正典)

> **ここがこの 4 条件の正典。** 元は ADR 0054 に書かれていたが、2026-08-14 の debrief で
> 「これはシステムアーキテクチャではなく**開発設備 (CI 資格情報) の運用判断**なので ADR ではない」と
> 裁定され、記録は [`docs/adr/archive/operations/readonly-investigation-identity-on-unprotected-branch.md`](../adr/archive/operations/readonly-investigation-identity-on-unprotected-branch.md)
> へ退避した (Status の変更ではなく分類の退避)。**条件を外す・広げるときは、退避した記録ではなくこの節を直すこと。**
> 判断の経緯 (却下した選択肢とその理由) は退避先に残してある。

1. **この識別には書き込み系のロールを一切付けない** — `ro.sh` が付けるのは Reader /
   Cost Management Reader / Log Analytics Reader の 3 つだけ。**増やすならこの節を先に改訂する**
2. **`ops-inspect` workflow は状態を変える操作をしない** — `az` は show / list のみ、`curl` は GET のみ、
   入力は `env:` 経由でのみ参照する (workflow 冒頭に明記されている)。
   **例外は Cost Management の query API 1 つだけ** (下の 2-a)

   **2-a. 例外: Cost Management の query API への POST は「読み取り」として許す**

   `cost-summary` は `az rest --method post` で
   `https://management.azure.com/subscriptions/{sub}/providers/Microsoft.CostManagement/query`
   を叩く。HTTP メソッドは POST だが、**これは書き込みではない**:

   - **なぜ POST なのか** — この API は集計の条件 (期間 `timeframe` / 粒度 `granularity` /
     グルーピング `grouping`) を body で渡す設計であり、クエリを URL に載せないため POST になる。
     **サブスクリプションのリソース・構成・課金設定は何も変わらない**。読めるのは
     Cost Management Reader の範囲の請求データだけで、必要な権限も Reader 系のみ (条件 1 のロールで足りる)
   - **なぜ GET で代替できないのか** — GET の `az consumption usage list` は、このサブスクリプションでは
     `pretaxCost` が文字列 `"None"` で返り実額が取れなかった (2026-08-10 実測 / 89 件すべて。
     Cost Management Reader を付けても変わらず、権限ではなく API の問題)。
     「予算 ¥3,000 に対して今いくらか」を読むという `cost-summary` の目的そのものが GET では果たせない
   - **例外の範囲はこの 1 URI に閉じる** — `Microsoft.CostManagement/query` 以外への POST、および
     PUT / PATCH / DELETE は一切許さない。**「読み取りに必要なら POST してよい」という一般則にはしない**
     (それを許すと条件 2 が実質的に空文になる)。増やすならこの節を先に改訂する。
     機械的な検査は無く、**2 つ目の POST が増えていないかはレビューで見るしかない**

3. **人間が実行するスクリプトはブランチ名で取らない** — `ro.sh` は PO 自身の Azure 権限
   (device-code ログイン / [ADR 0006](../adr/0006-azure-access-via-device-code.md)) の下で走るため、
   **commit sha で固定して取得する** (上の手順のとおり)。ブランチ名で取ると、このブランチに
   push できる者が中身を差し替えた瞬間に read-only の枠を超えて PO の全権限で任意コマンドが走る
   (この経路が唯一の「壊せる」穴)。**機械的に強制されていない運用規律**なので、
   `ro.sh` をブランチ名で取る運用に戻すと穴が開く
4. **リポジトリの write 権限を持つ主体が増えたら再判断する** — 現状は PO 1 人 +
   その委任で動くエージェントセッションのみ。共同作業者が増えた時点でこの受容は前提を失う

> **既知の劣化**: `ops-inspect` の `recent-errors` のうち **Container App のライブログ tail**
> （`az containerapp logs show`）は Reader 系ロールでは実行できない（`Microsoft.App/.../authtoken/action` が要る）。
> 同じチェックの中の **Log Analytics クエリ側は Reader で動く**し、コメントにあるとおり
> 「過去の障害はそこでしか追えない」ので、実用上の損失は小さい。失敗しても
> `(未検証: 理由)` として可視化される設計になっている。

### どう使われるか

`ops-inspect.yml` の guard が ref を見て使い分ける。

| ref      | ログインに使う識別                      |
| -------- | --------------------------------------- |
| `main`   | 従来のデプロイ SP (`AZURE_CLIENT_ID`)   |
| それ以外 | **read-only ID** (`AZURE_CLIENT_ID_RO`) |

### 動作検証

`ops/inspect` ブランチから `ops-inspect` を dispatch し、`check: azure-resources` が
🟢 になること。ログの guard ステップに `ログインに使う識別: read-only ID (ops/inspect)`
が出ていれば、read-only 側で通っている。

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

**ただし read-only 識別 (`ops-inspect` 用) だけは事情が違う** — あちらは
「ブランチ名の完全一致しか書けない」制約が保護なしブランチを生んでいるので、Environment の
deployment branch policy (ワイルドカード可) で解ける。移行は
[#405](https://github.com/yomote/mind-inbox/issues/405) が持つ。

### 連続でマージしたときのデプロイ順

- 同一 concurrency group + `cancel-in-progress: false` で直列化される。走行中のデプロイは中断されず、後続はキューイングされて順に流れる（中途半端な状態を残さないため意図的にこの設定）。
- GitHub Actions の仕様上、**pending は最新 1 件のみ保持**されるため、短時間に何度もマージすると中間のコミットはデプロイをスキップして最新だけが反映される。常設 dev の用途では問題にならない。

### up が遅い

- image は ghcr に事前ビルド済み（#67）なので、デプロイ経路でのイメージビルドは無い。撤収しても ghcr の image は残るため、再 up でビルドし直す必要はない（`deploy-*.sh` は ghcr のタグ差し替えのみ）。
- 詳細: [ghcr images runbook](./ghcr-images.md)

## Related

- Issue: [#46 OIDC CD の SP ロールスコープ最小化](https://github.com/yomote/mind-inbox/issues/46) / [#405 read-only 識別を GitHub Environments ベースへ移行](https://github.com/yomote/mind-inbox/issues/405)
- ADR: [0009 オンデマンド CD](../adr/0009-on-demand-cd-via-github-actions-oidc.md) / [0013 常設 dev](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0006 device-code](../adr/0006-azure-access-via-device-code.md) / [0018 動作検証](../adr/archive/operations/runtime-verification-in-the-loop.md)
- ワークフロー: `.github/workflows/deploy.yml`
- スクリプト: `cicd/scripts/cloud-env/setup-oidc.sh` / `cicd/scripts/deploy/provision.sh` / `cicd/scripts/env/cleanup-env.sh`
- 関連 Runbook: [claude-web-azure-access.md](./claude-web-azure-access.md) / [local-fullstack-dev.md](./local-fullstack-dev.md) / [refresh-infra-diagram.md](./refresh-infra-diagram.md)
- IaC 手順: [`cicd/iac/README.md`](../../cicd/iac/README.md)
