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
# 管理系 RG (ADR 0056 D1 / #302)。撤収の対象はアプリ系だけで、ここは**削除できない**。
# 判定は persistent_layer_guard.py が持つ (このスクリプトは材料を集めるだけ)。
MGMT_RG="${MGMT_RG:-rg-mgmt-mindbox}"
ALLOW_PROTECTED_DELETE="${ALLOW_PROTECTED_DELETE:-false}"
# 層タグ (mindInboxLayer=management) の付いていない管理系リソースを名指しで守る。
# 空白区切り。移行前の (mgmt bicep をまだ流していない) Storage / Log Analytics 用。
PROTECTED_RESOURCE_NAMES="${PROTECTED_RESOURCE_NAMES:-}"

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
  MGMT_RG                         Management-layer RG that must never be torn down (default: rg-mgmt-mindbox)
  PROTECTED_RESOURCE_NAMES        Space-separated resource names to treat as management even without the layer tag (default: empty)
  ALLOW_PROTECTED_DELETE          true|false. Proceed even though protected resources are in the target RG (default: false)

This script refuses to run when the target RG is the management layer, when the
target RG still holds management resources, when it holds data whose restore has
never been proven, or when either the RG's existence or its contents could not be
determined (ADR 0056 D1 / #302). "Could not check" is treated as "do not delete"
-- not as "nothing to protect".

Soft-deleted resources do NOT show up in "az resource list", so when a purge flag
is on, "list-deleted" for that type is fed to the guard as well. Purging a
protected soft-deleted twin destroys the only recovery path, so it is refused.

A resource counts as management when (1) it carries the layer tag
mindInboxLayer=management stamped by main-mgmt.bicep, or (2) its name is listed
in PROTECTED_RESOURCE_NAMES. Key Vault, Storage and Log Analytics exist in BOTH
layers, so type alone does not make them management -- they need the tag or the
name. Untagged ones are reported before deletion.

Cosmos DB is app-layer by design (ADR 0056 D1 / PO ruling on #302) but is refused for now:
the backup/restore round-trip (ADR 0046 D9) has never been run, and "a backup you
have never restored is not a backup" (ADR 0018). Once the restore is proven, that
blanket refusal is replaced by a backup-freshness check -- see
docs/runbooks/mgmt-layer-apply.md. OpenAI / Speech do NOT block teardown (they hold
no data); what they cost to lose is printed instead of silently ignored.

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

# 層ガードの判定は python の純粋関数が持つ (pytest で押さえてある)。
# 無いと判定できない = 削除に進めない、なので存在チェックは必須扱いにする。
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 command not found (needed by persistent_layer_guard.py)" >&2
  exit 1
fi

az account show >/dev/null

# RG の存在を 3 値で返す: true / false / unknown。
# **unknown を false (不在) に潰さない** — 「確かめられなかった」を「消すものが無い」と
# 読み替えると、層ガードが inventory を一度も検証しないまま通過し、後段の再確認が
# 成功したときに az group delete まで進む経路ができる。az の stderr は握り潰さず出す。
rg_exists_state() {
  local out err_file rc=0

  err_file="$(mktemp)"
  if ! out="$(az group exists -n "$RG" -o tsv 2>"$err_file")"; then
    rc=1
  fi

  if (( rc != 0 )); then
    echo "WARN: could not check whether ${RG} exists. az said:" >&2
    cat "$err_file" >&2
    rm -f "$err_file"
    echo "unknown"
    return 0
  fi
  rm -f "$err_file"

  case "$(printf '%s' "$out" | tr -d '[:space:]')" in
    true) echo "true" ;;
    false) echo "false" ;;
    *)
      # 想定外の出力 (az のフォーマット変更 / 空応答) も「不在」にしない。
      echo "WARN: unexpected 'az group exists' output for ${RG}: '${out}'" >&2
      echo "unknown"
      ;;
  esac
}

# 破壊系より後ろの分岐で使う簡易版。unknown は false 側 (= 触らない) に倒れるので、
# 「消すのをやめる」方向にしか働かない。**ガードの判定には使わない** (そちらは 3 値)。
rg_exists() {
  [[ "$(rg_exists_state)" == "true" ]]
}

