# 宣言していないものと、その理由。**resource は 1 つもありません。**
#
# 「黙って対象外にしない」ための置き場です (settings.yml の `unmanaged` と同じ思想)。
# ここに名前が出ているものは **Terraform の管理下に無い** = plan に差分が出ない
# = 誰かが変えても気づけない、という状態です。
#
# ══════════════════════════════════════════════════════════════════════════
#  1. リポジトリ本体の設定 (github_repository) — 宣言しない
# ══════════════════════════════════════════════════════════════════════════
#
# `github_repository` は **リポジトリ全体を 1 resource で持つ**設計です。宣言に
# 書かなかった引数は provider の既定値になり、apply でそちらへ倒れます。
# 半分が未取得の今これを宣言すると、**#377 (「PUT が送らなかった項目が既定値に
# 戻って、誰も意図していない値で上書きされた」) を Terraform で再演します**。
#
# ── 読めた値 (2026-08-14、code search API 経由。管理系 API ではない) ────────
#   default_branch = "main" / visibility = "public" / archived = false
#   has_issues = true / has_wiki = true / has_projects = true
#   has_discussions = false / has_downloads = false
#   allow_forking = true / is_template = false
#   web_commit_signoff_required = false
#   has_pages = true (※ **Pages が有効なことだけ**。中身は下記 3 で未取得)
#
# ── 未取得 (管理系 API が 403 なので **推測で書かない**) ──────────────────
#   allow_auto_merge            ← auto-merge 許可。#253 / #327 の議論の前提そのもの
#   delete_branch_on_merge      ← マージ後のブランチ自動削除
#   allow_squash_merge / allow_merge_commit / allow_rebase_merge
#   squash_merge_commit_title / squash_merge_commit_message
#   merge_commit_title / merge_commit_message
#   allow_update_branch
#   security_and_analysis (secret_scanning / secret_scanning_push_protection)
#     ※ スナップショットでは両方 enabled だが、**この resource 経由でしか
#        宣言できない**ので、repository ごと未宣言 = 管理外
#
#   取り方: gh api repos/{owner}/{repo} --jq '{allow_auto_merge, delete_branch_on_merge,
#           allow_squash_merge, allow_merge_commit, allow_rebase_merge,
#           squash_merge_commit_title, squash_merge_commit_message,
#           merge_commit_title, merge_commit_message, allow_update_branch,
#           security_and_analysis}'
#
# resource "github_repository" "this" {
#   name = var.github_repository
#   # ⚠️ **全項目を実測値で埋めてから**有効化すること。1 つでも空けると既定値に倒れる。
# }
#
# ══════════════════════════════════════════════════════════════════════════
#  2. Actions の権限 — 宣言しない (未取得)
# ══════════════════════════════════════════════════════════════════════════
#
# こちらは repository と違って **独立した resource** なので、値さえ取れれば
# 単体で宣言できます (repository resource ごと待つ必要はない)。
#
#   github_actions_repository_permissions   … enabled / allowed_actions
#     取り方: gh api repos/{owner}/{repo}/actions/permissions
#   github_workflow_repository_permissions  … default_workflow_permissions
#                                             (GITHUB_TOKEN の既定 read/write) /
#                                             can_approve_pull_request_reviews
#     取り方: gh api repos/{owner}/{repo}/actions/permissions/workflow
#
# `default_workflow_permissions` は **門の強さに直結**します (#331: 判定 job の
# 定義が PR 側から来る問題と同じ面)。未取得のまま「異常なし」と書かないこと。
#
# resource "github_actions_repository_permissions" "this" {
#   repository      = var.github_repository
#   enabled         = true
#   allowed_actions = "all" # ← 実測値で置き換える
# }
#
# resource "github_workflow_repository_permissions" "this" {
#   repository                       = var.github_repository
#   default_workflow_permissions     = "read" # ← 実測値で置き換える
#   can_approve_pull_request_reviews = false  # ← 実測値で置き換える
# }
#
# ══════════════════════════════════════════════════════════════════════════
#  3. GitHub Pages — 宣言しない (未取得)
# ══════════════════════════════════════════════════════════════════════════
#
# 状況ページ (<https://yomote.github.io/mind-inbox/status/>) の配信面。
# `has_pages = true` は読めましたが、**build_type / source.branch / source.path /
# cname / public は未取得**です。gh-pages ブランチから配っているのか Actions
# ビルドなのかを推測で書くと、apply で配信が止まります。
#
#   取り方: gh api repos/{owner}/{repo}/pages
#
# resource "github_repository_pages" "this" {
#   repository = var.github_repository
#   build_type = "legacy" # ← 実測値で置き換える (legacy / workflow)
#   source {
#     branch = "gh-pages" # ← 実測値で置き換える
#     path   = "/"
#   }
# }
#
# ══════════════════════════════════════════════════════════════════════════
#  4. ラベル — 宣言しない (集合が未取得)
# ══════════════════════════════════════════════════════════════════════════
#
# `github_issue_labels` は provider docs が明記するとおり **authoritative** です
# (「This resource is authoritative」)。= 宣言に無いラベルを **消します**。
#
# 個別のラベルは読めます (実測 2026-08-14: `stream:factory` → color `ededed` /
# description 空)。しかし **ラベルの全集合を列挙する経路が無い**
# (`gh api repos/{owner}/{repo}/labels` は 403。MCP には 1 件取得しかない)。
# 集合が未取得のまま authoritative な resource を書くと、**列挙し損ねたラベルが
# apply で消え、既存 Issue から剥がれます**。これは元に戻せません。
#
#   取り方: gh api --paginate repos/{owner}/{repo}/labels --jq '.[] | {name, color, description}'
#
# 非 authoritative な `github_issue_label` を 1 個ずつ書く手もありますが、
# それは「宣言から作り直せる」(ADR 0046) を満たしません (宣言に無いラベルが
# 生き残るので、宣言と現実が一致しているかを plan で言えない)。
# **集合を取ってから github_issue_labels で一括宣言する**のが本筋。
#
# resource "github_issue_labels" "this" {
#   repository = var.github_repository
#   # label { name = "stream:factory"  color = "ededed" description = "" }
#   # … 実測した全ラベルを列挙する。1 つでも漏らすと apply で消える。
# }
#
# ══════════════════════════════════════════════════════════════════════════
#  5. branch protection のうち、resource はあるが管理から外した項目
# ══════════════════════════════════════════════════════════════════════════
#
#   require_signed_commits (署名コミットの強制) — main / release の両方
#     → `github_branch_protection_v3` の引数には**ある**が、**現在値が未取得**。
#       スナップショットに `required_signatures` キーが無く、自作機構も
#       `settings_diff.py:70` の `NOT_COMPARED` で比較対象外と明記している。
#       provider の既定値 false で現実を上書きしないよう、
#       `branch_protection.tf` の `lifecycle.ignore_changes` で外している。
#       = **plan に差分が出ない** (誰かが有効/無効にしても気づけない)。
#
#       取り方: gh api repos/{owner}/{repo}/branches/{branch}/protection/required_signatures --jq '.enabled'
#       実値を取れたら `ignore_changes` を消して明示する。
#
#   ※ 引数そのものが無い 6 項目 (allow_force_pushes / lock_branch など) は
#      `branch_protection.tf` 冒頭を参照。そちらは resource 側の穴。
#
# ══════════════════════════════════════════════════════════════════════════
#  6. provider に対応が無いもの
# ══════════════════════════════════════════════════════════════════════════
#
#   code_scanning_default_setup (CodeQL の default setup)
#     → provider に resource が無い (#390 実測: `default-setup` / `DefaultSetup`
#       が provider の Go ファイル 435 個に 0 件)。観測値は configured。
#       `gh api repos/{repo}/code-scanning/default-setup` を叩く自作コードとして残る。
#
#   allow_weakening 相当の「保護を弱める操作は明示許可がないと実行しない」ゲート
#     → Terraform に同等物なし。`terraform plan` の差分を人が読む運用に落ちる。
#       **これは移行で失う機能**です (README.md の対応表に明記)。
