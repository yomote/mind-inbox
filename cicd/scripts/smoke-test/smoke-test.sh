#!/usr/bin/env bash
set -euo pipefail

# Avoid Azure CLI interactive prompts (e.g., extension install) in CI/smoke scripts.
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=${AZURE_EXTENSION_USE_DYNAMIC_INSTALL:-yes_without_prompt}

RG=${RG:-""}
DEPLOYMENT=${DEPLOYMENT:-""}

# Timeout (seconds) for operations that can hang (e.g., Log Analytics query).
LA_QUERY_TIMEOUT=${LA_QUERY_TIMEOUT:-20}

if [[ -z "$RG" || -z "$DEPLOYMENT" ]]; then
  echo "Usage: RG=<resource-group> DEPLOYMENT=<deployment-name> $0" >&2
  exit 2
fi

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 2; }
}

need az
need curl
need timeout

az account show >/dev/null 2>&1 || {
  echo "Azure CLI not logged in. Run: az login" >&2
  exit 2
}

out() {
  local name="$1"
  az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.${name}.value" -o tsv
}

SWA_HOST=$(out staticSiteDefaultHostname || true)
FUNC_HOST=$(out functionAppDefaultHostname || true)
SQL_FQDN=$(out sqlServerFqdn || true)
LAW_CUSTOMER_ID=$(out logAnalyticsCustomerId || true)

fail=0

section() {
  echo
  echo "== $1 =="
}

ok() { echo "OK  - $1"; }
ng() { echo "NG  - $1"; fail=1; }
warn() { echo "WARN- $1"; }

section "Resolve outputs"
[[ -n "$SWA_HOST" ]] && ok "staticSiteDefaultHostname: $SWA_HOST" || ng "Missing output: staticSiteDefaultHostname"
[[ -n "$FUNC_HOST" ]] && ok "functionAppDefaultHostname: $FUNC_HOST" || ng "Missing output: functionAppDefaultHostname"
[[ -n "$SQL_FQDN" ]] && ok "sqlServerFqdn: $SQL_FQDN" || ng "Missing output: sqlServerFqdn"
[[ -n "$LAW_CUSTOMER_ID" ]] && ok "logAnalyticsCustomerId: $LAW_CUSTOMER_ID" || warn "Missing output: logAnalyticsCustomerId"

section "Public reachability"
if [[ -n "$SWA_HOST" ]]; then
  # Best-effort: discover SWA SKU from ARM so we can decide whether /api/* is expected.
  # Note: linkedBackends (SWA -> existing Function App) is Standard-only in our IaC.
  SWA_SKU=$(az resource list -g "$RG" --resource-type "Microsoft.Web/staticSites" --query "[?properties.defaultHostname=='$SWA_HOST']|[0].sku.name" -o tsv 2>/dev/null || true)
  [[ -n "$SWA_SKU" ]] && ok "SWA SKU: $SWA_SKU" || warn "Could not resolve SWA SKU (will treat /api/health check as best-effort)"

  if curl -fsS "https://$SWA_HOST" >/dev/null; then
    ok "SWA root reachable"
  else
    ng "SWA root not reachable"
  fi

  set +e
  swa_api_code=$(curl -sS -o /dev/null -w "%{http_code}" "https://$SWA_HOST/api/health")
  curl_rc=$?
  set -e

  if [[ "$curl_rc" -eq 0 && "$swa_api_code" == "200" ]]; then
    ok "SWA /api/health reachable"
  else
    if [[ "$SWA_SKU" == "Standard" ]]; then
      ng "SWA /api/health not reachable (expected reachable for Standard SKU linked backend; HTTP ${swa_api_code:-?})"
    else
      warn "SWA /api/health not reachable (often expected on Free SKU unless repo/API is wired; HTTP ${swa_api_code:-?})"
      warn "Tip: set staticSiteSkuName=Standard to enable linked backend in IaC, or link repo so SWA builds the API"
    fi
  fi
