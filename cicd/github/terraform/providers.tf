# provider の設定。
#
# 🔑 **トークンをここにも tfvars にも書かない**。provider は `GITHUB_TOKEN`
#    環境変数を読む (provider docs `index.md`: "Explicit Token — `token` argument
#    or `GITHUB_TOKEN` environment variable")。既存の
#    `cicd/scripts/github-settings/device_login.py` が device-code で取ったトークンを
#    同じ run の中で `GITHUB_TOKEN` に載せれば、「鍵をどこにも保管しない」性質は
#    そのまま保てる (#390 が provider ドキュメントで確認済み)。
#    **どのトークンを CI でどう渡すかは未決定** — 選択肢は
#    docs/runbooks/github-terraform.md、決めるのは #390 (needs-human)。
#
# `owner` を明示するのは、トークンからの自動判定に任せると
# **別アカウントのリポジトリに向く事故**が起きるため。ただし値 (アカウント名) は
# 変数の既定値ではなく **環境変数 `TF_VAR_github_owner` で渡す** —
# `cicd/github/settings.yml` が「適用先リポジトリすら宣言に書かない」規律を
# 敷いているので、それを崩さない。

provider "github" {
  owner = var.github_owner
}
