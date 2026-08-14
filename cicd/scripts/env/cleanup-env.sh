#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
CONFIG_DEPLOYMENT="${CONFIG_DEPLOYMENT:-main-config}"
BOOTSTRAP_DEPLOYMENT="${BOOTSTRAP_DEPLOYMENT:-main-bootstrap}"
# 破壊の既定は「救済を残す」側に倒す (ADR 0046 D5/D6)。
# soft-delete を purge すると復旧手段が消えるため、purge は明示的に頼まれた時だけ行う。
# Entra アプリ登録は RG ではなくテナントのオブジェクトなので、RG の撤収では消さない。
DELETE_ENTRA_APP="${DELETE_ENTRA_APP:-false}"
NO_WAIT="${NO_WAIT:-true}"
PURGE_DELETED_KEYVAULTS="${PURGE_DELETED_KEYVAULTS:-false}"
PURGE_DELETED_COGNITIVE_SERVICES="${PURGE_DELETED_COGNITIVE_SERVICES:-false}"
FORCE_DELETE_LOG_ANALYTICS="${FORCE_DELETE_LOG_ANALYTICS:-false}"
PURGE_WAIT_SECONDS="${PURGE_WAIT_SECONDS:-1800}"
# 持続層 RG (ADR 0046 D1 / #302)。撤収の対象は環境層だけで、ここは**削除できない**。
# 判定は persistent_layer_guard.py が持つ (このスクリプトは材料を集めるだけ)。
PERSISTENT_RG="${PERSISTENT_RG:-rg-shared-mindbox}"
ALLOW_PERSISTENT_DELETE="${ALLOW_PERSISTENT_DELETE:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
  DELETE_ENTRA_APP                true|false. Delete auto-created Entra app registration first (default: false)
  NO_WAIT                         true|false. Pass --no-wait to az group delete (default: true)
  FORCE_DELETE_LOG_ANALYTICS      true|false. Force-delete LA workspaces before RG delete (default: false)
  PURGE_DELETED_KEYVAULTS         true|false. Purge soft-deleted Key Vaults after RG deletion (default: false)
  PURGE_DELETED_COGNITIVE_SERVICES true|false. Purge soft-deleted CS / OpenAI accounts after RG deletion (default: false)
  PURGE_WAIT_SECONDS              Max seconds to wait for RG deletion / soft-deleted state (default: 1800)
  PERSISTENT_RG                   Persistent-layer RG that must never be torn down (default: rg-shared-mindbox)
  ALLOW_PERSISTENT_DELETE         true|false. Proceed even though persistent resources are in the target RG (default: false)

