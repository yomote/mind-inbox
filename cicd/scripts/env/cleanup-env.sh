#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
CONFIG_DEPLOYMENT="${CONFIG_DEPLOYMENT:-main-config}"
BOOTSTRAP_DEPLOYMENT="${BOOTSTRAP_DEPLOYMENT:-main-bootstrap}"
DELETE_ENTRA_APP="${DELETE_ENTRA_APP:-true}"
NO_WAIT="${NO_WAIT:-true}"
PURGE_DELETED_KEYVAULTS="${PURGE_DELETED_KEYVAULTS:-true}"
PURGE_DELETED_COGNITIVE_SERVICES="${PURGE_DELETED_COGNITIVE_SERVICES:-true}"
FORCE_DELETE_LOG_ANALYTICS="${FORCE_DELETE_LOG_ANALYTICS:-true}"
PURGE_WAIT_SECONDS="${PURGE_WAIT_SECONDS:-1800}"

declare -a KEYVAULT_TARGETS=()
declare -a COGNITIVE_TARGETS=()

usage() {
  cat <<'EOF'
Delete an environment resource group and clean up soft-deleted residue so the
environment can be redeployed under the same names without conflicts.

What this script removes:
  1. Auto-created Entra app registration referenced by deployment outputs
  2. Log Analytics workspaces in the RG (force-deleted to skip 14-day soft-delete)
  3. The resource group itself
  4. Soft-deleted Key Vault(s) originally in the RG (purged after RG deletion)
  5. Soft-deleted Cognitive Services / OpenAI account(s) originally in the RG

Soft-deleted Key Vaults and Cognitive Services accounts are also discovered via
"list-deleted" as a fallback in case the RG was already deleted previously.

Environment variables:
  RG                              Resource group name (default: rg-dev-mind-inbox)
  CONFIG_DEPLOYMENT               Config deployment name to inspect for auth outputs (default: main-config)
  BOOTSTRAP_DEPLOYMENT            Bootstrap deployment name fallback (default: main-bootstrap)
  DELETE_ENTRA_APP                true|false. Delete auto-created Entra app registration first (default: true)
  NO_WAIT                         true|false. Pass --no-wait to az group delete (default: true)
  FORCE_DELETE_LOG_ANALYTICS      true|false. Force-delete LA workspaces before RG delete (default: true)
  PURGE_DELETED_KEYVAULTS         true|false. Purge soft-deleted Key Vaults after RG deletion (default: true)
  PURGE_DELETED_COGNITIVE_SERVICES true|false. Purge soft-deleted CS / OpenAI accounts after RG deletion (default: true)
  PURGE_WAIT_SECONDS              Max seconds to wait for RG deletion / soft-deleted state (default: 1800)

Examples:
  RG=rg-dev-mind-inbox ./scripts/env/cleanup-env.sh
  RG=rg-dev-mind-inbox DELETE_ENTRA_APP=false ./scripts/env/cleanup-env.sh
  RG=rg-dev-mind-inbox PURGE_DELETED_COGNITIVE_SERVICES=false ./scripts/env/cleanup-env.sh
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

rg_exists() {
  [[ "$(az group exists -n "$RG" -o tsv)" == "true" ]]
}

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

  if ! rg_exists; then
    echo "RG ${RG} does not exist; skipping Entra app deletion (deployment outputs unavailable)."
    return 0
  fi

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

target_already_captured() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

capture_key_vault_targets() {
  local name location

  KEYVAULT_TARGETS=()

  if rg_exists; then
    while IFS=$'\t' read -r name location; do
      if [[ -n "${name:-}" && -n "${location:-}" ]]; then
        KEYVAULT_TARGETS+=("$name|$location")
      fi
    done < <(az keyvault list -g "$RG" --query "[].{name:name,location:location}" -o tsv 2>/dev/null || true)
  fi

  while IFS=$'\t' read -r name location; do
    if [[ -n "${name:-}" && -n "${location:-}" ]]; then
      if ! target_already_captured "$name|$location" "${KEYVAULT_TARGETS[@]:-}"; then
        KEYVAULT_TARGETS+=("$name|$location")
      fi
    fi
  done < <(az keyvault list-deleted \
    --query "[?contains(properties.vaultId, '/resourceGroups/${RG}/')].{name:name,location:properties.location}" \
    -o tsv 2>/dev/null || true)

  if [[ ${#KEYVAULT_TARGETS[@]} -eq 0 ]]; then
    echo "No Key Vault resources found (live or soft-deleted) for RG ${RG}."
  else
    echo "Captured ${#KEYVAULT_TARGETS[@]} Key Vault(s) for purge after RG deletion."
  fi
}

capture_cognitive_services_targets() {
  local name location

  COGNITIVE_TARGETS=()

  if rg_exists; then
    while IFS=$'\t' read -r name location; do
      if [[ -n "${name:-}" && -n "${location:-}" ]]; then
        COGNITIVE_TARGETS+=("$name|$location")
      fi
    done < <(az cognitiveservices account list -g "$RG" --query "[].{name:name,location:location}" -o tsv 2>/dev/null || true)
  fi

  # Soft-deleted CS account id format:
  #   /subscriptions/<sub>/providers/Microsoft.CognitiveServices/locations/<location>/resourceGroups/<originalRg>/deletedAccounts/<name>
  while IFS=$'\t' read -r name location; do
    if [[ -n "${name:-}" && -n "${location:-}" ]]; then
      if ! target_already_captured "$name|$location" "${COGNITIVE_TARGETS[@]:-}"; then
        COGNITIVE_TARGETS+=("$name|$location")
      fi
    fi
  done < <(az cognitiveservices account list-deleted \
    --query "[?contains(id, '/resourceGroups/${RG}/deletedAccounts/')].{name:name,location:location}" \
    -o tsv 2>/dev/null || true)

  if [[ ${#COGNITIVE_TARGETS[@]} -eq 0 ]]; then
    echo "No Cognitive Services / OpenAI accounts found (live or soft-deleted) for RG ${RG}."
  else
    echo "Captured ${#COGNITIVE_TARGETS[@]} Cognitive Services account(s) for purge after RG deletion."
  fi
}

force_delete_log_analytics_workspaces() {
  local name

  if ! rg_exists; then
    echo "RG ${RG} does not exist; skipping Log Analytics force-delete."
    return 0
  fi

  local -a workspaces=()
  while IFS= read -r name; do
    [[ -n "$name" ]] && workspaces+=("$name")
  done < <(az monitor log-analytics workspace list -g "$RG" --query "[].name" -o tsv 2>/dev/null || true)

  if [[ ${#workspaces[@]} -eq 0 ]]; then
    echo "No Log Analytics workspaces found in RG."
    return 0
  fi

  echo "Force-deleting ${#workspaces[@]} Log Analytics workspace(s) (skipping 14-day soft-delete)."
  for name in "${workspaces[@]}"; do
    echo "  - $name"
    az monitor log-analytics workspace delete \
      --resource-group "$RG" \
      --workspace-name "$name" \
      --force true \
      --yes >/dev/null
  done
}

wait_for_resource_group_deletion() {
  local deadline
  deadline=$((SECONDS + PURGE_WAIT_SECONDS))

  while rg_exists; do
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

wait_until_cognitive_services_is_deleted() {
  local account_name="$1"
  local deadline deleted_name
  deadline=$((SECONDS + PURGE_WAIT_SECONDS))

  while true; do
    deleted_name="$(az cognitiveservices account list-deleted --query "[?name=='${account_name}'] | [0].name" -o tsv 2>/dev/null || true)"
    if [[ "$deleted_name" == "$account_name" ]]; then
      return 0
    fi

    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for Cognitive Services account to enter deleted state: $account_name" >&2
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

purge_deleted_cognitive_services() {
  local target account_name account_location

  if [[ ${#COGNITIVE_TARGETS[@]} -eq 0 ]]; then
    return 0
  fi

  for target in "${COGNITIVE_TARGETS[@]}"; do
    IFS='|' read -r account_name account_location <<< "$target"

    if ! wait_until_cognitive_services_is_deleted "$account_name"; then
      echo "Skipping purge for $account_name due to timeout." >&2
      continue
    fi

    echo "Purging soft-deleted Cognitive Services account: $account_name"
    az cognitiveservices account purge \
      --name "$account_name" \
      --resource-group "$RG" \
      --location "$account_location" >/dev/null
  done

  echo "Cognitive Services purge flow completed."
}

# ---- main flow ----

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

if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" ]]; then
  capture_cognitive_services_targets
else
  echo "Skipping Cognitive Services purge because PURGE_DELETED_COGNITIVE_SERVICES=false"
fi

if [[ "$FORCE_DELETE_LOG_ANALYTICS" == "true" ]]; then
  force_delete_log_analytics_workspaces
else
  echo "Skipping Log Analytics force-delete because FORCE_DELETE_LOG_ANALYTICS=false"
fi

if rg_exists; then
  delete_args=(group delete -n "$RG" --yes)
  if [[ "$NO_WAIT" == "true" ]]; then
    delete_args+=(--no-wait)
  fi

  echo "Deleting resource group: $RG"
  az "${delete_args[@]}"
  echo "Resource group deletion submitted."
else
  echo "Resource group ${RG} does not exist; skipping group delete."
fi

needs_purge_wait=false
if [[ "$PURGE_DELETED_KEYVAULTS" == "true" && ${#KEYVAULT_TARGETS[@]} -gt 0 ]]; then
  needs_purge_wait=true
fi
if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" && ${#COGNITIVE_TARGETS[@]} -gt 0 ]]; then
  needs_purge_wait=true
fi

if [[ "$needs_purge_wait" == "true" ]]; then
  if ! wait_for_resource_group_deletion; then
    echo "Skipping post-RG purge because RG deletion did not complete in time." >&2
    exit 1
  fi
fi

if [[ "$PURGE_DELETED_KEYVAULTS" == "true" ]]; then
  purge_deleted_key_vaults
fi

if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" ]]; then
  purge_deleted_cognitive_services
fi
