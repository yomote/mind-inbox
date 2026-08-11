#!/usr/bin/env bash
# BFF + frontend の成果物デプロイのみ。Container Apps (voicevox-wrapper / ai-agent) は
# 呼ばない — ai-agent MI への OpenAI ロール付与 (deploy-ai-agent.sh が唯一の所有者 / #262)
# もここでは走らないため、初回構築はこのスクリプト単体ではなく provision.sh を使うこと。
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "RG=$RG"
echo "DEPLOYMENT=$DEPLOYMENT"

RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$SCRIPT_DIR/deploy-backend.sh"
RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$SCRIPT_DIR/deploy-frontend.sh"
