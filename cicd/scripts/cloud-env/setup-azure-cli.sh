#!/bin/bash
#
# setup-azure-cli.sh — Claude Code on the web 環境用 Setup script (正本)
#
# 目的:
#   クラウドセッション起動時に Azure CLI (az) をインストールする。
#   開発セッション内から `az login --use-device-code` で Azure を確認/操作するための前提。
#
# どこで使うか:
#   このファイルは "正本"。中身を「環境編集ダイアログ → Setup script 欄」に貼り付けて使う。
#   (UI 側はここからコピーする。手順は docs/runbooks/claude-web-azure-access.md 参照)
#
# 前提 — 同じダイアログの Network access = Custom で以下を許可しておくこと:
#   - packages.microsoft.com   … az の apt 配布元 (Trusted デフォルトで既に許可済み)
#   - management.azure.com     … az のリソース操作 (ARM)。★要追加 (無いと何も操作できない)
#   - graph.microsoft.com      … `az ad` 系 (SP/ロール照会) を使うなら。要追加
#   ※ login.microsoftonline.com は Trusted の *.microsoftonline.com でカバー済み → 追加不要
#
# 設計上の注意:
#   - aka.ms の公式ワンライナーは allowlist 外なので使わない。packages.microsoft.com 直の apt 経路にする。
#   - `az login` はここに書かない。対話型であり、トークンはキャッシュに残さない設計のため、
#     ログインはセッション内でオンデマンドに `az login --use-device-code` を実行する。
#   - setup script が非ゼロ終了するとセッションが起動失敗する点に注意 (set -e)。
#   - 環境キャッシュのため処理は ~5 分以内に収める。az 1 本なら十分収まる。
#   - setup script か egress 許可ホストを変更すると、このスクリプトは次セッションで再実行される。

set -euo pipefail

# 既に az があれば何もしない (キャッシュ再ビルド時の無駄な再インストールを防ぐ)
if command -v az >/dev/null 2>&1; then
  echo "azure-cli は既にインストール済み。スキップします。"
  exit 0
fi

# apt の HTTPS リポジトリ取得に必要な基本ツール
apt-get update
apt-get install -y ca-certificates curl gnupg

# Microsoft の署名鍵を keyring に取り込む
install -d -m 0755 /etc/apt/keyrings
curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
  | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg
chmod a+r /etc/apt/keyrings/microsoft.gpg

# Azure CLI の apt リポジトリを登録 (noble = Ubuntu 24.04)
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/azure-cli/ noble main" \
  > /etc/apt/sources.list.d/azure-cli.list

# インストール
apt-get update
apt-get install -y azure-cli

# 確認 (失敗ならここで非ゼロ終了して気づける)
az version
echo "azure-cli のインストール完了。セッション内で 'az login --use-device-code' でログインしてください。"
