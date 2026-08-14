# Terraform 本体と provider の版。
#
# provider は `integrations/github` (#390 が既製 OSS として選定したもの)。
# 版は 2026-08-14 時点の最新リリース v6.13.0 に合わせて `~> 6.13` (= >= 6.13, < 7.0)。
#
# **ハッシュ固定は `.terraform.lock.hcl` が持つ**。この `version` 制約だけでは
# 「6.13 以上 7.0 未満の何か」しか言えず、同じ版を名乗る別バイナリを弾けない。
# ロックファイルは `terraform providers lock` で生成して commit 済み
# (linux_amd64 / linux_arm64 / darwin_amd64 / darwin_arm64 の 4 platform。
#  CI runner は linux_amd64、PO のローカルは darwin 系を想定)。
#
# ⚠️ **版を上げるときはロックファイルも同じ PR で更新する**:
#      terraform -chdir=cicd/github/terraform providers lock \
#        -platform=linux_amd64 -platform=linux_arm64 \
#        -platform=darwin_amd64 -platform=darwin_arm64
#    github-terraform-check.yml は `init -lockfile=readonly` で回したうえで
#    同じコマンドを実行して差分を見るので、更新を忘れると **run が落ちる**
#    (黙って通らない)。

terraform {
  required_version = ">= 1.5.0" # config-driven import (import ブロック) が 1.5 から

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }
}
