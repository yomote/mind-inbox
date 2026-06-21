#!/bin/bash
#
# setup.sh — Claude Code on the web 環境用 Setup script (正本)
#
# 役割:
#   クラウドセッション起動時に必要なツール類をまとめて入れる「単一エントリポイント」。
#   この 1 ファイルの中身をそのまま「環境編集ダイアログ → Setup script 欄」に貼り付けて使う。
#   ツールが増えたら下に install_xxx 関数 (= セクション) を追記して、末尾の実行リストに足す。
#   (現状は Azure CLI のみ。今後 Azure 以外も入りうる前提で汎用名にしている)
#
# 共通の注意:
#   - 非ゼロ終了するとセッションが起動失敗する (set -e)。落ちて困らない処理は `|| true`。
#   - 環境キャッシュのため全体で ~5 分以内に収める。
#   - Network access か Setup script を変更すると次セッションで再実行される。
#   - 各セクションは「既に入っていればスキップ」を入れて再実行を安くする。
#   - 必要な egress 許可は各セクションのコメントに明記する (Network access = Custom で追加)。
#
# 手順全体: docs/runbooks/claude-web-azure-access.md

set -euo pipefail

# =============================================================================
# [section] Azure CLI
# -----------------------------------------------------------------------------
# 用途: 開発セッションから `az login --use-device-code` で Azure を確認/操作する。
# 要 egress (Network access = Custom):
#   - packages.microsoft.com   … apt 配布元 (Trusted デフォルトで許可済み)
#   - management.azure.com     … リソース操作 (ARM)。★要追加 (無いと操作不可)
#   - graph.microsoft.com      … `az ad` 系を使うなら。要追加
#   ※ login.microsoftonline.com は Trusted の *.microsoftonline.com でカバー済み
# 注意:
#   - aka.ms ワンライナーは allowlist 外。packages.microsoft.com 直 apt にする。
#   - `az login` はここに書かない (対話型・トークンはセッション限り)。
# =============================================================================
install_azure_cli() {
  if command -v az >/dev/null 2>&1; then
    echo "[azure-cli] 既にインストール済み。スキップ。"
    return 0
  fi
  echo "[azure-cli] インストール開始..."
  # ベースイメージに既存の PPA (deadsnakes/ondrej 等 = ppa.launchpadcontent.net) があり、
  # それが egress allowlist 外で 403 になる。Azure とは無関係なので update 全体は失敗させず
  # 続行する (必要な ubuntu 本体 / packages.microsoft.com は通る)。az 入手可否は末尾で検証。
  apt-get update || true
  apt-get install -y ca-certificates curl gnupg
  install -d -m 0755 /etc/apt/keyrings
  curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg
  chmod a+r /etc/apt/keyrings/microsoft.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/azure-cli/ noble main" \
    > /etc/apt/sources.list.d/azure-cli.list
  apt-get update || true  # 同上: 既存 PPA の 403 で update 全体を落とさない
  apt-get install -y azure-cli
  az version
  echo "[azure-cli] 完了。'az login --use-device-code' でログイン可能。"
}

# =============================================================================
# 新しいツールを足すときの雛形 (コピーして使う):
#
# # [section] <tool 名>
# # 用途: ...
# # 要 egress: ...
# install_<tool>() {
#   command -v <bin> >/dev/null 2>&1 && { echo "[<tool>] skip"; return 0; }
#   ...
# }
# =============================================================================

# =============================================================================
# 実行するセクション (足したら下に 1 行追記する)
# =============================================================================
install_azure_cli

echo "setup.sh 完了。"
