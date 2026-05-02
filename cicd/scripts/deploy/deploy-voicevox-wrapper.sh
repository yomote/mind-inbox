#!/usr/bin/env bash
# Deploy VOICEVOX Wrapper to Azure Container Apps.
#
# 1. az acr build         — ACR Tasks でクラウドビルド（ローカル Docker 不要）
# 2. az containerapp      — Container App を作成 or 更新
# 3. az role assignment   — Managed Identity に AcrPull を付与
#
# 前提: main-bootstrap が enableAcr=true / enableVoicevoxWrapperAca=true でデプロイ済み
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
TARGET_PORT="${TARGET_PORT:-8080}"

# Role definition IDs (built-in)
ROLE_ACR_PULL="7f951dda-4ed3-4680-a7ca-43fe172d538d"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }
}
need az

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SOURCE_DIR="$ROOT_DIR/apps/services/voicevox"

# ── Resolve deployment outputs (single API call) ──────────────────────────────
echo "=== Resolving deployment outputs ==="
_OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$_OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

ACR_NAME="${ACR_NAME:-$(_val acrName)}"
CA_NAME="${CA_NAME:-$(_val voicevoxWrapperContainerAppName)}"
CAE_NAME="${CAE_NAME:-$(_val voicevoxWrapperContainerAppsEnvironmentName)}"
VOICEVOX_ENGINE_BASE_URL="${VOICEVOX_ENGINE_BASE_URL:-$(_val voicevoxBaseUrl)}"

if [[ -z "$ACR_NAME" ]]; then
  echo "ERROR: ACR name not found. Re-run bootstrap with enableAcr=true, or set ACR_NAME=<name>." >&2; exit 1
fi
if [[ -z "$CA_NAME" ]]; then
  echo "ERROR: Container App name not found. Re-run bootstrap with enableVoicevoxWrapperAca=true, or set CA_NAME=<name>." >&2; exit 1
fi
if [[ -z "$CAE_NAME" ]]; then
  echo "ERROR: Container Apps Environment name not found. Set CAE_NAME=<name>." >&2; exit 1
fi
if [[ -z "$VOICEVOX_ENGINE_BASE_URL" ]]; then
  echo "ERROR: VOICEVOX_ENGINE_BASE_URL not found. Re-run bootstrap with enableVoicevoxAca=true, or set VOICEVOX_ENGINE_BASE_URL=<url>." >&2; exit 1
fi

IMAGE="${ACR_NAME}.azurecr.io/voicevox-wrapper:${IMAGE_TAG}"
echo "ACR:        $ACR_NAME"
echo "CA:         $CA_NAME"
echo "CAE:        $CAE_NAME"
echo "Image:      $IMAGE"
echo "Engine URL: $VOICEVOX_ENGINE_BASE_URL"

# ── Build ─────────────────────────────────────────────────────────────────────
echo ""
echo "=== Building image with ACR Tasks ==="
az acr build \
  --registry "$ACR_NAME" \
  --image "voicevox-wrapper:${IMAGE_TAG}" \
  "$SOURCE_DIR"

# ── Deploy Container App (create or update) ───────────────────────────────────
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
    --system-assigned \
    --registry-server "${ACR_NAME}.azurecr.io" \
    --registry-identity system \
    --env-vars "${ENV_VARS[@]}" \
    --query 'properties.configuration.ingress.fqdn' -o tsv)"
else
  echo "Updating Container App '$CA_NAME'..."
  FQDN="$(az containerapp update \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --image "$IMAGE" \
    --set-env-vars "${ENV_VARS[@]}" \
    --system-assigned \
    --query 'properties.configuration.ingress.fqdn' -o tsv)"
fi

# ── Role assignments ──────────────────────────────────────────────────────────
echo ""
echo "=== Assigning roles ==="

PRINCIPAL_ID="$(az containerapp show -g "$RG" -n "$CA_NAME" \
  --query 'identity.principalId' -o tsv)"

if [[ -z "$PRINCIPAL_ID" ]]; then
  echo "ERROR: principalId is empty. Managed Identity may not be assigned to the Container App." >&2
  exit 1
fi
echo "  Principal ID: $PRINCIPAL_ID"

_assign_role() {
  local role="$1" scope="$2" label="$3"
  if az role assignment list \
      --assignee "$PRINCIPAL_ID" --role "$role" --scope "$scope" \
      --query '[0].id' -o tsv 2>/dev/null | grep -q .; then
    echo "  $label: already assigned."
  else
    az role assignment create \
      --assignee-object-id "$PRINCIPAL_ID" \
      --assignee-principal-type ServicePrincipal \
      --role "$role" \
      --scope "$scope" \
      --output none
    echo "  $label: assigned."
  fi
}

ACR_ID="$(az acr show -g "$RG" -n "$ACR_NAME" --query id -o tsv)"
_assign_role "$ROLE_ACR_PULL" "$ACR_ID" "AcrPull"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Done ==="
echo "Endpoint: https://${FQDN}"
echo "Health:   https://${FQDN}/health"
echo "Docs:     https://${FQDN}/docs"
