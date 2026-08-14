# GitHub 設定の Terraform 宣言 (未取得を埋める / CI で plan を回す)

## Trigger

- `cicd/github/terraform/` の宣言が **未取得のまま宣言から外している項目** (ruleset / auto-merge 許可 / Actions 権限 / Pages / ラベル) を埋めたいとき
- CI で `terraform plan` を回せるようにしたいが、**トークンをどこに置くか**を決める必要があるとき ([Issue #390](https://github.com/yomote/mind-inbox/issues/390) の needs-human)
- 「宣言と現実が一致しているか」を確かめたいとき

> **⛔ apply しない。** 2026-08-14 の PO 裁定は「宣言 + plan まで」。実 apply は [#387](https://github.com/yomote/mind-inbox/issues/387) の裁定 (`enforce_admins` をどちらに揃えるか) が先です。この Runbook にも apply の手順は書きません。

## Prerequisites

- **PO 本人**の GitHub アカウント (リポジトリ admin)。エージェントは管理系 API に届きません (実測 2026-08-14: `gh api repos/yomote/mind-inbox` → `403 "GitHub access is not enabled for this session."`)
- `gh` CLI にログイン済みであること、または `github-settings` workflow の device-code 認証
- Terraform 1.5 以上 (`import` ブロックのため)。`registry.terraform.io` は通るので、**`init` / `validate` / `providers lock` はエージェント環境でも回せます** (実測 2026-08-14 / Terraform 1.9.8。同日朝までは egress ポリシーで 403 でしたが、開通しました)
- **`plan` だけは回せません。** トークンが要り (Step 3 が未決定)、エージェントのトークンでは provider の設定段階で 403 になります (実測 2026-08-14。Common Issues 参照)。plan を回すのは PO のローカルか、トークンの置き場が決まったあとの GitHub runner

## Steps

### 1. 未取得の値を読む (PO 本人の権限で)

エージェントには 403 で見えない領域です。**読めた値だけを写し、読めなかったものは「未取得」と書いたまま残す**こと。

```bash
REPO=yomote/mind-inbox

# 本当の門 (ruleset)。#373 が「実際にマージを止めているのはこれ」と実測した対象
gh api "repos/$REPO/rulesets"
gh api "repos/$REPO/rulesets/<ruleset_id>"

# リポジトリ本体 (auto-merge 許可 / ブランチ自動削除 / secret scanning など)
gh api "repos/$REPO" --jq '{allow_auto_merge, delete_branch_on_merge, allow_squash_merge,
  allow_merge_commit, allow_rebase_merge, squash_merge_commit_title,
  squash_merge_commit_message, merge_commit_title, merge_commit_message,
  allow_update_branch, security_and_analysis}'

# Actions の権限 (GITHUB_TOKEN の既定権限は門の強さに直結する)
gh api "repos/$REPO/actions/permissions"
gh api "repos/$REPO/actions/permissions/workflow"

# Pages の配信元 (状況ページを止めないため、推測で書かない)
gh api "repos/$REPO/pages"

# ラベルの**全集合** (github_issue_labels は authoritative = 列挙漏れは apply で消える)
gh api --paginate "repos/$REPO/labels" --jq '.[] | {name, color, description}'
```

### 2. 読めた値を `.tf` に写す

写し先と、写すときの注意は各ファイルのコメントにあります。

| 読んだもの                           | 写し先                                                                            |
| ------------------------------------ | --------------------------------------------------------------------------------- |
| ruleset                              | `cicd/github/terraform/rulesets.tf` (テンプレをコメントで用意してある)            |
| repo 本体 / Actions / Pages / ラベル | `cicd/github/terraform/unmanaged.tf` (コメントアウトされた resource を有効化する) |

**あるべき論への変更を混ぜないこと。** ここは「現状の写し」です。直したい値があれば、写したうえで**別の PR**で直します (差分が plan に出るのが正しい姿)。

写したら `import` ブロックを `imports.tf` に足します (既存のものを「新規作成」と誤認させないため)。

### 3. トークンの置き場を決める (**未決定 / needs-human**)

`terraform plan` は GitHub の管理系 API を読むのでトークンが要ります。**まだ決まっていません。** 選択肢:

| 案                                       | 中身                                                                                                                                                                | 効くこと                                                                                      | 払うこと                                                                                                             |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **A. device-code を同じ run の中で渡す** | 既存の `cicd/scripts/github-settings/device_login.py` が取ったトークンを、同じ run で `GITHUB_TOKEN` 環境変数として terraform に渡す (provider docs が明記する経路) | **鍵をどこにも保管しない**性質がそのまま保てる。今の `github-settings.yml` の骨格を流用できる | **PO 本人の承認が毎回要る** = 自動では回らない (PR ごとの plan にはできない)                                         |
| **B. GitHub App のトークン**             | App を 1 個作り、`administration: read` 等の最小スコープを与えて `actions/create-github-app-token` で発行                                                           | 自動で回る。スコープを PAT より絞れる                                                         | **App の private key を secret に置く** → #331 と同型の露出面が増える (同一リポジトリの PR から secret を読める経路) |
| **C. admin PAT を secret に置く**        | 素直な方法                                                                                                                                                          | 自動で回る                                                                                    | **却下されている**経路 (#344 の workflow ヘッダに理由あり)。同一リポジトリの PR から読める                           |
| **D. plan は回さない**                   | fmt / validate だけを CI で回し、plan は PO が手元で回す                                                                                                            | 露出面が増えない                                                                              | **「宣言と現実が一致しているか」を CI が言えない** — 今この状態                                                      |

現在は **D**。決めるのは #390 (needs-human)。A と B は排他ではなく、「PR ごとは D、節目は A」も取れます。

### 4. ロックファイルをローカルで作って commit する

**`.terraform.lock.hcl` は CI が作ってくれません。** provider を足した / 版を上げたときは、自分で生成して commit します。

```bash
terraform -chdir=cicd/github/terraform providers lock \
  -platform=linux_amd64 -platform=linux_arm64 \
  -platform=darwin_amd64 -platform=darwin_arm64
```

(これは CI の `tf-lock` ステップがエラー時に出すのと同じコマンドです。4 platform を並べるのは、runner の `linux_amd64` だけだと PO のローカル (darwin) がハッシュ検証なしで provider を入れられてしまうため。)

commit していないと CI は次のように落ちます — **どちらも「黙って生成して緑」にはなりません**:

- ロックファイルが git に未登録なら、`tf-lock` ステップが **`init` より前に exit 1** する
- `init` は `-lockfile=readonly` で走るので、ロックファイルが無くても生成せずに落ちる (実測 2026-08-14 / Terraform 1.9.8: `Error: Provider dependency changes detected ... the lock file is read-only`)
- commit 済みの内容が同じ版から再生成される内容と 1 バイトでも違えば、`tf-lock-complete` ステップが差分を出して落とす

### 5. CI を回す

`.github/workflows/github-terraform-check.yml` が PR で自動的に走ります (`cicd/github/terraform/**` を触ったとき)。やることは **fmt / ロックファイルの確認 / init / validate まで**で、**plan は実行しません** (3 が未決定のため)。run サマリに「plan: 未実行」と理由が毎回出ます。

## Verification

- [ ] `terraform fmt -check -recursive` が通る (ローカルでも通せる。provider 取得が要らない)
- [ ] `cicd/github/terraform/.terraform.lock.hcl` が commit されていて、Step 4 のコマンドを回しても差分が出ない
- [ ] `github-terraform-check` の run が緑で、`terraform validate` のステップが成功している
- [ ] run サマリに「terraform plan: **未実行**」と理由が出ている (= 回していないものを緑と誤読させていない)
- [ ] `cicd/github/terraform/README.md` の「未取得」表が、実際に埋めた項目の分だけ減っている
- [ ] **`rulesets.tf` に resource が入るまで、「門を宣言から作り直せる」とは書かない** (#373 が指した目的未達はここ)

## Rollback

宣言を足すだけでは現実は変わりません (**apply しない**ため)。戻したいときは PR を revert すれば十分です。

万一 `terraform apply` を実行してしまった場合:

1. **すぐ止める。** `data/github-settings` の `snapshots/yomote/mind-inbox.json` (commit `4ec3bf9`) が 2026-08-12 18:42 UTC の観測です — これが戻し先の値
2. ブランチ保護は `docs/runbooks/github-settings.md` の手順で宣言 (`cicd/github/settings.yml`) から戻す
3. ruleset は Terraform の管理外なので、apply で壊れることは無い (これは今回の唯一の救い)

## Common Issues

### `could not connect to registry.terraform.io: ... Forbidden`

- **2026-08-14 朝までの症状で、現在は解消しています。** 当時はエージェント環境の egress ポリシーが `registry.terraform.io:443` を 403 で塞いでいました (`curl -sS "$HTTPS_PROXY/__agentproxy/status"` の `recentRelayFailures` に残る)。同日中に開通し、エージェント環境でも `init` / `validate` / 4 platform の `providers lock` が通ることを実測しています (2026-08-14 / Terraform 1.9.8)
- 再発したときの対処: **迂回しない**。GitHub runner (プロキシ外) で回す。手元の検証は `terraform fmt` までに限る

### `terraform plan` が `403 ... sessions are bound to their configured repositories` で落ちる

- 原因: provider が `owner` を解決するために `GET /orgs/{owner}` を叩くが、エージェントのトークンは管理系 API に届かない (実測 2026-08-14: `failed to lookup organization "yomote"`)。**plan 用のトークンが未決定** (Step 3 / #390) なので、これは想定内の停止
- 対処: PO 本人の権限で回すか、#390 が決まるまで plan を回さない。**「plan が落ちた」を「宣言が壊れている」と読み替えないこと** — 逆に「plan を回していない」を「宣言と現実が一致している」と読み替えるのも禁止。今言えているのは fmt / validate までです

### `terraform plan` が import ブロックでエラーになる

- 原因: `import` ブロックは対象が**実在すること**を前提にする。宣言したブランチが保護されていない等で対象が無いと落ちる (#390 の「未確認 1」— まだ誰も確かめていない)
- 対処: 実在しない対象の `import` ブロックを外す。**外したことを `imports.tf` にコメントで残す** (黙って消すと「なぜ無いか」が失われる)

### plan に差分が出ない項目がある

- 原因: `github_branch_protection_v3` は `allow_force_pushes` / `allow_deletions` / `required_linear_history` / `lock_branch` / `block_creations` / `allow_fork_syncing` を**管理しない** (provider docs 実測)。ruleset / repo 本体 / ラベルも未取得のため管理外
- 対処: 管理外の一覧は `cicd/github/terraform/unmanaged.tf`。**「差分なし = 一致」ではない**ことを前提に読む

## Related

- Issue: [#390](https://github.com/yomote/mind-inbox/issues/390) (棚卸し / 本件) / [#387](https://github.com/yomote/mind-inbox/issues/387) (**apply の前提**) / [#373](https://github.com/yomote/mind-inbox/issues/373) (ruleset が管理対象外) / [#389](https://github.com/yomote/mind-inbox/issues/389) / [#344](https://github.com/yomote/mind-inbox/issues/344)
- ADR: [0046](../adr/0046-environment-rebuildable-from-declaration.md) (宣言から作り直せるものにする)。マージの門の経緯は退役済み記録 [`archive/operations/merge-gate-as-required-check-and-pm-cadence.md`](../adr/archive/operations/merge-gate-as-required-check-and-pm-cadence.md) (**現行ルールではない**)
- 関連 Runbook: [`github-settings.md`](github-settings.md) (**現役の仕組み**。Terraform はまだ plan も回せていないので、点検・適用はこちら)
- 宣言: `cicd/github/terraform/` ([README](../../cicd/github/terraform/README.md))
