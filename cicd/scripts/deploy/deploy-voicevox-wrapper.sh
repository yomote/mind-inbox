#!/usr/bin/env bash
# Deploy VOICEVOX Wrapper to Azure Container Apps.
#
# 1. az containerapp      — Container App を作成 or 更新（ghcr の事前ビルド image を差し替え）
#
# image は build-images.yml が main マージ時に ghcr へ push 済み（#67 / ADR 0013）。
# このスクリプトはビルドしない（`az acr build` 廃止）: 既存タグを Container App に差し替えるだけ。
# **IMAGE_TAG=sha-<full-sha> の明示指定を推奨** (#107): 既定の :latest は既存 CA の更新で
# 「同一 image 文字列の update = ARM 的に変更なし → 新 revision を作らない no-op」になり、
# ghcr 側の latest が進んでいても反映されない。CD (deploy.yml) は build-images の
# 直近成功 run から sha タグを自動解決して渡す。
# ghcr の image は public 前提でプル（registry secret 不要）。公開手順: docs/runbooks/ghcr-images.md
#
# 前提: main-bootstrap が enableVoicevoxWrapperAca=true でデプロイ済み / ghcr に image が push 済み
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
TARGET_PORT="${TARGET_PORT:-8080}"

# ghcr の image 座標（ghcr.io/<owner>/<repo>/voicevox-wrapper）。owner/repo は環境変数で上書き可能。
IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io}"
IMAGE_REPO="${IMAGE_REPO:-yomote/mind-inbox}"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }
}
need az

# shellcheck source=lib/containerapp-image.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerapp-image.sh"

# ── Resolve deployment outputs (single API call) ──────────────────────────────
echo "=== Resolving deployment outputs ==="
_OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$_OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

CA_NAME="${CA_NAME:-$(_val voicevoxWrapperContainerAppName)}"
CAE_NAME="${CAE_NAME:-$(_val voicevoxWrapperContainerAppsEnvironmentName)}"
VOICEVOX_ENGINE_BASE_URL="${VOICEVOX_ENGINE_BASE_URL:-$(_val voicevoxBaseUrl)}"

if [[ -z "$CA_NAME" ]]; then
  echo "ERROR: Container App name not found. Re-run bootstrap with enableVoicevoxWrapperAca=true, or set CA_NAME=<name>." >&2; exit 1
fi
if [[ -z "$CAE_NAME" ]]; then
  echo "ERROR: Container Apps Environment name not found. Set CAE_NAME=<name>." >&2; exit 1
fi
if [[ -z "$VOICEVOX_ENGINE_BASE_URL" ]]; then
  echo "ERROR: VOICEVOX_ENGINE_BASE_URL not found. Re-run bootstrap with enableVoicevoxAca=true, or set VOICEVOX_ENGINE_BASE_URL=<url>." >&2; exit 1
fi

IMAGE="${IMAGE:-${IMAGE_REGISTRY}/${IMAGE_REPO}/voicevox-wrapper:${IMAGE_TAG}}"
echo "CA:         $CA_NAME"
echo "CAE:        $CAE_NAME"
echo "Image:      $IMAGE"
echo "Engine URL: $VOICEVOX_ENGINE_BASE_URL"

# ── Deploy Container App (create or update) ───────────────────────────────────
# ghcr の public image を pull（--registry-server 不要）。ビルドは build-images.yml 側で完了済み。
echo ""
echo "=== Deploying Container App ==="

ENV_VARS=(
  "VOICEVOX_ENGINE_BASE_URL=${VOICEVOX_ENGINE_BASE_URL}"
  "LOG_LEVEL=INFO"
)

CA_EXISTS="$(az containerapp show -g "$RG" -n "$CA_NAME" --query name -o tsv 2>/dev/null || true)"

if [[ -z "$CA_EXISTS" ]]; then
  echo "Creating Container App '$CA_NAME'..."
  FQDN="$(az containerapp create \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --environment "$CAE_NAME" \
    --image "$IMAGE" \
    --ingress external \
    --target-port "$TARGET_PORT" \
    --transport http \
    --min-replicas 0 \
    --max-replicas 3 \
    --cpu 0.5 \
    --memory 1Gi \
    --env-vars "${ENV_VARS[@]}" \
    --query 'properties.configuration.ingress.fqdn' -o tsv)"
else
  echo "Updating Container App '$CA_NAME'..."
  warn_if_noop_image_update "$RG" "$CA_NAME" "$IMAGE"
  FQDN="$(az containerapp update \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --image "$IMAGE" \
    --set-env-vars "${ENV_VARS[@]}" \
    --query 'properties.configuration.ingress.fqdn' -o tsv)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""

# ── Auth gate (ADR 0017 / #86) ────────────────────────────────────────────────
# Container Apps の組み込み認証 (Entra) を冪等に適用する。IP 許可リストは使わない —
# Functions (Consumption) の実送信元 IP は possibleOutboundIpAddresses にすら
# 含まれないことが実測で確認されており (2026-08-08、runbook 参照)、IP 方式は成立しない。
GATE_CLIENT_ID="${GATE_CLIENT_ID:-$(_val containerAppsGateClientId)}"
if [[ -n "$GATE_CLIENT_ID" ]]; then
  echo ""
  echo "=== Applying auth gate (audience: api://$GATE_CLIENT_ID) ==="
  TENANT_ID="$(az account show --query tenantId -o tsv)"
  az containerapp auth microsoft update -g "$RG" -n "$CA_NAME" \
    --client-id "$GATE_CLIENT_ID" \
    --issuer "https://login.microsoftonline.com/$TENANT_ID/v2.0" \
    --allowed-token-audiences "api://$GATE_CLIENT_ID,$GATE_CLIENT_ID" \
    --yes --output none
  az containerapp auth update -g "$RG" -n "$CA_NAME" \
    --enabled true --unauthenticated-client-action Return401 --output none
  echo "  Auth gate: enabled (unauthenticated -> 401)"
else
  echo "WARN: containerAppsGateClientId が未設定 — 認証の門なしで公開されます (#86)。" >&2
  echo "      runbook (container-apps-auth-gate.md) の手順で gate app を作成し parameters.json に設定してください。" >&2
fi

echo "=== Done ==="
echo "Endpoint: https://${FQDN}"
echo "Health:   https://${FQDN}/health"
echo "Docs:     https://${FQDN}/docs"
