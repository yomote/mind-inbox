# Terraform 本体と provider の版。
#
# provider は `integrations/github` (#390 が既製 OSS として選定したもの)。
# 版は 2026-08-14 時点の最新リリース v6.13.0 に合わせて `~> 6.13` (= >= 6.13, < 7.0)。
#
# ⚠️ **`.terraform.lock.hcl` はまだ無い**。エージェント環境からは
#    registry.terraform.io が egress ポリシーで 403 (実測: 2026-08-14、
#    proxy status の `connect_rejected` に `registry.terraform.io:443` が残っている)
#    ため `terraform init` を実行できず、ハッシュ固定のロックファイルを生成できない。
#    **CI (GitHub runner = プロキシ外) で最初の `terraform init` を回した結果を
#    commit するまで、provider は版でしか固定されていない** (ハッシュ検証が無い)。

terraform {
  required_version = ">= 1.5.0" # config-driven import (import ブロック) が 1.5 から

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }
}
