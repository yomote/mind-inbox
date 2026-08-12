# 0046. 環境を「宣言から作り直せるもの」にする — ライフサイクル 3 層分断 / Entra の Graph Bicep 宣言 / 週次プロビジョンテスト

- Status: Accepted (2026-08-12, design-gate にて PO 承認)
- Date: 2026-08-12
- Deciders: omoteforlab
- Consulted: —
- Informed: —

関連: [ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md)（「常設」の解釈を**追補**する / supersede しない）/ [ADR 0003](0003-two-phase-bicep.md)（2-phase Bicep — 本 ADR はこの構造の上に乗る）/ [ADR 0025](0025-deploy-container-images-by-immutable-sha-tag.md)（image は不変 sha）/ [ADR 0045](0045-e2e-artifacts-are-secret-by-default.md)（「管理系 RG」の初出。本 ADR がその実体を定義する）/ [ADR 0018](0018-runtime-verification-in-the-loop.md)（動作検証）/ [ADR 0006](0006-azure-access-via-device-code.md)

対象 Issue: [#302](https://github.com/yomote/mind-inbox/issues/302)（ライフサイクル分断）/ [#303](https://github.com/yomote/mind-inbox/issues/303)（設定を宣言に一本化）/ [#306](https://github.com/yomote/mind-inbox/issues/306)（プロビジョンテスト）

## Context and Problem Statement

3 つの Issue は**同じ 1 つの問題の別の面**なので、1 本の ADR で決める。PO の提起（2026-08-12）:

> 設定はちゃんと bicep に全部書き切ってほしい。**消えちゃうものがあるのは駄目**なんだよ。だって**宣言されてないってことだから**。

「リソースを消すと設定が失われる」のではなく、**失われる設定は宣言されていない**。この言い換えを受け入れると、現状には 3 つの穴がある。

### 穴 1 — 宣言されていない設定がある（#303）

`deploy-*.sh` が `--set-env-vars` / `az functionapp config appsettings set` / `az role assignment create` を叩いている。**bicep だけを読んでも実環境の設定は分からず、bicep を適用しただけでは環境が正しく結線されない。**

この穴は既に実害を出している。`deploy-ai-agent.sh` のロール付与と bicep の宣言が二重になり、`RoleAssignmentExists` で **dev の自動デプロイが 9 回連続で落ちた**（#262）。

### 穴 2 — ライフサイクルの違うものが同居している（#302）

`cleanup-env.sh` は RG を削除したうえで **Key Vault と Cognitive Services の soft-delete を明示的に purge** する。同じ RG に **Cosmos（ユーザーデータ）と Azure OpenAI（クォータ）**が同居しているため、撤収が「消えては困るもの」を巻き込み、**soft-delete による救済すら残らない**。

### 穴 3 — 宣言できているか確かめる手段が無い（#306）

**宣言的にすることと、宣言的になったことを確かめることは別物**で、後者の手段が存在しない。既に立っている dev が、bicep の不完全さを隠している。[`docs/testing/strategy.md`](../testing/strategy.md) §1.2 の入場条件で言えば「**無いと『宣言の外に設定が残っている』が静かに通る**」。

加えて **bicep 自体の腐敗**（API version の廃止 / provider の既定変更 / region quota）も、作り直さない限り検出されない。

## Decision Drivers

- **宣言されていない設定をゼロにする** — PO 原則。例外を作るなら ADR で明示する
- **ユーザーデータとクォータを撤収で失わない** — 消えて困るものと作り直してよいものを構造で分ける
- **「宣言できている」を主張ではなく機械で確かめる** — 検証手段のない宣言性は宣言性ではない
- **週次の自動化が PO の手作業を増やさない** — 毎週ログインが壊れて PO が手で直すなら本末転倒
- **待機コストを増やさない** — [ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) の「待機ほぼ ¥0」を壊さない
- **[ADR 0003](0003-two-phase-bicep.md) の 2 フェーズ構造を壊さない** — 新しい構造を発明せず既存の上に乗せる

## Considered Options

- **Option A: 3 層に分け、Entra を Graph Bicep で宣言し、週次で作り直して検証する**（本 ADR の採用案）
- **Option B: 安全弁だけ入れて現状維持** — `cleanup-env.sh` の purge を止めるだけ。層は分けず、宣言化も進めない
- **Option C: 全部を 1 つの RG のまま宣言化する** — 層は分けないが設定は全部 bicep へ。撤収は「やらない運用」で守る
- **Option D: 環境を完全に使い捨てにする** — Cosmos も含めて毎回作り直し、データは毎回捨てる

## Decision Outcome

Chosen option: **Option A**。

### D1 — リソースをライフサイクルで 3 層に分ける

| 層 | 置き場所 | 中身（`cicd/modules/bootstrap-core.bicep` の行） | 撤収の対象 |
| --- | --- | --- | --- |
| **持続層** | `rg-shared-mindbox`（[ADR 0045](0045-e2e-artifacts-are-secret-by-default.md) が言う「管理系 RG」の実体） | Cosmos `:1250`（ユーザーデータ）/ Azure OpenAI `:912`（アカウント + デプロイ = クォータ）/ **Speech `:952`（F0 = 1 サブスクに 1 つ）** / Log Analytics `:398`（履歴）/ **Key Vault（新設）** / バックアップ保管ストレージ（新設） | ❌ **触らない** |
| **環境層** | `rg-dev-mind-inbox` | SWA `:1426` / Function App `:661` + Plan `:632` + Storage `:615` / Container Apps `:851,:1039,:1080` + managed environment `:834` | ✅ **壊して作り直す** |
| **デプロイ層** | （リソースを作らない） | image の sha 差し替え / zip deploy / 静的配信 | — |

**Speech を持続層に置くことで、#306 の「Speech を検証対象に含めるか」は構造的に解ける** — F0 は 1 サブスクに 1 つなので、環境層に置くと再作成のたびに枠の取り合いになる。持続層なら壊さないので競合しない。**含めない**が答えになる。

**Key Vault は現在 dev に存在しない** — `sqlAdminKeyVault` は `if (enableSql)` 条件つきで（`bootstrap-core.bicep:495`）、dev は `enableSql=false` なので作られていない。[ADR 0045](0045-e2e-artifacts-are-secret-by-default.md) D5 が秘密鍵の置き場所として要求している Key Vault は、**持続層で新規に宣言する**（これが [#301](https://github.com/yomote/mind-inbox/issues/301) の前提）。VNet / Private Endpoint も同様に dev では未作成（`:418` `if (vnetEnabled)`）。

**Entra のアプリ登録はどの RG にも属さない**（テナントのオブジェクト）。ライフサイクルとしては**持続層と同じ扱い**にする（D5）。

環境層から持続層への参照はパラメータ渡しにする（既存 bicep が output 経由で組み立てている構造をそのまま外側へ広げるだけ）。

### D2 — 層の境界は「**名前が決定的か**」で引く

これが本 ADR の中心的な判断基準。**壊して作り直せるのは、作り直しても同じ名前で戻ってくるリソースだけ**。

| リソース | 再作成後の**外から見える名前** | 参照している設定 |
| --- | --- | --- |
| Function App | `func-dev-mindbox.azurewebsites.net` — **決定的** | — |
| **SWA** | `<ランダム語>-<ハッシュ>.azurestaticapps.net` — **生成物。作り直すと変わる** | Entra の `spa.redirectUris` / CORS |
| **Container Apps** | `ca-dev-mindbox-ai-agent.<生成 ID>.<region>.azurecontainerapps.io` — **managed environment の既定ドメインが生成物**。CAE を作り直すと変わる | BFF の `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` |
| Cosmos / OpenAI / Speech | 決定的だが**中身・クォータが戻らない** | — |

名前が生成物であるリソースを環境層に置くと、**それを参照している設定が全部追従を要求される**。

**そして現状、その追従は宣言でなく命令で行われている。** 調査で判明した実態:

- `cicd/iac/main-bootstrap.parameters.json` は `aiAgentBaseUrl` / `voicevoxWrapperBaseUrl` に **現在の FQDN をハードコード**している
- `deploy-backend.sh:189-195` が、デプロイのたびに**実 FQDN を `az containerapp show` で引き直して `appsettings set` で上書き**している

つまり **bicep のパラメータは既に陳腐化しており、スクリプトがそれを毎回黙って直している**。これは「宣言の外に設定がある」（#303）の典型であると同時に、**#306 が最初に踏む地雷**でもある: CAE を作り直すと生成ドメインが変わるので、**bicep だけで作り直した環境は BFF が ai-agent に届かない状態で立つ**。

したがって D7（スクリプトから設定を撤去する）は整理ではなく **#306 を成立させるための必須条件**になる。撤去と同時に、**bicep 側をハードコードから `containerApp.properties.configuration.ingress.fqdn` の参照に変える**必要がある。

**「名前が生成物なら、参照側を宣言で追従させる」**——これが D3 / D7 に共通する設計原理。SWA と CAE を持続層へ逃がす案もあったが、両者は #306 で最も検証したい「bicep から作り直せるか」の対象なので、逃がさず追従させる方を選ぶ。

### D3 — Entra アプリ登録は Microsoft Graph Bicep 拡張で宣言する

**現状は手作業**（[`docs/runbooks/entra-spa-auth-and-budget.md`](../runbooks/entra-spa-auth-and-budget.md) の「一度きり」の手順を人が `az ad app` で叩く）。これを宣言に移す。

一次ソースで、**手作業の 5 ステップがすべて宣言可能**であることを確認した（[Graph Bicep v1.0 リファレンス](https://learn.microsoft.com/en-us/graph/templates/reference/overview)）:

| 手作業のステップ | 宣言での表現 |
| --- | --- |
| SPA 種別でアプリ登録 | `Microsoft.Graph/applications@v1.0` の `spa.redirectUris` |
| SP を別途作る（無いとログイン無限ループ） | `Microsoft.Graph/servicePrincipals@v1.0` |
| トークンを v2 にする（無いと API 401） | `api.requestedAccessTokenVersion: 2` |
| `access_as_user` スコープ公開 | `api.oauth2PermissionScopes`（`id` を固定 GUID で採る） |
| 自己参照 delegated permission + **事前 consent** | `requiredResourceAccess` + **`Microsoft.Graph/oauth2PermissionGrants@v1.0`** |

**#303 のコメントが「最後の関門」としていた事前 consent は、`oauth2PermissionGrants` が v1.0 のサポート対象なので解ける。**

制約の確認（[一次ソース](https://learn.microsoft.com/en-us/graph/templates/bicep/limitations)）:

- **`passwordCredentials` 非対応** → **該当しない**。SPA + PKCE でクライアントシークレットを使っていない
- **what-if が拡張リソースで使えない** → D4 で回避する
- role-assignable group / Deployment stacks 非対応 → 該当なし

**冪等性の鍵は `uniqueName`** — `applications` の**必須かつ Immutable な代替キー**。これを固定すれば、何度デプロイしても**同じアプリ登録**が更新され、**`appId`（クライアント ID）が変わらない**。つまり:

- SWA を作り直して**ホスト名が変わっても**、`spa.redirectUris` が新ホスト名に更新される（bicep が SWA の output を参照する）
- **クライアント ID は変わらない**ので、フロントのビルド時変数も Function App の EasyAuth `allowedAudiences` も揺れない

これで「週次で作り直すと毎週ログインが壊れる」（#306 のハードな前提 1）が構造的に消える。

#### ⚠️ 移行時に一度だけクライアント ID が変わる可能性がある（未確認）

現在のアプリ登録は `az ad app create` で手作業で作られており（`entra-spa-auth-and-budget.md:35-39`）、**`uniqueName` を持たない**。`uniqueName` は Immutable なので、**既存アプリに後から付けられるかは未確認**。付けられない場合、bicep は「既存を採用」できず**新しいアプリ登録を作る**ことになり、`appId` が変わる。

影響範囲は特定済み（すべてリポジトリ内で追える）:

- `cicd/iac/main-bootstrap.parameters.json` の `functionAuthEntraClientId`（現在 `a0f2c66e-…` が**コミット済み**）
- フロントのビルド時変数（`VITE_*`、GitHub Actions Variables 側）
- 事前 consent のやり直し（宣言に含まれるので自動）

**第 1 段階（D9）で最初に確かめるのはこれ**。変わるなら一度だけの移行コストとして受け入れ、変わらないならそのまま。**どちらでも設計は成立する**（毎週変わるわけではないため）。

### D4 — Graph リソースは **config フェーズ**に置き、bootstrap の what-if を汚さない

[ADR 0003](0003-two-phase-bicep.md) の 2 フェーズをそのまま使う:

- **bootstrap**（Azure リソースのみ）— what-if が効く。[#308](https://github.com/yomote/mind-inbox/issues/308) が PR CI に足そうとしている `az deployment group what-if` の網はここに掛かる。**SWA の実ホスト名を output する**
- **config**（Graph + EasyAuth 結線）— bootstrap の output を param で受け、Entra を宣言する。**what-if は使えない**

Graph リソースを bootstrap に混ぜると **bootstrap 全体が what-if 不可**になり、#308 の投資が無駄になる。**分離は what-if の網を守るための判断**であって、単なる整理ではない。

**ただし config フェーズは現在いちども適用されていない。** `provision.sh` が適用するのは `main-bootstrap.bicep` だけで（`provision.sh:188-195`）、`main-config` は同ファイルのコメント 2 箇所（`:32, :198`）に名前が出るのみ。`deploy.yml` も参照しない。CI での扱いは `iac-validate.yml:36` の build 検証だけ。**つまり [ADR 0003](0003-two-phase-bicep.md) の 2 フェーズは、dev では片肺で動いている。**

したがって D4 は「既存の config フェーズに足す」ではなく「**config フェーズを実際に適用する経路を作る**」ことを含む。

また `main-config.bicep` が呼ぶ `static-site-auth.bicep:53` の `deploymentScripts` は、**SWA 向けの Web アプリ登録（クライアントシークレットあり）**を作るもので、ログインに使っている **SPA アプリ登録（PKCE / シークレットなし）とは別物**。[ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) が「SWA は Free・認可は Functions の EasyAuth」と決めているので、**この deploymentScript は現状と乖離した経路**。宣言化のついでに整理する（#303 のコメントが `cicd/iac/README.md` §3 について指摘していた点）。

**ファイル境界（[PR #292](https://github.com/yomote/mind-inbox/pull/292) との衝突回避）**: #292 が `provision.sh:75-200` / `deploy-ai-agent.sh` / `bootstrap-core.bicep:1201-1240` / `main-bootstrap.bicep:112-125,183-195` を占有している。config フェーズの適用は **`provision.sh` を編集せず、`deploy.yml` に独立したステップとして足す**（`deploy.yml` は #292 の変更対象外）。#292 がマージされてから `provision.sh` へ寄せるかは後で決める。

### D5 — `cleanup-env.sh` は Entra アプリ登録を**削除しない**

コードを読むと、現在削除しているのは **deployment output で `staticSiteEntraAppAutoCreated == "true"` のものだけ**（`cleanup-env.sh:76-95, 97-120`）。**今ログインに使っている手作業のアプリ登録は、この条件に当たらないので実は削除されていない。**

ここに**宣言化の落とし穴**がある: **D3 でアプリ登録を bicep に宣言すると、それが deployment output に載り、`cleanup-env.sh` の削除対象に入りうる。** つまり「宣言化したら、今は消えていないものが毎週消えるようになる」という退行が起こる。

したがって **`DELETE_ENTRA_APP` の既定を `false` にし、アプリ登録は RG のライフサイクルから切り離す**。テナントのオブジェクトであり、`uniqueName` で冪等に再適用できるので、削除する理由が無い。

### D6 — `cleanup-env.sh` の purge を既定 off にする（**即効の安全弁**）

`PURGE_DELETED_KEYVAULTS`（`cleanup-env.sh:9`）/ `PURGE_DELETED_COGNITIVE_SERVICES`（`:10`）の既定を `false` にする。**移行が終わる前に事故が起きるのを止めるのが先**で、これは層の移行を待たずに単独で入れられる。

**現状の危険度を正確に書くと**: Key Vault は dev に存在しない（D1）ので、いま実害が出るのは **Cognitive Services = OpenAI と Speech** の方。走査は `:185-187` で **RG 削除の前**に対象を捕まえ、削除後に `:266` でポーリングして `:318-321` で purge する。つまり **RG を消してから拾いに行くのではなく、消す前に「これを後で purge する」と決めてから消している** — soft-delete による救済が構造的に残らないのはこのため。

purge が要るのは「同名で作り直すために soft-delete を退かす」場合だけなので、**必要になった時に明示的に `true` で呼ぶ**形にする。

`FORCE_DELETE_LOG_ANALYTICS`（`:11`、既定 `true`）も同様に既定 `false` にする（Log Analytics は持続層 = 履歴を消さない）。

### D7 — デプロイスクリプトは「成果物を置く」だけにする

`--set-env-vars` / `az functionapp config appsettings set` / `az role assignment create` / `az containerapp auth ... update` を **deploy スクリプトから撤去**し、bicep に移す。デプロイ時にしか決まらない値は例外にならない:

- **image の sha** → bicep のパラメータ（[ADR 0025](0025-deploy-container-images-by-immutable-sha-tag.md) の既存経路）
- **兄弟サービスの FQDN** → bicep のリソース参照（**ハードコードされた parameters.json の値を置き換える** — D2 参照）

撤去対象は調査で全数を特定した。**いずれも bicep 側に既に宣言があり、二重宣言になっている**:

| スクリプト | 命令的に設定しているもの | bicep 側の宣言 |
| --- | --- | --- |
| `deploy-backend.sh:189-195` | `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` | `bootstrap-core.bicep` の Function App appSettings（値は parameters.json に**ハードコード**） |
| `deploy-ai-agent.sh:72-77` | `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` / `USE_MANAGED_IDENTITY` / `LOG_LEVEL` | Container App `:1039` の env |
| `deploy-ai-agent.sh:131-155` | OpenAI User ロール付与 | `:1223` `aiAgentOpenAiRoleAssignment` |
| `deploy-ai-agent.sh:197-203` / `deploy-voicevox-wrapper.sh:107-123` | **認証ゲート**（`az containerapp auth microsoft update`） | `:1133` / `:1161` `authConfigs`（[ADR 0017](0017-container-apps-access-via-auth-gate.md)） |
| `deploy-voicevox-wrapper.sh:66-69` | `VOICEVOX_ENGINE_BASE_URL` / `LOG_LEVEL` | Container App `:1080` の env |

**認証ゲートが二重宣言だったのは新しい発見**。[ADR 0017](0017-container-apps-access-via-auth-gate.md) の「Container Apps は組み込み認証で閉じる」は守るべき資源（OpenAI の課金）に直結する門なので、**宣言とスクリプトのどちらが勝つかが曖昧なまま放置してはいけない**（[ADR 0018](0018-runtime-verification-in-the-loop.md) の「到達経路を全部数える」）。

ロール割り当てについては **[PR #292](https://github.com/yomote/mind-inbox/pull/292)（持ち主を bicep 1 本にする）が本治療**であり、本 ADR はその方針を追認する。

### D8 — GitHub Actions Variables は「欠けていたら落ちる」ようにする

**恒久的な例外は GitHub 側の設定だけ**（Azure ではないので bicep の管轄外）。対象は **4 変数・Secrets 0 個**（OIDC なので保存シークレットは無い）:

`AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`（`deploy.yml:75` の guard で 3 つ揃わないと `run=false`）/ `AUTO_DEPLOY_ENABLED`（`:94`）。

現状はどれかが欠けると deploy が**ガードで静かに skip し、run は成功のまま**になる。必要な変数の一覧をリポジトリに commit し、**欠けていたら CI が落ちる**ようにする。「成功 run = デプロイ済みではない」を毎回疑わなければならない現状（`/status` skill が長大な判定手順を持っている理由）を、構造で消す。

**warning は出ている**（`deploy.yml:78-79` の `::warning::`）が、run は緑のままなので**沈黙と同じ**。今日の教訓「『壊れている』と『確かめられなかった』が同じ 1 行に見える構造を疑う」の一例。

### D9 — 週次プロビジョンテストで宣言性を検証する（#306）

```text
バックアップ取得（持続層へ）
  → cleanup-env.sh（環境層のみ破壊）
  → provision.sh（bicep で再構築）
  → データ復元（持続層から）
  → smoke-test.sh / e2e-live
```

**段階を踏む**（最初から定期化しない）:

1. **第 1 段階**: 手動 `workflow_dispatch` で 1 回。**初回はほぼ確実に赤くなり、その差分リストが成果物**（#303 の作業リストになる）
2. **第 2 段階**: 前提（D3 の Entra 宣言化 / バックアップ・復元）を実装し、緑になるまで往復
3. **第 3 段階**: 週次で定期化し、[`watchers.json`](../../cicd/scripts/status-page/watchers.json) に 1 行足す

**第 3 段階まで行けないならこの自動化は作らない**（CLAUDE.md）。

**Cosmos のバックアップは Azure の復元機能に頼らず、持続層のストレージへ export → import する自前方式**にする。理由は (1) 毎週テストされる、(2) 壊れたら CI の赤として出る、(3) Azure の periodic backup 復元はサポート経由の経路があり自動化に向かない。

現状の確認（調査で判明）: **`backupPolicy` はどこにも宣言されていない**（`grep -rn backupPolicy cicd/` → 0 件）ので ARM の既定 periodic backup に委ねられており、**アカウント削除と一緒にバックアップも消える**。また Cosmos は **provisioned + `enableCosmosFreeTier: true`**（`bootstrap-core.bicep:1267`）で、無料枠は **1 サブスクに 1 つ**。Speech F0 と同じ制約であり、**環境層に置いて作り直すと枠の取り合いが起きる**——これも Cosmos を持続層に置く理由になる。

### D10 — [ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md)「常設」の解釈を追補する

ADR 0013 は「オンデマンド teardown をやめて常設にする」と決めた。**D9 はその字面と衝突する**ので、解釈を明示する。

> **0013 の「常設」が守っているのは「PO が触りたい時に触れる」であって、「リソースを作り直さない」ではない。** 0013 が否定したのは**使うたびに 20〜40 分待つ**構造と**翌朝には消えている**状態であり、**PO が見ていない時間帯に、計画的に、数分で戻る再構築**はこれに当たらない。

ただし**代償を明記する**: 再構築が途中で落ちると **dev は復旧されるまで無い**。これは 0013 が消したはずの「待たされる」の部分的な復活なので、

- **発火は土曜 10:00 JST**（cron `0 1 * * 6` UTC）— 2026-08-12 の design-gate で PO 決定。判断基準は「落ちたとき何時間気づかれなくてよいか」。日曜 03:00 JST 案（Issue #306 の当初案）は、落ちると月曜まで dev が無い可能性があるため退けた。**週末の日中なら気づける**
- **失敗は通常の CI 赤より強く報せる**
- **途中失敗からの再実行が冪等であること**を第 1 段階で実測する

### 受け入れる穴 — 持続層の「再構築」は検証されない

持続層を壊さないと決めた結果、**持続層の bicep だけが検証されない部分として残る**。しかもそこには**バックアップと GPG 秘密鍵**（[ADR 0045](0045-e2e-artifacts-are-secret-by-default.md) D5）が置かれる——**腐っていたことに気づくのが、いちばん困っているとき**という構造になる。

ただし穴は見た目ほど大きくない: **週次の復元が持続層の「中身」（バックアップが読める / Key Vault に届く）を毎週使う**ので、検証されないのは「**持続層を作り直せるか**」だけ。

**キリがないので今は先送りする、という明示的な判断**として記録する。将来やるなら別 Issue。

## Consequences

### Positive

- **撤収がユーザーデータとクォータを巻き込まない** — Cosmos と OpenAI が持続層にあり、`cleanup-env.sh` の射程外
- **週次で作り直してもログインが壊れない** — `uniqueName` でクライアント ID が固定、redirect URI は SWA の output に自動追従（D3）
- **「宣言できている」が毎週機械で確かめられる** — 主張ではなく赤/緑になる（D9）
- **「成功 run = デプロイ済みではない」が消える** — 変数欠落が静かな skip ではなく赤になる（D8）
- **手作業の Runbook が 1 本減る** — Entra の 5 ステップ（抜かすと「ログイン無限ループ」「ログイン後も 401」になる罠つき）が宣言になる
- **#308 の what-if 投資が守られる** — Graph を config フェーズに隔離するため、bootstrap の網は無傷（D4）

### Negative

- **移行コストが大きい** — Cosmos と OpenAI の RG 間移動はダウンタイムと再結線を伴う。一息にはやらない
- **持続層の再構築が検証されない**（上記「受け入れる穴」）
- **再構築の失敗が dev の不在に直結する**（D10）— 0013 が消したはずの「待たされる」が部分的に戻る
- **Graph 部分に what-if が効かない** — config フェーズの変更は事前確認の網から外れる。レビューと第 1 段階の実測で補う
- **RG が 1 つ増える** — 管理対象と、参照のパラメータ渡しが増える

## Pros and Cons of the Options

### Option A: 3 層 + Graph Bicep 宣言 + 週次検証（採用）

- Good, because 3 つの穴すべてに構造で答える
- Good, because 宣言性が**検証される**（他の案はどれも「宣言したつもり」を残す）
- Good, because 既存の 2 フェーズ構造（[ADR 0003](0003-two-phase-bicep.md)）の上に乗り、新しい機構を発明しない
- Bad, because 移行コストが大きく、段階を踏む必要がある
- Bad, because 再構築失敗時に dev が不在になる

### Option B: 安全弁だけ入れて現状維持

- Good, because 今日すぐ入る。事故（データ purge）は止まる
- Bad, because **宣言されていない設定はそのまま**。PO 原則に答えていない
- Bad, because 「bicep を適用しただけでは環境が結線されない」状態が続く

### Option C: 1 つの RG のまま全部宣言化する

- Good, because 層の移行コストが要らない
- Bad, because **撤収が依然としてユーザーデータを消す**。「やらない運用」で守るのは宣言的でない
- Bad, because **#306 が実行できない** — 壊すと本物のデータが消えるので、検証手段が手に入らない

### Option D: 環境を完全に使い捨て（Cosmos も毎回作り直し）

- Good, because 最も単純。層が 1 つで済む
- Bad, because **蓄積がプロダクトの中核**（Problem 中心 2 層モデル / [ADR 0007](0007-problem-centric-two-layer-domain-model.md)）。毎週データが消える環境では、蓄積から出る挙動（#102 / #244）を確認できない
- Bad, because OpenAI のクォータ再取得が毎週要る

## 動作検証（実装後に何を叩くか / [ADR 0018](0018-runtime-verification-in-the-loop.md)）

「設定したか」ではなく**振る舞い**で書く:

| 判断 | 確かめ方 | 何が言えたら緑か |
| --- | --- | --- |
| D3 Entra 宣言 | 環境層を壊して `provision.sh` → **ブラウザで dev にログイン** | ログインが通り、`/api/trpc/*` が 200。**手作業をゼロ回**挟んでいる |
| D3 クライアント ID 固定 | 再構築の前後で `appId` を比較 | **同一**。redirect URI は新 SWA ホスト名に更新されている |
| D5 アプリ登録が消えない | 再構築後に `az ad app show --id <appId>` | 存在する |
| D6 purge 安全弁 | `cleanup-env.sh` を既定で実行し、Key Vault の soft-delete を確認 | **soft-delete が残っている**（purge されていない） |
| D7 宣言だけで結線 | bicep 適用**だけ**を行い、deploy スクリプトを**通さずに** `/api/health` 相当を叩く | ai-agent / VOICEVOX の FQDN が入っており応答する |
| D2 CAE 再作成後の追従 | CAE を作り直した後、BFF の `AI_AGENT_BASE_URL` を読む | **新しい生成ドメイン**が入っている（parameters.json の古い FQDN ではない） |
| D7 認証ゲート | 再構築後、ai-agent の ingress を**トークン無しで**叩く | **401**（[ADR 0017](0017-container-apps-access-via-auth-gate.md) の門が宣言だけで閉じている） |
| D8 変数欠落 | 変数を 1 つ外した状態で deploy を回す | **run が赤**（成功のまま skip しない） |
| D9 復元 | 復元後に `problem.list` を叩く | 破壊前の Problem が戻っている |

## Links

- Issue: [#302](https://github.com/yomote/mind-inbox/issues/302) / [#303](https://github.com/yomote/mind-inbox/issues/303) / [#306](https://github.com/yomote/mind-inbox/issues/306) / 関連: [#301](https://github.com/yomote/mind-inbox/issues/301)（鍵の設置 — D1 の持続層が前提）/ [#308](https://github.com/yomote/mind-inbox/issues/308)（what-if — D4 が関係）/ [#305](https://github.com/yomote/mind-inbox/issues/305)（deploy の job 分割）
- PR: [#292](https://github.com/yomote/mind-inbox/pull/292)（D7 のロール割り当て部分の本治療）
- 一次ソース: [Graph Bicep v1.0 リファレンス](https://learn.microsoft.com/en-us/graph/templates/reference/overview) / [Graph Bicep の制約](https://learn.microsoft.com/en-us/graph/templates/bicep/limitations) / [Microsoft.Graph/applications](https://learn.microsoft.com/en-us/graph/templates/reference/applications)
- 関連 ADR: [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md)（D10 で追補）/ [0003](0003-two-phase-bicep.md) / [0025](0025-deploy-container-images-by-immutable-sha-tag.md) / [0045](0045-e2e-artifacts-are-secret-by-default.md) / [0018](0018-runtime-verification-in-the-loop.md) / [0007](0007-problem-centric-two-layer-domain-model.md)
