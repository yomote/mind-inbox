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

# ── Deploy Container App (robust create/update; ACR pull via MI) ──────────────
# 鶏卵問題の回避（deploy-voicevox-wrapper.sh と同じ）: system-assigned MI + プライベート
# ACR では create 時点で MI がまだ AcrPull を持たず、--registry-identity system で
# プライベートイメージを pull しようとすると create がハングする。まず public の
# プレースホルダ画像 + system MI で作成 → AcrPull 付与 → registry を MI 経由に設定して
# 本物の ACR イメージへ update する。
echo ""
echo "=== Deploying Container App ==="

ENV_VARS=(
  "AZURE_OPENAI_ENDPOINT=${OPENAI_ENDPOINT}"
  "AZURE_OPENAI_DEPLOYMENT=${OPENAI_DEPLOYMENT:-gpt-4o}"
  "USE_MANAGED_IDENTITY=true"
  "LOG_LEVEL=INFO"
)

# min-replicas: 既定 1（warm 維持）。scale-to-zero(0) だと初回リクエストで cold-start が走り、
# 相談の1発目が ~1-2 分固まって見える（実測: ボタンが無反応に見える）。UX 優先で常時 1 レプリカ。
# コスト最優先なら AI_AGENT_MIN_REPLICAS=0 で従来の scale-to-zero に戻せる（on-demand env は down 時 ¥0）。
AI_AGENT_MIN_REPLICAS="${AI_AGENT_MIN_REPLICAS:-1}"
PLACEHOLDER_IMAGE="mcr.microsoft.com/k8se/quickstart:latest"

CA_EXISTS="$(az containerapp show -g "$RG" -n "$CA_NAME" --query name -o tsv 2>/dev/null || true)"

if [[ -z "$CA_EXISTS" ]]; then
  echo "Creating Container App '$CA_NAME' (placeholder image; ACR は identity+role 付与後に配線)..."
  az containerapp create \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --environment "$CAE_NAME" \
    --image "$PLACEHOLDER_IMAGE" \
    --ingress external \
    --target-port "$TARGET_PORT" \
    --transport http \
    --min-replicas "$AI_AGENT_MIN_REPLICAS" \
    --max-replicas 3 \
    --cpu 0.5 \
    --memory 1Gi \
    --system-assigned \
    --env-vars "${ENV_VARS[@]}" \
    --output none
else
  echo "Container App '$CA_NAME' exists; ensuring system-assigned identity..."
  az containerapp identity assign \
    --resource-group "$RG" \
    --name "$CA_NAME" \
    --system-assigned \
    --output none
fi

# ── Role assignments（本物のイメージを pull する前に AcrPull を付与）──────────────
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

# ── ACR を MI 経由に配線し、本物のイメージへ切り替え ─────────────────────────────
echo ""
echo "=== Wiring ACR (identity) + deploying real image ==="
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
  --min-replicas "$AI_AGENT_MIN_REPLICAS" \
  --max-replicas 3 \
  --set-env-vars "${ENV_VARS[@]}" \
  --query 'properties.configuration.ingress.fqdn' -o tsv)"

# ── Smoke: /chat が実 LLM で応答するか（cold-start + モデル呼び出しで時間がかかる）──────
# AI_AGENT_SMOKE=false でスキップ可。既定は実行して「本当に喋る」を確認する。
if [[ "${AI_AGENT_SMOKE:-true}" == "true" ]]; then
  echo ""
  echo "=== Smoke 1/2: warm-up GET /health（scale-to-zero からの cold-start を先に済ませる）==="
  # cold-start を /health で先に踏んでおく（replica 起動 + kernel ロード）。/chat の時間予算を実処理に使う。
  H_CODE="$(curl -sS -m 120 -o /dev/null -w '%{http_code}' "https://${FQDN}/health" 2>/dev/null || echo 000)"
  echo "health http=$H_CODE"

  echo ""
  echo "=== Smoke 2/2: POST /chat（gpt-5-mini は推論モデルで遅め。workflow が複数回 LLM を呼ぶ）==="
  SMOKE_JSON="$(mktemp)"
  # 推論モデル + 複数呼び出し + cold-start 残りを見て時間を厚めに（300s）。500 等は body に出るので必ず表示。
  SMOKE_CODE="$(curl -sS -m 300 -o "$SMOKE_JSON" -w '%{http_code}' \
    -X POST "https://${FQDN}/chat" \
    -H 'Content-Type: application/json' \
    -d '{"session_id":"smoke","message":"こんにちは。これは接続テストです。ひとことで返してください。"}' \
    2>/dev/null || echo 000)"
  echo "smoke /chat http=$SMOKE_CODE"
  echo "--- reply (先頭 800 文字) ---"
  head -c 800 "$SMOKE_JSON" 2>/dev/null; echo
  rm -f "$SMOKE_JSON"
  if [[ "$SMOKE_CODE" != "200" ]]; then
    echo "ERROR: /chat が 200 を返しません（http=$SMOKE_CODE）。上の body に 500 の例外詳細が出ていれば LLM 呼び出しエラー、" >&2
    echo "       000 なら 300s でも応答なし（推論が長すぎ/ハング）。ai-agent ログ（Log Analytics）も参照。" >&2
    exit 1
  fi

  # ── 統合 smoke（診断）: BFF(func) → ai-agent の疎通を func 直叩きで確認 ──────────
  # func の trpc は authLevel=anonymous なので SWA 認証を迂回して BFF→ai-agent 経路だけ検証できる。
  # 非致命（ai-agent デプロイ自体は成功済み）。full では ai-agent が backend より先なので func 側は
  # 旧 BFF の可能性がある点に注意（あくまで現時点の疎通確認用）。
  FUNC_HOST="$(_val functionAppDefaultHostname)"
  if [[ -n "$FUNC_HOST" ]]; then
    echo ""
    echo "=== 統合 smoke: BFF(func) → ai-agent（POST /api/trpc/consultation.start 直叩き, anonymous）==="
    BFF_JSON="$(mktemp)"
    BFF_CODE="$(curl -sS -m 200 -o "$BFF_JSON" -w '%{http_code}' \
      -X POST "https://${FUNC_HOST}/api/trpc/consultation.start?batch=1" \
      -H 'Content-Type: application/json' \
      -d '{"0":{"concern":"接続テストです。ひとことで返してください。"}}' \
      2>/dev/null || echo 000)"
    echo "BFF consultation.start http=$BFF_CODE （func=$FUNC_HOST）"
    echo "--- body (先頭 1000 文字) ---"
    head -c 1000 "$BFF_JSON" 2>/dev/null; echo
    rm -f "$BFF_JSON"
    if [[ "$BFF_CODE" != "200" ]]; then
      echo "WARN: BFF 経由が 200 になりません（http=$BFF_CODE）。SWA↔func↔ai-agent のどこかで詰まっている可能性。" >&2
    fi
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Done ==="
echo "Endpoint: https://${FQDN}"
echo "Health:   https://${FQDN}/health"
echo "Docs:     https://${FQDN}/docs"
