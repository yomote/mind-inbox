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
# ⚠️ **未検証** (まだ 1 度も plan を回せていません):
#   1. 空 state + import ブロックで、対象が存在するときに plan が通るか
#   2. 宣言した対象が **存在しない**とき (例: 保護されていないブランチ) の振る舞い。
#      import ブロックは対象が無いとエラーになるはずで、その場合ここを消す必要がある
#   3. `id` に変数を埋めた式が plan 時に解決されるか (plan 時に既知の値なので
#      通る想定だが、実行して確かめていない)
#
#   理由: **plan 用のトークンが未決定** (#390 needs-human)。plan は GitHub の管理系
#   API を読むのでトークンが要り、どこに置くかが決まっていません。エージェントの
#   トークンでは provider の設定段階で落ちます (実測 2026-08-14: `terraform plan` →
#   `failed to lookup organization "yomote": GET https://api.github.com/orgs/yomote:
#   403 This GitHub API path is not available: sessions are bound to their
#   configured repositories.`)。選択肢は docs/runbooks/github-terraform.md の Step 3。
#
#   **provider が取れないことは、もう理由ではありません。** registry.terraform.io は
#   2026-08-14 朝までは egress ポリシーで 403 でしたが同日中に開通し、エージェント
#   環境でも `terraform init` / `validate` / 4 platform の `providers lock` が通ることを
#   実測しました (2026-08-14 / Terraform 1.9.8)。つまり **止まっているのは plan だけ**。
#   → #390 の「未確認 1〜3」は、トークンが決まった経路で plan を回して潰す。

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
