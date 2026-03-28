#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
CONFIG_DEPLOYMENT="${CONFIG_DEPLOYMENT:-main-config}"
BOOTSTRAP_DEPLOYMENT="${BOOTSTRAP_DEPLOYMENT:-main-bootstrap}"
DELETE_ENTRA_APP="${DELETE_ENTRA_APP:-true}"
NO_WAIT="${NO_WAIT:-true}"
PURGE_DELETED_KEYVAULTS="${PURGE_DELETED_KEYVAULTS:-true}"
PURGE_WAIT_SECONDS="${PURGE_WAIT_SECONDS:-900}"

declare -a KEYVAULT_TARGETS=()

usage() {
  cat <<'EOF'
Delete an environment resource group and optionally remove the auto-created Entra app registration.

Environment variables:
  RG                 Resource group name (default: rg-dev-mind-inbox)
  CONFIG_DEPLOYMENT  Config deployment name to inspect for auth outputs (default: main-config)
  BOOTSTRAP_DEPLOYMENT Bootstrap deployment name fallback (default: main-bootstrap)
  DELETE_ENTRA_APP   true|false. Delete auto-created Entra app registration first (default: true)
  NO_WAIT            true|false. Pass --no-wait to az group delete (default: true)
  PURGE_DELETED_KEYVAULTS true|false. Purge soft-deleted Key Vaults after RG deletion (default: true)
  PURGE_WAIT_SECONDS Max seconds to wait for RG deletion / Key Vault deleted state (default: 900)

Examples:
  RG=rg-dev-mind-inbox ./scripts/env/cleanup-env.sh
  RG=rg-dev-mind-inbox DELETE_ENTRA_APP=false ./scripts/env/cleanup-env.sh
  RG=rg-dev-mind-inbox PURGE_DELETED_KEYVAULTS=true ./scripts/env/cleanup-env.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: az command not found" >&2
  exit 1
fi

az account show >/dev/null

get_output_value() {
  local deployment_name="$1"
  local output_name="$2"

  az deployment group show \
    -g "$RG" \
    -n "$deployment_name" \
    --query "properties.outputs.${output_name}.value" \
    -o tsv 2>/dev/null || true
}

resolve_auto_created_app() {
  local deployment_name auto_created app_object_id app_client_id

  for deployment_name in "$CONFIG_DEPLOYMENT" "$BOOTSTRAP_DEPLOYMENT"; do
    auto_created="$(get_output_value "$deployment_name" staticSiteEntraAppAutoCreated)"
    app_object_id="$(get_output_value "$deployment_name" staticSiteEntraAppObjectId)"
    app_client_id="$(get_output_value "$deployment_name" effectiveClientId)"
    if [[ -z "$app_client_id" ]]; then
      app_client_id="$(get_output_value "$deployment_name" staticSiteEntraClientId)"
    fi

    if [[ "$auto_created" == "true" && -n "$app_object_id" ]]; then
      echo "$deployment_name|$app_object_id|$app_client_id"
      return 0
    fi
  done

  return 1
}

delete_auto_created_entra_app() {
  local resolved deployment_name app_object_id app_client_id

  if ! resolved="$(resolve_auto_created_app)"; then
    echo "No auto-created Entra app registration metadata found in deployment outputs. Skipping app deletion."
    return 0
  fi

  IFS='|' read -r deployment_name app_object_id app_client_id <<< "$resolved"
  echo "Deleting auto-created Entra app registration from deployment: $deployment_name"
  echo "  appObjectId=$app_object_id"
  if [[ -n "$app_client_id" ]]; then
    echo "  appId=$app_client_id"
    az ad sp delete --id "$app_client_id" >/dev/null 2>&1 || true
  fi

  az rest --method DELETE --url "https://graph.microsoft.com/v1.0/applications/${app_object_id}" >/dev/null
  echo "Auto-created Entra app registration deleted."
}

capture_key_vault_targets() {
  local name location

  KEYVAULT_TARGETS=()

  while IFS=$'\t' read -r name location; do
    if [[ -n "${name:-}" && -n "${location:-}" ]]; then
      KEYVAULT_TARGETS+=("$name|$location")
    fi
  done < <(az keyvault list -g "$RG" --query "[].{name:name,location:location}" -o tsv 2>/dev/null || true)

  if [[ ${#KEYVAULT_TARGETS[@]} -eq 0 ]]; then
    echo "No Key Vault resources found in RG before deletion."
  else
    echo "Captured ${#KEYVAULT_TARGETS[@]} Key Vault(s) for purge after RG deletion."
  fi
}

wait_for_resource_group_deletion() {
  local deadline
  deadline=$((SECONDS + PURGE_WAIT_SECONDS))

  while [[ "$(az group exists -n "$RG" -o tsv)" == "true" ]]; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for resource group deletion: $RG" >&2
      return 1
    fi
    sleep 10
  done

  return 0
}

wait_until_keyvault_is_deleted() {
  local vault_name="$1"
  local deadline deleted_name
  deadline=$((SECONDS + PURGE_WAIT_SECONDS))

  while true; do
    deleted_name="$(az keyvault list-deleted --query "[?name=='${vault_name}'] | [0].name" -o tsv 2>/dev/null || true)"
    if [[ "$deleted_name" == "$vault_name" ]]; then
      return 0
    fi

    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for Key Vault to enter deleted state: $vault_name" >&2
      return 1
    fi

    sleep 10
  done
}

purge_deleted_key_vaults() {
  local target vault_name vault_location

  if [[ ${#KEYVAULT_TARGETS[@]} -eq 0 ]]; then
    return 0
  fi

  if ! wait_for_resource_group_deletion; then
    echo "Skipping Key Vault purge because RG deletion did not complete in time." >&2
    return 0
  fi

  for target in "${KEYVAULT_TARGETS[@]}"; do
    IFS='|' read -r vault_name vault_location <<< "$target"

    if ! wait_until_keyvault_is_deleted "$vault_name"; then
      echo "Skipping purge for $vault_name due to timeout." >&2
      continue
    fi

    echo "Purging soft-deleted Key Vault: $vault_name"
    az keyvault purge --name "$vault_name" --location "$vault_location" >/dev/null
  done

  echo "Key Vault purge flow completed."
}

if [[ "$DELETE_ENTRA_APP" == "true" ]]; then
  delete_auto_created_entra_app
else
  echo "Skipping Entra app deletion because DELETE_ENTRA_APP=false"
fi

if [[ "$PURGE_DELETED_KEYVAULTS" == "true" ]]; then
  capture_key_vault_targets
else
  echo "Skipping Key Vault purge because PURGE_DELETED_KEYVAULTS=false"
fi

delete_args=(group delete -n "$RG" --yes)
if [[ "$NO_WAIT" == "true" ]]; then
  delete_args+=(--no-wait)
fi

echo "Deleting resource group: $RG"
az "${delete_args[@]}"
echo "Resource group deletion submitted."

if [[ "$PURGE_DELETED_KEYVAULTS" == "true" ]]; then
  purge_deleted_key_vaults
fi