# 直近のガード判定コード (rg-absent / ok / … )。許可か拒否かの 2 値では足りない —
# 「不在だから通した」のか「中身を見て通した」のかで、その後の扱いが変わる。
GUARD_DECISION_CODE=""
# これまでに出た判定コードを古い順に溜める。**ここで比較はしない** — 溜めて guard に
# 渡すだけ。「一度不在で通した RG が現れた」の判定は persistent_layer_guard.py 側
# (decide) が持つ (シェルの if に判定を置くと、壊してもテストが落ちない)。
declare -a GUARD_PREVIOUS_CODES=()

# 撤収してよい RG かを判定する。判定そのものは持たず、材料 (RG の中身) を集めて
# persistent_layer_guard.py に渡すだけ。**呼ぶたびに材料を取り直す** (使い回さない)。
run_layer_guard() {
  local guard="${SCRIPT_DIR}/persistent_layer_guard.py"
  local -a guard_args=(--target-rg "$RG" --mgmt-rg "$MGMT_RG")
  local inventory deleted err_file rg_state name code rc=0

  if [[ "$ALLOW_PROTECTED_DELETE" == "true" ]]; then
    guard_args+=(--allow-protected)
  fi

  # これまでの判定を材料として渡す (状態遷移の判定は guard 側)。
  if [[ ${#GUARD_PREVIOUS_CODES[@]} -gt 0 ]]; then
    for code in "${GUARD_PREVIOUS_CODES[@]}"; do
      guard_args+=(--previous-code "$code")
    done
  fi

  # 層タグの無い管理系リソースの名指し (移行前の Storage / Log Analytics 向け)。
  for name in $PROTECTED_RESOURCE_NAMES; do
    guard_args+=(--protected-name "$name")
  done

  # soft-delete 済みの保護対象は `az resource list` に出ない。**purge は soft-delete
  # という唯一の復旧手段を恒久的に消す**ので、purge を有効にした種類だけ list-deleted
  # も判定材料に渡す (有効にしていない種類は触らないので渡さない = 無関係な soft-delete
  # で撤収が止まらない)。
  if [[ "$PURGE_DELETED_KEYVAULTS" == "true" ]]; then
    err_file="$(mktemp)"
    if deleted="$(az keyvault list-deleted \
      --query "[?contains(properties.vaultId, '/resourceGroups/${RG}/')].{type:'Microsoft.KeyVault/vaults',name:name,tags:properties.tags}" \
      -o json 2>"$err_file")"; then
      guard_args+=(--deleted-inventory "$deleted")
    else
      echo "WARN: could not list soft-deleted Key Vaults for ${RG}. az said:" >&2
      cat "$err_file" >&2
      guard_args+=(--deleted-inventory-unavailable)
    fi
    rm -f "$err_file"
  fi

  if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" ]]; then
    err_file="$(mktemp)"
    if deleted="$(az cognitiveservices account list-deleted \
      --query "[?contains(id, '/resourceGroups/${RG}/deletedAccounts/')].{type:'Microsoft.CognitiveServices/accounts',name:name,tags:tags}" \
      -o json 2>"$err_file")"; then
      guard_args+=(--deleted-inventory "$deleted")
    else
      echo "WARN: could not list soft-deleted Cognitive Services accounts for ${RG}. az said:" >&2
      cat "$err_file" >&2
      guard_args+=(--deleted-inventory-unavailable)
    fi
    rm -f "$err_file"
  fi

  rg_state="$(rg_exists_state)"
  if [[ "$rg_state" == "unknown" ]]; then
    # 存在を確かめられなかった。**不在扱いにしない** — guard 側が拒否する。
    guard_args+=(--rg-unknown)
  elif [[ "$rg_state" == "false" ]]; then
    guard_args+=(--rg-missing)
  else
    err_file="$(mktemp)"
    # az の失敗を握り潰さない: 失敗したら「保護対象は無い」ではなく
    # 「確かめられなかった」として渡す (guard 側が拒否する)。
    # stderr は捨てずに下で表示する — 権限不足かログイン切れかを見えるようにするため。
    # type だけでなく name / tags も渡す: 型だけではアプリ系の Key Vault / Storage /
    # Log Analytics と管理系のそれを区別できない (判定は guard 側)。
    if inventory="$(az resource list -g "$RG" --query "[].{type:type,name:name,tags:tags}" -o json 2>"$err_file")"; then
      guard_args+=(--inventory "$inventory")
    else
      echo "WARN: could not list resources in ${RG}. az said:" >&2
      cat "$err_file" >&2
      guard_args+=(--inventory-unavailable)
    fi
    rm -f "$err_file"
  fi

  # 判定コードは stdout、人間向けの本文は stderr (そのまま流す)。
  if ! GUARD_DECISION_CODE="$(python3 "$guard" "${guard_args[@]}")"; then
    rc=3
  fi

  # 判定コードは中身を見ずにそのまま積む (どれが特別かを知っているのは guard 側)。
  if [[ -n "$GUARD_DECISION_CODE" ]]; then
    GUARD_PREVIOUS_CODES+=("$GUARD_DECISION_CODE")
  fi

  return "$rc"
}

# **破壊系の 1 つ手前で必ず呼ぶ。** 最初の判定から時間が経つ間に RG が作り直されて
# いる可能性があるため (TOCTOU)、材料はそのつど取り直す。
assert_safe_to_destroy() {
  local phase="$1"

  # 「不在だから通した」あとに RG が現れた場合も、この判定 (rg-reappeared-after-absent)
  # として guard 側から返る。**このシェルは比較しない** — 判定は 1 か所に置く。
  if ! run_layer_guard; then
    echo "" >&2
    echo "Nothing was deleted (refused before ${phase}). See the guard line above:" >&2
    echo "  - management resources still in the RG -> move them to ${MGMT_RG} first (Issue #302)" >&2
    echo "  - data whose restore is unproven (Cosmos) -> provisional refusal until the backup" >&2
    echo "    and restore round-trip has been run once (ADR 0046 D9)" >&2
    echo "  - could not check (existence or contents) -> fix az login / permissions and re-run" >&2
    echo "  - target IS the management RG -> there is no flag for this, on purpose" >&2
    echo "  - the RG was absent earlier in this run but is not now -> a concurrent provision" >&2
    echo "    re-created it; nothing was deleted. Re-run from the start if you still want it gone" >&2
    echo "ALLOW_PROTECTED_DELETE=true overrides all but the last two, and what it destroys" >&2
    echo "does not come back." >&2
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
  local deadline state
  deadline=$((SECONDS + PURGE_WAIT_SECONDS))

  while true; do
    state="$(rg_exists_state)"
    # unknown を「消えた」と読み替えない — 読み替えると、RG が生きている可能性を
    # 残したまま purge (soft-delete の唯一の復旧手段を消す処理) に進む。
    if [[ "$state" == "false" ]]; then
      return 0
    fi

    if (( SECONDS >= deadline )); then
      if [[ "$state" == "unknown" ]]; then
        echo "Could not determine whether ${RG} was deleted (az check kept failing)." >&2
      else
        echo "Timed out waiting for resource group deletion: $RG" >&2
      fi
      return 1
    fi
    sleep 10
  done
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
# 以降、破壊系の 1 つ手前でそのつど取り直す (TOCTOU — 判定と削除の間に RG の中身が
# 変わりうる。特に「不在だから通した」あとに provision が RG を作り直す経路)。
assert_safe_to_destroy "any destructive step"

if [[ "$DELETE_ENTRA_APP" == "true" ]]; then
  assert_safe_to_destroy "the Entra app deletion"
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
  assert_safe_to_destroy "the Log Analytics force-delete"
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

# 削除の直前にもう一度。**削除するかどうかもこのガードの判定から取る** —
# `rg_exists` を別に呼ぶと、その返答と「中身を検証した瞬間」がまたズレる。
assert_safe_to_destroy "the resource group deletion"

# ここは「消してよいか」の判定ではなく、**済んだ判定から動作を選ぶだけ** (不在なら
# 消すものが無いので呼ばない)。取り違えても `az group delete` が存在しない RG に対して
# 落ちるだけで、無検証の削除にはならない (削除してよいかの判定は guard が済ませている)。
if [[ "$GUARD_DECISION_CODE" != "rg-absent" ]]; then
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

# purge は「soft-delete という唯一の復旧手段を恒久的に消す」処理なので、ここでも
# 直前に判定を取り直す。この時点では RG は既に消えている (rg-absent) が、ガードは
# **soft-delete 済みの保護対象を RG の存在とは独立に**見るので素通りしない。
if [[ "$PURGE_DELETED_KEYVAULTS" == "true" || "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" ]]; then
  assert_safe_to_destroy "the soft-deleted resource purge"
fi

if [[ "$PURGE_DELETED_KEYVAULTS" == "true" ]]; then
  purge_deleted_key_vaults
fi

if [[ "$PURGE_DELETED_COGNITIVE_SERVICES" == "true" ]]; then
  purge_deleted_cognitive_services
fi
