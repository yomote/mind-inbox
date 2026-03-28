#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "RG=$RG"
echo "DEPLOYMENT=$DEPLOYMENT"

RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$SCRIPT_DIR/deploy-backend.sh"
RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$SCRIPT_DIR/deploy-frontend.sh"
