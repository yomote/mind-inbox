# ruleset = **本当の門**。ここには resource が 1 つもありません。理由は「未取得」です。
#
# ══════════════════════════════════════════════════════════════════════════
#  未取得: このリポジトリに実在する ruleset の中身
# ══════════════════════════════════════════════════════════════════════════
#
# #373 が実測したとおり、実際にマージを止めているのは classic branch protection
# ではなく ruleset です (PR #284 のマージが `405 Repository rule violations found /
# Required status check "review-gate" is failing.` で拒否された)。
# `Repository rule violations` = ruleset による拒否。
#
# にもかかわらず、その中身を **誰も読めていません**:
#   - `cicd/github/settings.yml` に `ruleset` の語は 0 件 (自作の宣言は最初から対象外)
#   - `data/github-settings` のスナップショットも classic branch protection しか持たない
#   - エージェントからは管理系 API が 403 (実測 2026-08-14:
#     `gh api repos/yomote/mind-inbox/rulesets` →
#     403 "GitHub access is not enabled for this session.")
#
# **だから推測で書きません。** ここに「たぶんこうだろう」という ruleset を書くと、
# plan が「差分なし」に見えたり、apply が本物の門を上書きしたりします。
# 空欄のままにしておくほうが安全で、かつ「まだ管理できていない」が毎回見えます。
#
# ⚠️ **この状態で `terraform apply` をすると、門は Terraform の管理外のまま残ります。**
#    「宣言から作り直せる」(ADR 0046) は、ruleset がここに入るまで
#    **達成されていません**。#390 が「目的未達」と呼んでいるのはここ。
#
# ── 中身を取る手順 (PO 本人の権限が要る) ──────────────────────────────────
#   1. 一覧:  gh api repos/{owner}/{repo}/rulesets
#   2. 個別:  gh api repos/{owner}/{repo}/rulesets/{ruleset_id}
#      (GitHub UI からも Settings → Rules → Rulesets → ... → Export で JSON が落ちる)
#   3. 落ちた JSON を下のテンプレに写して、`import` ブロック
#      (imports.tf) に `to = github_repository_ruleset.<name>` / `id = "{repo}:{id}"`
#      を足す。**写すのは値だけで、あるべき論への変更は混ぜない**
#   手順の完全版: docs/runbooks/github-terraform.md
#
# ── 写すときのテンプレ (provider docs 実測コミット c55240a より) ────────────
#
# resource "github_repository_ruleset" "main" {
#   name        = "<ruleset の名前をそのまま>"
#   repository  = var.github_repository
#   target      = "branch"
#   enforcement = "active" # disabled / active / evaluate (evaluate は org のみ)
#
#   conditions {
#     ref_name {
#       include = ["refs/heads/main"] # or ["~DEFAULT_BRANCH"] / ["~ALL"]
#       exclude = []
#     }
#   }
#
#   # ⚠️ bypass_actors を **書き漏らすと現状より強く**なり、書きすぎると門が抜けます。
#   #    docs/team.md の「規律は破られ、機構は守られる」節は「main のブランチ保護は
#   #    Bypass list を空にする」と書いていますが、それが現実かどうかは未取得
#   #    (#387 の争点)。**実物を写すこと**。
#   # bypass_actors {
#   #   actor_id    = 0
#   #   actor_type  = "RepositoryRole" # Integration / OrganizationAdmin / RepositoryRole / Team / DeployKey
#   #   bypass_mode = "always"         # always / pull_request
#   # }
#
#   rules {
#     # 実物の JSON の rules[] を 1 つずつ写す。例:
#     # creation                = false
#     # deletion                = true
#     # non_fast_forward        = true
#     # required_linear_history = false
#     #
#     # required_status_checks {
#     #   strict_required_status_checks_policy = false
#     #   required_check {
#     #     context = "review-gate"
#     #     # integration_id = <app id>  ← 未取得なら書かない
#     #   }
#     # }
#     #
#     # pull_request {
#     #   required_approving_review_count   = 0
#     #   dismiss_stale_reviews_on_push     = false
#     #   require_code_owner_review         = false
#     #   require_last_push_approval        = false
#     #   required_review_thread_resolution = false
#     # }
#   }
# }
