# mgmt-bootstrap — GitHub App + Key Vault + mgmt 層初回 apply のワンショットキット

PO の手作業 30 分+ ([`docs/runbooks/mgmt-layer-apply.md`](../../../docs/runbooks/mgmt-layer-apply.md) + GitHub App 作成 + 資格情報の置き場作り) を約 10 分に圧縮する。Issue [#387](https://github.com/yomote/mind-inbox/issues/387) (apply 前提の裁定) / [#390](https://github.com/yomote/mind-inbox/issues/390) (plan 用トークンの置き場) の実装。**手順の正典は Runbook** ([`mgmt-layer-apply.md`](../../../docs/runbooks/mgmt-layer-apply.md)) で、ここはキットの仕様と根拠だけを書く。

| ファイル                                           | 何                                                                                                                                                                                             |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`create-github-app.html`](create-github-app.html) | manifest flow のワンクリックページ。**manifest の正典はこの HTML の textarea** (二重管理しない)                                                                                                |
| [`bootstrap.sh`](bootstrap.sh)                     | pem パスと App ID を受け取り、Azure mgmt 層 apply → pem の Key Vault 格納 → terraform plan (import-only なら `--tf-apply` で apply) まで。信頼境界と「無いと何が静かに通るか」はスクリプト冒頭 |
| [`test_bootstrap.py`](test_bootstrap.py)           | az / terraform / curl をスタブして全分岐を実測。**pem 本文と installation token が出力・argv に現れないこと**を固定                                                                            |

## App 権限の最小化と根拠

mgmt 層の Terraform ([`cicd/github/terraform/`](../../../cicd/github/terraform/)) が**実際に叩く API** から導出した。**根拠を書けない権限は入れない** — 足すときは対応する resource / API を 1 行で書き、`test_bootstrap.py` の manifest 固定テストと同じ PR で更新する。

### 入れた権限 (2 つだけ)

| 権限                    | なぜ要るか (1 行ずつ)                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `administration: write` | `github_branch_protection_v3` ×2 が `GET/PUT /repos/{o}/{r}/branches/{main,release}/protection` を叩く (Administration 権限の API)   |
| 〃                      | `github_repository_vulnerability_alerts` が `GET/PUT /repos/{o}/{r}/vulnerability-alerts` を叩く (同上)                              |
| 〃                      | `github_repository_dependabot_security_updates` が `GET/PUT /repos/{o}/{r}/automated-security-fixes` を叩く (同上)                   |
| 〃                      | 将来 `rulesets.tf` を埋めたとき `/repos/{o}/{r}/rulesets` も Administration 権限 — 追加の権限拡張なしで「本当の門」(#373) まで写せる |
| `metadata: read`        | 全 App 必須の土台。provider が owner / repo を解決する (`GET /users/{owner}` 等)。read しか選べない                                  |

> `administration` を `read` にすると plan までは回るが apply で落ちる。App は apply のためのものなので `write` にし、**変更の門は権限ではなく plan の中身で守る** — `bootstrap.sh` は add/change/destroy が 1 つでもある plan を apply しない (#387 の裁定を機械で迂回しないため)。

### 入れなかった権限 (根拠が書けない = 入れない)

| 権限                                          | 入れない理由                                                                                                     |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `contents`                                    | Terraform の管理対象にリポジトリ内容の読み書きが無い (branch protection は Administration API)                   |
| `issues`                                      | ラベルは未管理 ([`unmanaged.tf`](../../../cicd/github/terraform/unmanaged.tf))。管理し始めるときに根拠つきで追加 |
| `pages` / `actions` / `secrets` / `workflows` | いずれも未管理 (同上)。宣言に入った時点で追加を検討                                                              |
| webhook (受信)                                | pull 型 (plan/apply を回すだけ) で受け側エンドポイントを持たない。`hook_attributes` を書かず**無効**で作る       |

### App の性質

- **public: false** — この owner 専用。他人はインストールできない
- **インストール先は `yomote/mind-inbox` の 1 リポジトリだけ**にする (installation token も `repositories` でこのリポジトリに絞って発行する — 二重の絞り)
- pem は **Azure Key Vault (`kv-dev-mindbox` / シークレット `github-app-mgmt-private-key`)** に置く。GitHub Actions の secret には置かない — 同一リポジトリの PR から secret を読める経路 (#331 と同型) を作らないため ([`docs/runbooks/github-terraform.md`](../../../docs/runbooks/github-terraform.md) Step 3 の案 B の弱点への対処)。CI から使う経路を作るときは、KV 側の RBAC 付与という**明示の 1 手**が要る形になる

## なぜ自動化 (watchers.json) に載せないか

このキットは **PO がローカルで叩く一度きりの手動オペ**で、GitHub 側に run を残さない。watchers.json に載せると「動いた形跡が無い」と「動いていない」が区別できず偽の緑になる ([`cicd/CLAUDE.md`](../../CLAUDE.md) の claude-hooks と同じ理由)。生死はテスト (`test_bootstrap.py` → `npm run test:scripts` → required check) が見る。