fi

if [[ -n "$FUNC_HOST" ]]; then
  if curl -fsS "https://$FUNC_HOST/api/health" >/dev/null; then
    ok "Function App /api/health reachable"
    warn "If you intend to block direct access to Function App, add access restrictions (not present in IaC)"
  else
    warn "Function App /api/health not reachable (deployment/package may not be published yet)"
  fi
fi

section "SQL public access should be blocked"
if [[ -n "$SQL_FQDN" ]]; then
  SQL_SERVER_NAME=${SQL_FQDN%%.*}

  # Prefer config truth over TCP checks (Azure SQL may accept a TCP handshake even when firewall blocks auth).
  sql_pna=$(az sql server show -g "$RG" -n "$SQL_SERVER_NAME" --query "publicNetworkAccess" -o tsv 2>/dev/null || true)
  if [[ -z "$sql_pna" ]]; then
    warn "Could not query SQL publicNetworkAccess (check permissions/resource name)"
  elif [[ "$sql_pna" == "Disabled" ]]; then
    ok "SQL publicNetworkAccess Disabled (expected)"
  else
    ng "SQL publicNetworkAccess is '$sql_pna' (expected Disabled)"
  fi
fi

section "Private Endpoint / DNS config sanity"
if [[ -n "$SQL_FQDN" ]]; then
  SQL_SERVER_NAME=${SQL_FQDN%%.*}
  if az network private-endpoint show -g "$RG" -n "pe-$SQL_SERVER_NAME" --query "name" -o tsv >/dev/null 2>&1; then
    pe_status=$(az network private-endpoint show -g "$RG" -n "pe-$SQL_SERVER_NAME" --query "properties.privateLinkServiceConnections[0].properties.privateLinkServiceConnectionState.status" -o tsv)
    [[ "$pe_status" == "Approved" ]] && ok "SQL Private Endpoint approved" || warn "SQL Private Endpoint status: $pe_status"

    dzg_count=$(az network private-endpoint dns-zone-group list -g "$RG" --endpoint-name "pe-$SQL_SERVER_NAME" --query "length(@)" -o tsv 2>/dev/null || echo "0")
    [[ "$dzg_count" != "0" ]] && ok "Private DNS zone group present" || warn "No private DNS zone group found"
  else
    warn "Private Endpoint pe-$SQL_SERVER_NAME not found"
  fi
fi

section "Log Analytics check"
if [[ -n "$LAW_CUSTOMER_ID" ]]; then
  # Query AzureDiagnostics to see if any diagnostic logs are flowing.
  # With current IaC, SQL diagnostic settings has no categories enabled, so this may be empty.
  set +e
  timeout "$LA_QUERY_TIMEOUT" \
    az monitor log-analytics query \
      -w "$LAW_CUSTOMER_ID" \
      --analytics-query "AzureDiagnostics | take 5" \
      -o json >/dev/null 2>&1
  la_rc=$?
  set -e

  if [[ "$la_rc" -eq 0 ]]; then
    ok "Log Analytics query executed"
    echo "(If results are empty, it may be expected until diagnostic categories are enabled.)"
  elif [[ "$la_rc" -eq 124 ]]; then
    warn "Log Analytics query timed out after ${LA_QUERY_TIMEOUT}s (network/permission/extension install stall)"
    warn "Tip: re-run with LA_QUERY_TIMEOUT=60, or try: az monitor log-analytics query -w <id> --analytics-query 'AzureDiagnostics | take 1' -o table"
  else
    warn "Log Analytics query failed (permission, workspace id mismatch, or missing extension)"
  fi
else
  warn "Skipping Log Analytics query (no logAnalyticsCustomerId output)"
fi

section "Result"
if [[ "$fail" -eq 0 ]]; then
  echo "PASS (with possible WARNs)"
else
  echo "FAIL (see NG items)"
fi

exit "$fail"
