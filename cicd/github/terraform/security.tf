# セキュリティ機能の **現状の写し**。出どころは branch_protection.tf と同じ
# スナップショット (`data/github-settings` の `4ec3bf9` / 2026-08-12 18:42 UTC)。
#
# ── 宣言できるもの ─────────────────────────────────────────────────────────
#   dependabot_alerts           → github_repository_vulnerability_alerts
#   dependabot_security_updates → github_repository_dependabot_security_updates
#
# ── 宣言できないもの (推測で書かない) ───────────────────────────────────────
#   secret_scanning / secret_scanning_push_protection
#     → provider では `github_repository` の `security_and_analysis` ブロックの中。
#       `github_repository` は **リポジトリ全体を 1 resource で持つ**ので、
#       未取得の項目 (allow_auto_merge 等) が provider の既定値で上書きされます。
#       #377 (「PUT が送らなかった項目が既定値に戻る」) と同じ事故の作り方なので、
#       repository resource ごと宣言していません。詳細は unmanaged.tf。
#       **観測値は両方 enabled** ですが、Terraform の管理下にはありません。
#   code_scanning_default_setup
#     → **provider に対応 resource が無い** (#390 実測: provider の `github/` 配下
#       435 個の Go ファイルに `default-setup` / `DefaultSetup` が 0 件)。
#       観測値は configured。ここは `gh api repos/{repo}/code-scanning/default-setup`
#       を叩く自作コードとして残ります (退役計画は README.md の対応表)。
#
# ⚠️ 適用の順序に依存関係があります: security updates は alerts が有効でないと
#    有効にできない。Terraform には `depends_on` で明示します
#    (暗黙の依存が無い別 resource なので、書かないと並列に投げられる)。

resource "github_repository_vulnerability_alerts" "this" {
  repository = var.github_repository
  enabled    = true # 観測値: enabled
}

resource "github_repository_dependabot_security_updates" "this" {
  repository = var.github_repository
  enabled    = true # 観測値: enabled

  depends_on = [github_repository_vulnerability_alerts.this]
}
