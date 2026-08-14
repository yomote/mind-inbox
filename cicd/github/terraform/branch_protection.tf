# classic branch protection (REST) の **現状の写し**。
#
# ⚠️ ここは「あるべき姿」ではなく **2026-08-12 18:42 UTC に観測された現実** です。
#    出どころ: データブランチ `data/github-settings` の
#    `snapshots/yomote/mind-inbox.json` (commit `4ec3bf9`)。
#    エージェントはリポジトリ管理系 API に 403 で届かない (実測 2026-08-14:
#    `GET /repos/yomote/mind-inbox` → 403 "GitHub access is not enabled for this
#    session.") ため、**今日この瞬間の値を読み直してはいません**。観測は 2 日前の値。
#
# ⚠️ **あるべき論への変更を混ぜていません** (#390 の着工条件)。特に
#    `enforce_admins = false` は [#387](https://github.com/yomote/mind-inbox/issues/387)
#    が「apply すると測定済みの門バイパスが復活する」と指摘して needs-human に
#    なっている当の値です。**直すのは #387 の裁定後**。ここで黙って true にすると
#    「現状の写し」ではなくなり、plan が現実との差分を出さなくなります。
#
# ── provider が **管理しない** 項目 (#373 / #389 が指す穴の一部) ─────────────
# `github_branch_protection_v3` の引数は provider docs (実測コミット c55240a、
# `docs/resources/branch_protection_v3.md`) の全量が
#   repository / branch / enforce_admins / require_signed_commits /
#   require_conversation_resolution / required_status_checks /
#   required_pull_request_reviews / restrictions
# だけです。スナップショットにある次の 6 項目は **この resource では宣言できません**:
#   allow_deletions / allow_force_pushes / allow_fork_syncing /
#   block_creations / lock_branch / required_linear_history
# 現実はいずれも `false` = REST の既定値なので **写しとしては一致**しますが、
# **plan に出ないので、後から誰かが true に変えても差分として気づけません**。
# (GraphQL 版 `github_branch_protection` はこのうち 4 つを持ちますが、
#  `restrict_pushes.blocks_creations` の既定が `true` で、宣言しないと
#  現状 `false` を **変えてしまう**。現状維持を優先して v3 を採っています。)
#
# ── 未取得のため触れていないもの ─────────────────────────────────────────
# **ruleset (本当の門)** は別ファイル `rulesets.tf` を参照。classic branch
# protection をいくら宣言しても #373 が実測した `405 Repository rule violations`
# の門は再現されません。
#
# **`require_signed_commits` (署名コミットの強制) は未取得です。**
# 写し元のスナップショット (`snapshots/yomote/mind-inbox.json`) に
# `required_signatures` キーがそもそも無く、自作機構も
# `cicd/scripts/github-settings/settings_diff.py:70` の `NOT_COMPARED` で
# 「v1 の適用対象外」と明記して**比較していません**。= 現在値が false だとは
# 確認できていません。
#
# provider の既定値は `false` です。ここで引数を省いたまま import して apply
# すると、**実際には署名必須が有効でも「false への変更」として適用され、保護が
# 黙って弱まります** (#377 「PUT が送らなかった項目が既定値に戻った」の再演)。
# 観測していない値を既定値で上書きしないため、下の 2 resource では
# `lifecycle.ignore_changes` でこの引数を**管理対象から外して**います。
#
# ⚠️ これは「安全に倒した」のであって「一致を保証した」のではありません。
#    管理外なので **plan に差分が出ません** (誰かが変えても気づけない)。
#    上記 6 項目と同じ穴です。`unmanaged.tf` にも名前を出しています。
# ⚠️ `ignore_changes` が効くのは**更新時だけ**です。この 2 resource が import
#    ではなく**新規作成**になった場合 (= 保護が消えている場合) は既定値 false で
#    作られます。その状況は保護そのものが失われているということなので、plan の
#    `will be created` を人が見て止めること。
#
#   取り方 (PO 本人の権限が要る):
#     gh api repos/{owner}/{repo}/branches/main/protection/required_signatures --jq '.enabled'
#     gh api repos/{owner}/{repo}/branches/release/protection/required_signatures --jq '.enabled'
#   実値を取れたら `ignore_changes` を消して `require_signed_commits = <実値>` を
#   明示すること (それが本来の「現状の写し」)。

resource "github_branch_protection_v3" "main" {
  repository = var.github_repository
  branch     = "main"

  # 観測値: false。#387 の裁定対象 (上のコメント)。
  enforce_admins = false

  # 観測値: false。宣言 (settings.yml) も false で一致している。
  require_conversation_resolution = false

  required_status_checks {
    # 観測値: false (= マージ前に base へ追いつくことを必須にしない)。
    strict = false

    # ⚠️ `contexts` は provider で **DEPRECATED** (後継は `checks` =
    #    "context:app_id" の形)。app_id は管理系 API が 403 で読めず **未取得**の
    #    ため、推測で書かずに `contexts` のまま写しています。app_id を取れたら
    #    `checks` に移す (それまでは deprecation warning が出ます)。
    #
    # 値は job の `name:` そのもの。ここを直すときは test.yml の job 名と同じ PR で。
    # (#373 の 2: 過去に job 名だけ変わって context が行方不明になり、
    #  protection が実質効かなくなっていた)
    contexts = [
      "lint-and-build",
      "review-gate",
      "test (L0 / L1+L2 / L3 / L3-real)",
    ]
  }

  required_pull_request_reviews {
    dismiss_stale_reviews           = false
    require_code_owner_reviews      = false
    require_last_push_approval      = false
    required_approving_review_count = 0
  }

  # 観測値: restrictions = "none" (= push できる人・チーム・App の制限なし)。
  # provider では **ブロックを書かないこと**が「制限なし」の表現。空ブロックを
  # 書くと「誰も push できない」制限を作ってしまうので書きません。
  # なお `restrictions` は organization 所有リポジトリでしか使えない
  # (provider docs)。このリポジトリは個人所有。

  lifecycle {
    # `require_signed_commits` は未取得 (冒頭コメント参照)。既定値 false で
    # 現実を上書きしないため管理対象から外す。実値を取れたら消して明示する。
    ignore_changes = [require_signed_commits]
  }
}

resource "github_branch_protection_v3" "release" {
  repository = var.github_repository
  branch     = "release"

  enforce_admins                  = false
  require_conversation_resolution = false

  # 観測値: required_status_checks = null (= required check なし)。
  # ブロックを書かないことが null の表現。
  # 理由 (settings.yml のコメントより): release への PR では test.yml が走らないので、
  # required にすると永久 pending になる。リリースの門は release-gate + 人間 (ADR 0019)。

  required_pull_request_reviews {
    dismiss_stale_reviews           = false
    require_code_owner_reviews      = false
    require_last_push_approval      = false
    required_approving_review_count = 0
  }

  lifecycle {
    # main と同じ理由 (未取得の署名設定を既定値 false で上書きしない)。
    ignore_changes = [require_signed_commits]
  }
}
