#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
LOCATION="${LOCATION:-japaneast}"
CONTAINERAPPS_ENV="${CONTAINERAPPS_ENV:-cae-dev-mindbox-voicevox}"
APP_NAME="${APP_NAME:-ca-dev-mindbox-voicevox}"

VOICEVOX_IMAGE="${VOICEVOX_IMAGE:-voicevox/voicevox_engine:nvidia-latest}"
TARGET_PORT="${TARGET_PORT:-50021}"

# Serverless GPU (T4) default profile
WORKLOAD_PROFILE_NAME="${WORKLOAD_PROFILE_NAME:-voicevox-gpu-t4}"
WORKLOAD_PROFILE_TYPE="${WORKLOAD_PROFILE_TYPE:-Consumption-GPU-NC8as-T4}"

# Known-safe defaults for T4 profile
CPU="${CPU:-8.0}"
MEMORY="${MEMORY:-56.0Gi}"
MIN_REPLICAS="${MIN_REPLICAS:-0}"
MAX_REPLICAS="${MAX_REPLICAS:-1}"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI (az) が見つかりません。" >&2
  exit 1
fi

echo "==> Azure login check"
az account show >/dev/null

echo "==> Ensure required extension"
az extension add --name containerapp --upgrade --only-show-errors >/dev/null

echo "==> Ensure resource group: ${RG}"
az group create \
  --name "${RG}" \
  --location "${LOCATION}" \
  --only-show-errors \
  --output none

echo "==> Ensure Container Apps environment: ${CONTAINERAPPS_ENV}"
if ! az containerapp env show -g "${RG}" -n "${CONTAINERAPPS_ENV}" --only-show-errors >/dev/null 2>&1; then
  az containerapp env create \
    --name "${CONTAINERAPPS_ENV}" \
    --resource-group "${RG}" \
    --location "${LOCATION}" \
    --only-show-errors \
    --output none
fi

echo "==> Ensure GPU workload profile: ${WORKLOAD_PROFILE_NAME} (${WORKLOAD_PROFILE_TYPE})"
if ! az containerapp env workload-profile list \
  --name "${CONTAINERAPPS_ENV}" \
  --resource-group "${RG}" \
  --query "[?name=='${WORKLOAD_PROFILE_NAME}'] | [0].name" \
  --output tsv | grep -q "${WORKLOAD_PROFILE_NAME}"; then
  az containerapp env workload-profile add \
    --name "${CONTAINERAPPS_ENV}" \
    --resource-group "${RG}" \
    --workload-profile-name "${WORKLOAD_PROFILE_NAME}" \
    --workload-profile-type "${WORKLOAD_PROFILE_TYPE}" \
    --only-show-errors \
    --output none
fi

if az containerapp show -g "${RG}" -n "${APP_NAME}" --only-show-errors >/dev/null 2>&1; then
  echo "==> Update existing Container App: ${APP_NAME}"
  az containerapp update \
    --name "${APP_NAME}" \
    --resource-group "${RG}" \
    --image "${VOICEVOX_IMAGE}" \
    --cpu "${CPU}" \
    --memory "${MEMORY}" \
    --min-replicas "${MIN_REPLICAS}" \
    --max-replicas "${MAX_REPLICAS}" \
    --workload-profile-name "${WORKLOAD_PROFILE_NAME}" \
    --set-env-vars "MALLOC_ARENA_MAX=2" \
    --only-show-errors \
    --output none
else
  echo "==> Create Container App: ${APP_NAME}"
  az containerapp create \
    --name "${APP_NAME}" \
    --resource-group "${RG}" \
    --environment "${CONTAINERAPPS_ENV}" \
    --image "${VOICEVOX_IMAGE}" \
    --target-port "${TARGET_PORT}" \
    --ingress external \
    --transport http \
    --cpu "${CPU}" \
    --memory "${MEMORY}" \
    --min-replicas "${MIN_REPLICAS}" \
    --max-replicas "${MAX_REPLICAS}" \
    --workload-profile-name "${WORKLOAD_PROFILE_NAME}" \
    --env-vars "MALLOC_ARENA_MAX=2" \
    --only-show-errors \
    --output none
fi

FQDN="$(az containerapp show \
  --name "${APP_NAME}" \
  --resource-group "${RG}" \
  --query 'properties.configuration.ingress.fqdn' \
  --output tsv)"

echo
echo "VOICEVOX on ACA is ready."
echo "URL: https://${FQDN}"
echo
echo "Frontend env example:"
echo "VITE_VOICEVOX_BASE_URL=https://${FQDN}"
echo "VITE_VOICEVOX_SPEAKER=3"
