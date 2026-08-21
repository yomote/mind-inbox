# cicd/github/terraform — GitHub 設定の Terraform 宣言 (plan まで)

**現行の GitHub 設定を [`integrations/github`](https://registry.terraform.io/providers/integrations/github) provider で宣言し直したもの。** [Issue #390](https://github.com/yomote/mind-inbox/issues/390) の A-1 (「`github-settings` 5,221 行は provider の再実装」) に対する着工分です。

> **⛔ apply しない。** 2026-08-14 の PO 裁定は「**宣言 + plan まで**」。
>
> **`enforce_admins` は 2026-08-21 に裁定が出ました (#387 / 案 A)** — あるべき姿は `true` (管理者バイパス無効)。**それを書いたのは [`cicd/github/settings.yml`](../settings.yml) の方**で、実際に PUT されるのもそちらです (`github-settings` workflow の mode=apply)。ここは「現状の写し」なので **観測値の `false` のまま**にしてあります (理由は [`branch_protection.tf`](branch_protection.tf) の冒頭コメント — 意図を写すと plan が現実との差分を出さなくなるうえ、mgmt-bootstrap の import-only apply が差分を理由に拒否されます)。**この写しを `true` にするのは、apply 後に現実が `true` になったのを観測してから**です。
>
> **例外は 1 つ** (2026-08-16 の PO 裁定によるキット化): [`cicd/scripts/mgmt-bootstrap/`](../../scripts/mgmt-bootstrap/README.md) が **plan が import-only (`0 to add, 0 to change, 0 to destroy`) のときだけ** PO の手で apply を実行できます。import-only の apply は state への取り込みだけで **GitHub の実設定を 1 つも変更しない**ため、上の前提と両立します。差分が 1 つでもあればキットは適用を拒否します。

---

## ここに書いてあるのは「現状の写し」であって「あるべき姿」ではない

宣言 (`cicd/github/settings.yml`) は**意図**を書く場所ですが、**このディレクトリは現実を写す**場所です。あるべき論への変更を混ぜていません — 混ぜると plan が現実との差分を出さなくなり、「今どうなっているか」が分からなくなるからです。

### 出どころ

| 何                                                                | どこから取ったか                                                                               | いつの値                 |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------ |
| branch protection (main / release)                                | データブランチ `data/github-settings` の `snapshots/yomote/mind-inbox.json` (commit `4ec3bf9`) | **2026-08-12 18:42 UTC** |
| security (Dependabot / secret scanning / code scanning)           | 同上                                                                                           | **2026-08-12 18:42 UTC** |
| リポジトリ本体の一部 (visibility / has\_\* / default_branch など) | GitHub code search API (管理系 API ではない)                                                   | 2026-08-14               |

**今日この瞬間の値を読み直してはいません。** エージェントはリポジトリ管理系 API に届きません (実測 2026-08-14: `gh api repos/yomote/mind-inbox` → `403 "GitHub access is not enabled for this session."`)。上の 2 日前のスナップショットが、この環境から取れる最新の観測です。

---

## 未取得 (= 宣言から外したもの)

**推測で書かないと決めた**ので、読めなかったものは宣言していません。名前だけは必ず出します (黙って対象外にしない)。

| 対象                                                               | 状態                                                                                                                                                                                                              | 置き場                                         |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| **ruleset (本当の門)**                                             | **未取得** — 実際にマージを止めているのはこれ ([#373](https://github.com/yomote/mind-inbox/issues/373) が `405 Repository rule violations found` を実測)                                                          | [`rulesets.tf`](rulesets.tf)                   |
| auto-merge 許可 / ブランチ自動削除 / squash 設定 / secret scanning | **未取得** — `github_repository` は全体で 1 resource なので、半分未取得のまま宣言すると残りが既定値に倒れる (#377 の再演)                                                                                         | [`unmanaged.tf`](unmanaged.tf)                 |
| Actions 権限 (`allowed_actions` / `default_workflow_permissions`)  | **未取得**                                                                                                                                                                                                        | [`unmanaged.tf`](unmanaged.tf)                 |
| Pages の配信元 (build_type / source)                               | **未取得** (`has_pages = true` だけ読めた)                                                                                                                                                                        | [`unmanaged.tf`](unmanaged.tf)                 |
| ラベル                                                             | **集合が未取得** — `github_issue_labels` は authoritative なので、列挙漏れが apply で消える                                                                                                                       | [`unmanaged.tf`](unmanaged.tf)                 |
| `code_scanning_default_setup`                                      | **provider に対応 resource が無い**                                                                                                                                                                               | [`unmanaged.tf`](unmanaged.tf)                 |
| branch protection の 6 項目 (`allow_force_pushes` 等)              | **`github_branch_protection_v3` の引数に無い** — 現状が全て REST 既定値 `false` なので写しとしては一致するが、plan には出ない                                                                                     | [`branch_protection.tf`](branch_protection.tf) |
| `require_signed_commits` (署名コミットの強制)                      | **未取得** — スナップショットに `required_signatures` が無く、自作機構も比較対象外 (`settings_diff.py:70`)。既定値 `false` で現実を上書きしないよう `lifecycle.ignore_changes` で管理から外した (= plan に出ない) | [`branch_protection.tf`](branch_protection.tf) |

取り方 (PO 本人の権限が要る) は各ファイルのコメントと [`docs/runbooks/github-terraform.md`](../../../docs/runbooks/github-terraform.md)。

---

## ファイル構成

| ファイル                                       | 中身                                                                                                                                                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`versions.tf`](versions.tf)                   | Terraform / provider の版 (`integrations/github ~> 6.13`)                                                                                                                                                   |
| `.terraform.lock.hcl`                          | provider の**ハッシュ固定**。`version` 制約だけでは同じ版を名乗る別バイナリを弾けない。版を上げるときは `terraform providers lock` で同じ PR で更新する (CI は `-lockfile=readonly` なので、忘れると落ちる) |
| [`providers.tf`](providers.tf)                 | provider 設定。**トークンは `GITHUB_TOKEN` 環境変数から** (ファイルに書かない)                                                                                                                              |
| [`variables.tf`](variables.tf)                 | 適用先 (owner / repo)。**既定値なし** — 宣言に適用先を書かない規律を継承                                                                                                                                    |
| [`branch_protection.tf`](branch_protection.tf) | main / release の classic branch protection (現状の写し)                                                                                                                                                    |
| [`security.tf`](security.tf)                   | Dependabot alerts / security updates (現状の写し)                                                                                                                                                           |
| [`imports.tf`](imports.tf)                     | 既存設定を取り込む `import` ブロック (state を保管しない方式の要)                                                                                                                                           |
| [`rulesets.tf`](rulesets.tf)                   | **本当の門。未取得のため resource なし** + 写し方のテンプレ                                                                                                                                                 |
| [`unmanaged.tf`](unmanaged.tf)                 | 宣言していないものと、その理由                                                                                                                                                                              |

---

## 検証の状態 (できたこと / できなかったこと)

| 検証                                    | 結果                                                                                                                                                                      |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `terraform fmt -check -diff -recursive` | ✅ 通した (Terraform v1.9.8 / 2026-08-14)                                                                                                                                 |
| `terraform providers lock` (4 platform) | ✅ 通した — `integrations/github v6.13.0` を `.terraform.lock.hcl` に固定 (HashiCorp partner 署名 key `38027F80D7FD5FB2` を検証)                                          |
| `terraform init -backend=false`         | ✅ 通した (2026-08-14。同日中に `registry.terraform.io` の egress が開通した)                                                                                             |
| `terraform validate`                    | ✅ 通した — ただし **警告 1 件**: `github_branch_protection_v3.main` の `contexts` は provider が非推奨とし `checks` を勧めている (`branch_protection.tf:85`)。移行は別途 |
| `terraform plan`                        | ❌ **未検証: 認証 (トークンの置き場) が未決定** — #390 に needs-human として残っている                                                                                    |
| `import` ブロックの振る舞い             | ❌ **未検証** (#390 の「未確認 1〜3」はそのまま残っている)                                                                                                                |

上の ✅ と同じものを、PR ごとに [`github-terraform-check.yml`](../../../.github/workflows/github-terraform-check.yml) が回します。**ロックファイルが未登録なら、その workflow は非ゼロ終了で落ちます** — 無いまま `init` すると terraform は黙って生成して成功してしまい、「ハッシュ固定されていないのに CI は緑」が通ってしまうため。

> `fmt` は HCL をパースするので構文エラーは捕まえますが、**resource の引数名が正しいか・型が合うかは見ていません**。「fmt が通った = 正しい」ではありません。
>
> `validate` が見るのは**宣言が provider のスキーマに合っているか**だけです。**「宣言と GitHub の現実が一致しているか」は plan を回すまで何も言えません。**

---

## 既存の自作機構 (github-settings) の退役計画

**まだ 1 行も消していません。** これは対応表であって、実行は plan が通ってからです。

| 今あるもの                                                                 |                              行数 | 移行先                                                  | 判定                                                                                                                                            |
| -------------------------------------------------------------------------- | --------------------------------: | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `cicd/scripts/github-settings/settings_diff.py`                            |                             1,098 | `terraform plan` (provider の差分計算)                  | **移る**                                                                                                                                        |
| `cicd/scripts/github-settings/sync.py`                                     |                               354 | `terraform plan` / `terraform apply tfplan`             | **移る**                                                                                                                                        |
| `cicd/scripts/github-settings/test_settings_diff.py`                       |                             1,042 | —                                                       | **消える** (判定が provider 側に移るため)                                                                                                       |
| `cicd/scripts/github-settings/test_sync.py`                                |                               440 | —                                                       | **消える**                                                                                                                                      |
| `cicd/github/settings.yml`                                                 |                               131 | `cicd/github/terraform/*.tf`                            | **移る** (ただし宣言の性格が「意図」から「現状の写し」に変わる — 差分は #387 の裁定後に埋める)                                                  |
| `plan_digest` / `decide_apply` (「PO が見た差分しか適用しない」)           |       (`settings_diff.py` の一部) | `terraform plan -out=tfplan` → `terraform apply tfplan` | **移る** (Terraform の標準機能)                                                                                                                 |
| `allow_weakening` (保護を弱める操作の明示許可)                             |                            (同上) | —                                                       | **⚠️ 失う** — Terraform に同等物なし。plan の差分を人が読む運用に落ちる。(ただし #377 では現状の弱化ゲートも機能していなかった)                 |
| `cicd/scripts/github-settings/device_login.py`                             |                               422 | —                                                       | **残る** — device-code でトークンを取る部分。provider は `GITHUB_TOKEN` を読むので、そのまま前段に置ける                                        |
| `cicd/scripts/github-settings/test_device_login.py`                        |                               378 | —                                                       | **残る**                                                                                                                                        |
| `cicd/scripts/github-settings/write-snapshot.sh`                           |                               128 | —                                                       | **残る (要判断)** — 「事実」の履歴 (`git log -p` が変更の記録になる) は state を持たない方式では別に価値がある。plan 出力で代替できるかは未検証 |
| `code_scanning_default_setup` の読み書き                                   | 約 30 (`settings_diff.py` の一部) | —                                                       | **残る** — provider 非対応                                                                                                                      |
| ドリフト Issue の起票 / クローズ (`.github/workflows/github-settings.yml`) |                        (workflow) | —                                                       | **残る** — plan の差分を Issue に積む形に置き換える                                                                                             |

**消える見込み: 約 2,900 行 / 残る見込み: 約 950 行。** ([#390](https://github.com/yomote/mind-inbox/issues/390) の見積り「約 4,800 行消える」との差は、ruleset と repo 設定が未取得で移せない分と、Issue 起票・スナップショットの周辺が残る分。)

---

## 関連

- Issue: [#390](https://github.com/yomote/mind-inbox/issues/390) (棚卸し・本件の親) / [#387](https://github.com/yomote/mind-inbox/issues/387) (**apply の前提**) / [#373](https://github.com/yomote/mind-inbox/issues/373) (ruleset が管理対象外) / [#389](https://github.com/yomote/mind-inbox/issues/389) (宣言が repo 全体を持っていない) / [#377](https://github.com/yomote/mind-inbox/issues/377) [#372](https://github.com/yomote/mind-inbox/issues/372) (自作由来の欠陥) / [#344](https://github.com/yomote/mind-inbox/issues/344) (github-settings 本体)
- ADR: [0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) (宣言から作り直せるものにする)。マージの門の経緯は退役済み記録 [`archive/operations/merge-gate-as-required-check-and-pm-cadence.md`](../../../docs/adr/archive/operations/merge-gate-as-required-check-and-pm-cadence.md) (**現行ルールではない**)
- Runbook: [`docs/runbooks/github-terraform.md`](../../../docs/runbooks/github-terraform.md)
- 一次情報: provider の `docs/resources/` (実測コミット `c55240a` / 2026-08-11)
