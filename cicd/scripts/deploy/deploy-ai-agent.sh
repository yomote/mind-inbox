#!/usr/bin/env bash
# Deploy AI Agent to Azure Container Apps.
#
# 1. az acr build         — ACR Tasks でクラウドビルド（ローカル Docker 不要）
# 2. az containerapp      — Container App を作成 or 更新
# 3. az role assignment   — Managed Identity に OpenAI User / AcrPull を付与
#
# 前提: main-bootstrap が enableAcr=true / enableAiAgentAca=true でデプロイ済み
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
TARGET_PORT="${TARGET_PORT:-8000}"

# Role definition IDs (built-in)
ROLE_ACR_PULL="7f951dda-4ed3-4680-a7ca-43fe172d538d"
ROLE_OPENAI_USER="5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }
}
need az

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SOURCE_DIR="$ROOT_DIR/apps/services/ai-agent"

# ── Resolve deployment outputs (single API call) ──────────────────────────────
echo "=== Resolving deployment outputs ==="
_OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$_OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

ACR_NAME="${ACR_NAME:-$(_val acrName)}"
CA_NAME="${CA_NAME:-$(_val aiAgentContainerAppName)}"
CAE_NAME="${CAE_NAME:-$(_val aiAgentContainerAppsEnvironmentName)}"
OPENAI_ENDPOINT="${OPENAI_ENDPOINT:-$(_val openAiEndpoint)}"
OPENAI_DEPLOYMENT="${OPENAI_DEPLOYMENT:-$(_val openAiDeploymentName)}"
OPENAI_ACCOUNT_NAME="${OPENAI_ACCOUNT_NAME:-$(_val openAiAccountName)}"

if [[ -z "$ACR_NAME" ]]; then
  echo "ERROR: ACR name not found. Re-run bootstrap with enableAcr=true, or set ACR_NAME=<name>." >&2; exit 1
fi
if [[ -z "$CA_NAME" ]]; then
  echo "ERROR: Container App name not found. Re-run bootstrap with enableAiAgentAca=true, or set CA_NAME=<name>." >&2; exit 1
fi
if [[ -z "$CAE_NAME" ]]; then
  echo "ERROR: Container Apps Environment name not found. Set CAE_NAME=<name>." >&2; exit 1
fi
if [[ -z "$OPENAI_ENDPOINT" ]]; then
  echo "ERROR: OPENAI_ENDPOINT not found. Re-run bootstrap with enableOpenAi=true, or set OPENAI_ENDPOINT=<url>." >&2; exit 1
fi

IMAGE="${ACR_NAME}.azurecr.io/ai-agent:${IMAGE_TAG}"
echo "ACR:      $ACR_NAME"
echo "CA:       $CA_NAME"
echo "CAE:      $CAE_NAME"
echo "Image:    $IMAGE"
echo "Endpoint: $OPENAI_ENDPOINT"

# ── Build ─────────────────────────────────────────────────────────────────────
echo ""
echo "=== Building image with ACR Tasks ==="
az acr build \
  --registry "$ACR_NAME" \
  --image "ai-agent:${IMAGE_TAG}" \
  "$SOURCE_DIR"

# ── Deploy Container App (create or update; FQDN captured from output) ────────
echo ""
echo "=== Deploying Container App ==="

ENV_VARS=(
  "AZURE_OPENAI_ENDPOINT=${OPENAI_ENDPOINT}"
  "AZURE_OPENAI_DEPLOYMENT=${OPENAI_DEPLOYMENT:-gpt-4o}"
  "USE_MANAGED_IDENTITY=true"
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
  # MI を system-assigned に（既に有効なら no-op）
  az containerapp identity assign \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --system-assigned \
    --output none
  # ACR registry を identity 経由で設定（既に設定済みなら no-op）
  az containerapp registry set \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --server "${ACR_NAME}.azurecr.io" \
    --identity system \
    --output none
  FQDN="$(az containerapp update \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --image "$IMAGE" \
    --set-env-vars "${ENV_VARS[@]}" \
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
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "$role" \
    --scope "$scope" \
    --output none 2>&1 | grep -v "already exists" || true
  echo "  $label: done."
}

ACR_ID="$(az acr show -g "$RG" -n "$ACR_NAME" --query id -o tsv)"
_assign_role "$ROLE_ACR_PULL" "$ACR_ID" "AcrPull"

if [[ -n "$OPENAI_ACCOUNT_NAME" ]]; then
  OPENAI_ID="$(az cognitiveservices account show -g "$RG" -n "$OPENAI_ACCOUNT_NAME" --query id -o tsv 2>/dev/null || true)"
  if [[ -n "$OPENAI_ID" ]]; then
    _assign_role "$ROLE_OPENAI_USER" "$OPENAI_ID" "Cognitive Services OpenAI User"
  else
    echo "  WARNING: OpenAI account '$OPENAI_ACCOUNT_NAME' not found. Skipping OpenAI role." >&2
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Done ==="
echo "Endpoint: https://${FQDN}"
echo "Health:   https://${FQDN}/health"
echo "Docs:     https://${FQDN}/docs"
