#!/usr/bin/env bash
# クラウドセッション環境のセットアップスクリプト (claude.ai/code の環境ダイアログに貼る内容)。
#
# なぜ要るか (2026-08-10 の実測):
#   毎セッション、VOICEVOX の image (2GB) を pull し直し、依存を入れ直していた。
#   公式仕様では **このスクリプトの実行後にファイルシステムがスナップショットされ、
#   次のセッションはそこから始まる** (pull 済み image も含む)。つまり一度書けば
#   待ち時間が消える。費用はゼロ。
#   > "New sessions start with your dependencies, tools, and Docker images already on disk"
#
# 制約 (公式ドキュメント):
#   - **exit 0 必須**。非ゼロで終わるとセッションが起動しない → すべて `|| true`
#   - **5 分以内**。超えるとキャッシュが作られない → 独立なものは `&` で並列、最後に `wait`
#   - ネットワークは環境の access level に従う (npm / PyPI / apt は Trusted で通る)
#   - スナップショットは **ディスクの状態だけ**。起動したプロセスは残らない
#     (dockerd や VOICEVOX コンテナは毎セッション起こす — SKILL.md / SessionStart の担当)
#   - 約 7 日、またはスクリプト・許可ドメインを変えると再実行される
#
# リポジトリのファイルには依存しない (clone 前に走る可能性があるため自己完結)。
set -u

echo "=== cloud-setup: start ==="

# --- docker: image を先に取っておく -------------------------------------
# 報告会 (briefing skill) が使う VOICEVOX。ここでの pull が一番効く。
(
  dockerd >/tmp/dockerd-setup.log 2>&1 &
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker pull voicevox/voicevox_engine:cpu-latest || true
  echo "voicevox image: $(docker images -q voicevox/voicevox_engine:cpu-latest || echo none)"
) &
PID_DOCKER=$!

# --- CLI: プリインストールされていないもの -------------------------------
# gh: GitHub CLI (status-page の生成スクリプトが使う)
# @openai/codex: 技術レビュー担当 (ADR 0035 D4/D5)。MCP としても使う
(
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq gh >/dev/null 2>&1 || true
  npm install -g @openai/codex >/dev/null 2>&1 || true
  echo "gh: $(command -v gh || echo none) / codex: $(command -v codex || echo none)"
) &
PID_CLI=$!

wait $PID_DOCKER $PID_CLI 2>/dev/null || true

echo "=== cloud-setup: done ==="
# 依存 (npm ci / pnpm install / uv sync) はリポジトリの中身に依存するので
# SessionStart hook 側の担当。ここではやらない (clone 前に走る可能性がある)。
exit 0