This script refuses to run when the target RG is the persistent layer, when the
target RG still holds persistent resources (Cosmos / Cognitive Services / Key
Vault), or when their presence could not be determined (ADR 0046 D1 / #302).
"Could not check" is treated as "do not delete" -- not as "nothing to protect".

Destructive options default to OFF (ADR 0046 D5/D6). Purging soft-deleted resources
removes the only recovery path, so it must be asked for explicitly. Turn it on when
you need to recreate a resource under the SAME name and the soft-deleted twin is in
the way -- that is the only case that needs it.

Enable ONLY the flag for the resource type that actually conflicted. Setting both
"just in case" purges a type you did not need to purge, throwing away its recovery
path for nothing.

Examples:
  # Default: delete the RG, keep every soft-deleted twin recoverable
  RG=rg-dev-mind-inbox ./scripts/env/cleanup-env.sh

  # Re-provision hit a name conflict on OpenAI / Speech (Cognitive Services):
  RG=rg-dev-mind-inbox PURGE_DELETED_COGNITIVE_SERVICES=true ./scripts/env/cleanup-env.sh

  # Re-provision hit a name conflict on Key Vault:
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

# 持続層ガードの判定は python の純粋関数が持つ (pytest で押さえてある)。
# 無いと判定できない = 削除に進めない、なので存在チェックは必須扱いにする。
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 command not found (needed by persistent_layer_guard.py)" >&2
  exit 1
fi

az account show >/dev/null

rg_exists() {
  [[ "$(az group exists -n "$RG" -o tsv)" == "true" ]]
}

# 撤収してよい RG かを判定する。判定そのものは持たず、材料 (RG の中身) を集めて
# persistent_layer_guard.py に渡すだけ。拒否されたら**何も消さずに**終了する。
assert_target_is_not_persistent_layer() {
  local guard="${SCRIPT_DIR}/persistent_layer_guard.py"
  local -a guard_args=(--target-rg "$RG" --persistent-rg "$PERSISTENT_RG")
  local inventory err_file

  if [[ "$ALLOW_PERSISTENT_DELETE" == "true" ]]; then
    guard_args+=(--allow-persistent)
  fi

  if ! rg_exists; then
    guard_args+=(--rg-missing)
  else
    err_file="$(mktemp)"
    # az の失敗を握り潰さない: 失敗したら「持続層は無い」ではなく
    # 「確かめられなかった」として渡す (guard 側が拒否する)。
    # stderr は捨てずに下で表示する — 権限不足かログイン切れかを見えるようにするため。
    if inventory="$(az resource list -g "$RG" --query "[].type" -o json 2>"$err_file")"; then
      guard_args+=(--inventory "$inventory")
    else
      echo "WARN: could not list resources in ${RG}. az said:" >&2
      cat "$err_file" >&2
      guard_args+=(--inventory-unavailable)
    fi
    rm -f "$err_file"
  fi

  if ! python3 "$guard" "${guard_args[@]}"; then
    echo "" >&2
    echo "Nothing was deleted. Move the persistent resources to ${PERSISTENT_RG} first" >&2
    echo "(Issue #302), or re-run with ALLOW_PERSISTENT_DELETE=true if you really mean" >&2
    echo "to destroy them -- they do not come back." >&2
    exit 3
  fi
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

# **破壊系より前**に置く。ここを通らないと 1 つも消えない。
assert_target_is_not_persistent_layer

if [[ "$DELETE_ENTRA_APP" == "true" ]]; then
  delete_auto_created_entra_app
else
  echo "Keeping the Entra app registration (DELETE_ENTRA_APP=false)."
  echo "  It is a tenant object, not an RG resource -- the RG teardown does not own it (ADR 0046 D5)."
fi

if [[ "$PURGE_DELETED_KEYVAULTS" == "true" ]]; then
  capture_key_vault_targets
else
  echo "Keeping soft-deleted Key Vault(s) (PURGE_DELETED_KEYVAULTS=false) -- recovery stays possible."
fi

if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" ]]; then
  capture_cognitive_services_targets
else
  echo "Keeping soft-deleted Cognitive Services / OpenAI account(s) (PURGE_DELETED_COGNITIVE_SERVICES=false) -- recovery stays possible."
fi

if [[ "$FORCE_DELETE_LOG_ANALYTICS" == "true" ]]; then
  force_delete_log_analytics_workspaces
else
  echo "Keeping Log Analytics workspace(s) recoverable (FORCE_DELETE_LOG_ANALYTICS=false)."
fi

# 名前衝突が起きたときの手当てを案内する。**衝突した種類のフラグだけ**を出すこと —
# 「とりあえず全部立てる」を案内すると、衝突していない種類の soft-delete まで
# 巻き添えで purge され、必要のない復旧経路を永久に失う (ADR 0046 D6 の趣旨に反する)。
if [[ "$PURGE_DELETED_KEYVAULTS" != "true" || "$PURGE_DELETED_COGNITIVE_SERVICES" != "true" ]]; then
  echo ""
  echo "NOTE: soft-deleted twins are being left in place on purpose (ADR 0046 D6)."
  echo "If a later re-provision fails with a name conflict (e.g. \"already exists in"
  echo "soft-deleted state\" / FlagMustBeSetForRestore), that is the expected symptom."
  echo "Re-run this script with ONLY the flag matching the resource type that conflicted:"
  if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" != "true" ]]; then
    echo "  OpenAI / Speech (Cognitive Services) conflicted -> PURGE_DELETED_COGNITIVE_SERVICES=true"
  fi
  if [[ "$PURGE_DELETED_KEYVAULTS" != "true" ]]; then
    echo "  Key Vault conflicted                            -> PURGE_DELETED_KEYVAULTS=true"
  fi
  echo "Do NOT set both unless both actually conflicted -- purging a type you did not"
  echo "need to purge throws away its only recovery path."
  echo ""
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
