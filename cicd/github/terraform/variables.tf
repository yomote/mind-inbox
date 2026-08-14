# 適用先。**既定値を置かない** — 既定値を置くと、変数を渡し忘れた実行が
# 黙って「どこか」に向かう。渡し忘れたら Terraform が止まるほうが安全。
#
# 渡し方 (どちらでも):
#   export TF_VAR_github_owner=...    TF_VAR_github_repository=...
#   terraform plan -var github_owner=... -var github_repository=...
#
# CI では `${{ github.repository_owner }}` / `${{ github.event.repository.name }}`
# から入れる (= 常に「この workflow が動いているリポジトリ」。
# cicd/github/settings.yml が敷いた「宣言に適用先を書かない」規律と同じ)。

variable "github_owner" {
  description = "適用先の GitHub アカウント / Organization。宣言に値を書かないため既定値なし。"
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", var.github_owner))
    error_message = "github_owner が GitHub のアカウント名の形をしていません。"
  }
}

variable "github_repository" {
  description = "適用先のリポジトリ名 (owner を含まない)。宣言に値を書かないため既定値なし。"
  type        = string

  # #372 の major 1 (「宣言のブランチ名が未検証のまま API パスに直結する」) と
  # 同型の事故を Terraform 側でも防ぐ。`..` や `/` が入った値を API パスに流さない。
  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,100}$", var.github_repository))
    error_message = "github_repository にリポジトリ名として使えない文字が入っています。"
  }
}
