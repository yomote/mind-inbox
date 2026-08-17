# mgmt-bootstrap — GitHub App + Key Vault + mgmt 層初回 apply のワンショットキット

PO の手作業 30 分+ ([`docs/runbooks/mgmt-layer-apply.md`](../../../docs/runbooks/mgmt-layer-apply.md) + GitHub App 作成 + 資格情報の置き場作り) を約 10 分に圧縮する。Issue [#387](https://github.com/yomote/mind-inbox/issues/387) (apply 前提の裁定) / [#390](https://github.com/yomote/mind-inbox/issues/390) (plan 用トークンの置き場) の実装。**手順の正典は Runbook** ([`mgmt-layer-apply.md`](../../../docs/runbooks/mgmt-layer-apply.md)) で、ここはキットの仕様と根拠だけを書く。

| ファイル                                 | 何                                                                                                                                                                                             |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`bootstrap.sh`](bootstrap.sh)           | pem パスと App ID を受け取り、Azure mgmt 層 apply → pem の Key Vault 格納 → terraform plan (import-only なら `--tf-apply` で apply) まで。信頼境界と「無いと何が静かに通るか」はスクリプト冒頭 |
| [`test_bootstrap.py`](test_bootstrap.py) | az / terraform / curl をスタブして全分岐を実測。**pem 本文と installation token が出力・argv に現れないこと**を固定                                                                            |

GitHub App の作成そのものはスクリプト化していない (下の「GitHub App を作る」が正典)。**bootstrap.sh は App の作成経路に依存せず**、pem ↔ App ID の対応・インストール先・`administration=write` を GitHub API で実測して誤設定を捕まえる。

## GitHub App を作る (手動フォーム — ここが正典)

**manifest flow は使わない。** 以前はワンクリックの manifest ページ (`create-github-app.html`) を置いていたが、**GitHub の manifest flow は 3 手目の code 変換 (`POST /app-manifests/{code}/conversions`) まで到達しないと App が登録されない** ([GitHub Docs: Registering a GitHub App from a manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) — 「You must complete all three steps in the GitHub App Manifest flow within one hour」)。変換を呼ばない設計だったため確認画面で Create しても App ができず (PO 実測 2026-08-17 / [#497](https://github.com/yomote/mind-inbox/issues/497))、**変換を組み込むのではなく manifest flow ごと捨てて手動フォームに一本化した** (PO 裁定 2026-08-17)。code 変換は pem を HTTP 応答として受ける経路で、端末・履歴への露出面が増えるため。

<https://github.com/settings/apps/new> を開き、下の値を入れて **Create GitHub App**。**書いていない欄は既定のまま**にする。

### フォームに入れる値

| 欄                               | 値                                                                |
| -------------------------------- | ----------------------------------------------------------------- |
| GitHub App name                  | `mind-inbox-settings-mgmt`                                        |
| Homepage URL                     | `https://github.com/yomote/mind-inbox`                            |
| Webhook → Active                 | **チェックを外す** (受け側エンドポイントを持たない pull 型なので) |
| Where can this app be installed? | **Only on this account** (この owner 専用 = 他人は入れられない)   |

### Repository permissions (この 2 つだけ / 他は No access のまま)

<!-- この表が App の権限集合の正典。次節「入れた権限」の根拠表と 1 対 1 で対応させる。
     両表のズレは test_bootstrap.py::test_readme_permission_tables_agree が落とす。 -->

| Repository permission | 設定値         |
| --------------------- | -------------- |
| Administration        | Read and write |
| Metadata              | Read-only      |

**写し間違いはここで捕まる** — [`bootstrap.sh`](bootstrap.sh) は installation token の権限集合をこの 2 つと**完全一致**で検証し、余分 (例: Contents を付けてしまった) / 値ちがい / 不足のいずれかがあれば**何が余分・不足かを名指しして Key Vault 格納の前に停止する**。手動フォームなので「2 つだけ」を機械が見る場所はここしかない (下限だけ見ると過剰権限 App の pem が格納されてしまう — [#498](https://github.com/yomote/mind-inbox/pull/498) Codex P2)。

### 作った後にやること (この順で)

1. App の settings ページで **App ID** を控える
2. **Install App** から `yomote/mind-inbox` (このリポジトリだけ) にインストールする
3. **Generate a private key** で pem をダウンロードする (このパスを `bootstrap.sh --pem` に渡す)
4. [`bootstrap.sh`](bootstrap.sh) を実行する (手順の正典は [`mgmt-layer-apply.md`](../../../docs/runbooks/mgmt-layer-apply.md))

> ⚠️ pem は**スクリプトに渡し終えて格納を確認したら削除する** (スクリプトが最後に手順を出す)。メール・チャット・クラウドドライブに置かない。

## App 権限の最小化と根拠

mgmt 層の Terraform ([`cicd/github/terraform/`](../../../cicd/github/terraform/)) が**実際に叩く API** から導出した。**根拠を書けない権限は入れない** — 足すときは対応する resource / API を 1 行で書き、**上の「Repository permissions」の表と同じ PR で**更新する (片方だけ直すと `test_bootstrap.py::test_readme_permission_tables_agree` が落ちる)。

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
| webhook (受信)                                | pull 型 (plan/apply を回すだけ) で受け側エンドポイントを持たない。フォームの **Active のチェックを外す**         |

### App の性質

- **Only on this account** — この owner 専用。他人はインストールできない (API 上の `public: false`)
- **インストール先は `yomote/mind-inbox` の 1 リポジトリだけ**にする (installation token も `repositories` でこのリポジトリに絞って発行する — 二重の絞り)
- pem は **Azure Key Vault (`kv-dev-mindbox` / シークレット `github-app-mgmt-private-key`)** に置く。GitHub Actions の secret には置かない — 同一リポジトリの PR から secret を読める経路 (#331 と同型) を作らないため ([`docs/runbooks/github-terraform.md`](../../../docs/runbooks/github-terraform.md) Step 3 の案 B の弱点への対処)。CI から使う経路を作るときは、KV 側の RBAC 付与という**明示の 1 手**が要る形になる

## なぜ自動化 (watchers.json) に載せないか

このキットは **PO がローカルで叩く一度きりの手動オペ**で、GitHub 側に run を残さない。watchers.json に載せると「動いた形跡が無い」と「動いていない」が区別できず偽の緑になる ([`cicd/CLAUDE.md`](../../CLAUDE.md) の claude-hooks と同じ理由)。生死はテスト (`test_bootstrap.py` → `npm run test:scripts` → required check) が見る。
