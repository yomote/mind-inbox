# config-driven import (Terraform 1.5+ の `import` ブロック)。
#
# なぜこれを使うか (#390 の state 論点 (a) 案):
#   既存の GitHub 設定は **すでに存在している**ので、Terraform に「これは新規作成
#   ではなく既存の取り込みだ」と教える必要があります。`terraform import` コマンド
#   だと state を書き換えてしまいますが、**`import` ブロックは `terraform plan` で
#   「何を import するか」まで読める** (state を変更しない)。
#   これなら state をどこにも保管せず、毎 run 空 state から plan だけ回せます。
#   自作 github-settings の「状態を持たない」規律をそのまま維持できます。
#
# ⚠️ **未検証** (このセッションでは 1 度も plan を回せていません):
#   1. 空 state + import ブロックで、対象が存在するときに plan が通るか
#   2. 宣言した対象が **存在しない**とき (例: 保護されていないブランチ) の振る舞い。
#      import ブロックは対象が無いとエラーになるはずで、その場合ここを消す必要がある
#   3. `id` に変数を埋めた式が plan 時に解決されるか (plan 時に既知の値なので
#      通る想定だが、実行して確かめていない)
#   理由: registry.terraform.io が egress ポリシーで 403 (2026-08-14 実測) のため
#   `terraform init` ができず、provider を取得できない。
#   → #390 の「未確認 1〜3」はここで潰す。CI (プロキシ外) で最初に回すのがこの経路。

import {
  to = github_branch_protection_v3.main
  id = "${var.github_repository}:main"
}

import {
  to = github_branch_protection_v3.release
  id = "${var.github_repository}:release"
}

import {
  to = github_repository_vulnerability_alerts.this
  id = var.github_repository
}

import {
  to = github_repository_dependabot_security_updates.this
  id = var.github_repository
}

# ruleset の import はここに足す (中身を写したあと):
#   import {
#     to = github_repository_ruleset.main
#     id = "${var.github_repository}:<ruleset_id>"
#   }
# ruleset_id は `gh api repos/{owner}/{repo}/rulesets` の `.id`。未取得 (rulesets.tf)。
