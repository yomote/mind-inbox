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
SQL_ENABLED=$(out sqlEnabled || true)
LAW_CUSTOMER_ID=$(out logAnalyticsCustomerId || true)
EASYAUTH_ENABLED=$(out functionEasyAuthEnabled || true)

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
# SQL は enableSql=false（既定, ADR 0013）だと未プロビジョニング → 出力空は正常（skip 扱い）。
# ただし enableSql=true なのに FQDN 空なら SQL provisioning 失敗の疑い → NG（退行を握りつぶさない）。
if [[ -n "$SQL_FQDN" ]]; then
  ok "sqlServerFqdn: $SQL_FQDN"
elif [[ "$SQL_ENABLED" == "true" ]]; then
  ng "enableSql=true なのに sqlServerFqdn が空 (SQL provisioning 失敗の疑い)"
else
  warn "sqlServerFqdn 空: SQL 無効 (enableSql=false) とみなし SQL 系チェックを skip"
fi
[[ -n "$LAW_CUSTOMER_ID" ]] && ok "logAnalyticsCustomerId: $LAW_CUSTOMER_ID" || warn "Missing output: logAnalyticsCustomerId"

section "Public reachability"
if [[ -n "$SWA_HOST" ]]; then
  # Best-effort: discover SWA SKU from ARM so we can decide whether /api/* is expected.
  # Note: linkedBackends (SWA -> existing Function App) is Standard-only in our IaC.
  SWA_SKU=$(az resource list -g "$RG" --resource-type "Microsoft.Web/staticSites" --query "[?properties.defaultHostname=='$SWA_HOST']|[0].sku.name" -o tsv 2>/dev/null || true)
  [[ -n "$SWA_SKU" ]] && ok "SWA SKU: $SWA_SKU" || warn "Could not resolve SWA SKU (will treat /api/trpc/health.ping check as best-effort)"

  if curl -fsS "https://$SWA_HOST" >/dev/null; then
    ok "SWA root reachable"
  else
    ng "SWA root not reachable"
  fi

  set +e
  swa_api_code=$(curl -sS -o /dev/null -w "%{http_code}" "https://$SWA_HOST/api/trpc/health.ping")
  curl_rc=$?
  set -e

  if [[ "$curl_rc" -eq 0 && "$swa_api_code" == "200" ]]; then
    ok "SWA /api/trpc/health.ping reachable"
  else
    if [[ "$SWA_SKU" == "Standard" ]]; then
      ng "SWA /api/trpc/health.ping not reachable (expected reachable for Standard SKU linked backend; HTTP ${swa_api_code:-?})"
    else
      # Free SKU では linked backend を持たない設計 (#69)。SWA 配下に API が無いのは正常で、
      # フロントは Functions を直叩きする。ここは skip 扱い。
      warn "SWA /api/trpc/health.ping 応答なし: Free SKU は linked backend を持たない設計のため正常 (HTTP ${swa_api_code:-?})"
    fi
  fi
fi

# -------- Functions の認可 (#69) --------
# 常設・公開 URL では、課金の芯 (OpenAI) を持つ Functions が唯一の門。
# EasyAuth 有効なら「未認証で 200 が返る」= 門が開きっぱなし → NG にする。
# ここを reachable 判定のままにすると、認可が外れていても緑で通ってしまう。
if [[ -n "$FUNC_HOST" ]]; then
  set +e
  func_code=$(curl -sS -o /dev/null -w "%{http_code}" "https://$FUNC_HOST/api/trpc/health.ping")
  func_rc=$?
  set -e

  if [[ "$func_rc" -ne 0 ]]; then
    warn "Function App に到達できませんでした (デプロイ未完了 / ネットワーク; HTTP ${func_code:-?})"
  elif [[ "$EASYAUTH_ENABLED" == "true" ]]; then
    case "$func_code" in
      401|403) ok "Functions 未認証アクセスが $func_code で拒否された (EasyAuth の門が効いている)" ;;
      200) ng "EasyAuth 有効なのに未認証で 200。門が開いている (誰でも OpenAI を消費できる)" ;;
      *) warn "Functions 未認証アクセスが HTTP $func_code (401/403 を期待。デプロイ未完了の可能性)" ;;
    esac

    # CORS preflight が EasyAuth に巻き込まれて 401 になると、実ブラウザから一切呼べなくなる。
    if [[ -n "$SWA_HOST" ]]; then
      set +e
      pre_code=$(curl -sS -o /dev/null -w "%{http_code}" -X OPTIONS \
        -H "Origin: https://$SWA_HOST" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: authorization,content-type" \
        "https://$FUNC_HOST/api/trpc/health.ping")
      set -e
      case "$pre_code" in
        200|204) ok "CORS preflight (OPTIONS) が $pre_code で通った" ;;
        401|403) ng "CORS preflight が $pre_code。EasyAuth が OPTIONS まで弾いており実ブラウザから呼べない (runbook: entra-spa-auth-and-budget.md)" ;;
        *) warn "CORS preflight が HTTP $pre_code (200/204 を期待)" ;;
      esac
    fi
  else
    if [[ "$func_code" == "200" ]]; then
      ok "Function App /api/trpc/health.ping reachable (EasyAuth 無効)"
      warn "EasyAuth が無効です。公開 URL のまま運用するなら applyFunctionAuthLockdown=true と functionAuthEntraClientId を設定してください (#69)"
    else
      warn "Function App /api/trpc/health.ping not reachable (HTTP $func_code; デプロイ未完了の可能性)"
    fi
  fi
fi

# -------- Container Apps の露出 (#86) --------
# 認可の門は Functions だけでは足りない。OpenAI の鍵を握る ai-agent をはじめ、
# Container Apps は「もう一つの扉」であり、ここが無認可で開いていると
# Functions の 401 を実測して安心していても財布が焼かれる。
#
# 無いと何が静かに通るか: ingress の IP 制限が剥がれても (bicep 管理のリソースは
# 再デプロイで ingress ごと上書きされ、実際に voicevox で発生した) デプロイは緑のまま。
# 気づく手段が「たまたま点検したとき」しかなくなる。
section "Container Apps should reject anonymous access"
for CA in $(az containerapp list -g "$RG" --query "[].name" -o tsv 2>/dev/null); do
  CA_FQDN=$(az containerapp show -g "$RG" -n "$CA" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
  if [[ -z "$CA_FQDN" ]]; then
    ok "$CA: 外部 ingress なし (公開されていない)"
    continue
  fi

  set +e
  ca_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 "https://$CA_FQDN/health")
  ca_rc=$?
  set -e

  if [[ "$ca_rc" -ne 0 ]]; then
    # 接続自体が確立できない = 到達できない。拒否されている側なので想定どおり。
    ok "$CA: 匿名アクセスが接続段階で拒否された"
  elif [[ "$ca_code" == "403" || "$ca_code" == "401" ]]; then
    ok "$CA: 匿名アクセスが $ca_code で拒否された"
  elif [[ "$ca_code" == "200" ]]; then
    ng "$CA: 匿名アクセスが 200。無認可で公開されている (ingress の制限を確認: issue #86)"
  else
    warn "$CA: 匿名アクセスが HTTP $ca_code (401/403 もしくは接続拒否を期待)"
  fi
done

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
