#!/usr/bin/env bash
set -euo pipefail

# 実環境の「今どうなっているか」を read-only で一括ダンプする (ADR 0018 の①)。
#
# smoke-test.sh との違い:
#   smoke-test.sh  … 合否を出して CD を止めるためのもの (exit code に意味がある)
#   inspect-env.sh … 人が読む / PR に貼るためのもの。**判定しない**。exit code は
#                    「ダンプできたか」だけを表す (中身が良いか悪いかは表さない)
#
# 設計上の約束:
#   - **書き込み操作をしない**。az は show/list、curl は GET/OPTIONS だけ
#   - **測れなかったことを「異常なし」と書かない**。取得失敗は `(未検証: 理由)` と明示する。
#     ネットワーク制限のある環境から実行すると到達確認だけ落ちるので、そこを黙って
#     省略すると「確認した」に化ける (#88 のレビューで実際に指摘された類)
#   - **秘密を出さない**。SAS 付き URL はクエリ文字列を落として出す
#
# 使い方:
#   RG=rg-dev-mind-inbox DEPLOYMENT=main-bootstrap cicd/scripts/smoke-test/inspect-env.sh
#   RG=... DEPLOYMENT=... cicd/scripts/smoke-test/inspect-env.sh > /tmp/inspect.md

export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=${AZURE_EXTENSION_USE_DYNAMIC_INSTALL:-yes_without_prompt}

RG=${RG:-""}
DEPLOYMENT=${DEPLOYMENT:-""}
CURL_TIMEOUT=${CURL_TIMEOUT:-20}
# CORS preflight の Origin。既定は SWA の実ホスト名 (解決できたら) を使う。
CORS_ORIGIN=${CORS_ORIGIN:-""}

if [[ -z "$RG" || -z "$DEPLOYMENT" ]]; then
  echo "Usage: RG=<resource-group> DEPLOYMENT=<deployment-name> $0" >&2
  exit 2
fi

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 2; }
}
need az
need curl
need python3

az account show >/dev/null 2>&1 || {
  echo "Azure CLI not logged in. Run: az login" >&2
  exit 2
}

# --- 小道具 -------------------------------------------------------------------

ERRFILE="$(mktemp)"
trap 'rm -f "$ERRFILE"' EXIT

# az などを実行して stdout を RUN_OUT に、失敗理由を RUN_ERR に入れる。
# **stdout を素通しさせない**のが要点 (素通しすると整形前の生 JSON が
# レポートの途中に混ざる)。set -e で落とさず、呼び出し側が「取得できなかった」を
# 書けるようにする。
RUN_OUT=""
RUN_ERR=""
run() {
  RUN_OUT=""
  RUN_ERR=""
  local rc=0
  set +e
  RUN_OUT="$("$@" 2>"$ERRFILE")"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    RUN_ERR="$(tr '\n' ' ' <"$ERRFILE" | head -c 200)"
    [[ -z "$RUN_ERR" ]] && RUN_ERR="exit code $rc"
    RUN_OUT=""
    return 1
  fi
  return 0
}

# deployment output を 1 つ取る (無ければ空)。
out() {
  run az deployment group show -g "$RG" -n "$DEPLOYMENT" \
    --query "properties.outputs.${1}.value" -o tsv --only-show-errors || true
  echo "$RUN_OUT"
}

# JSON を python に食わせて 1 行取り出す。失敗したら空。
jq1() {
  python3 -c "$1" 2>/dev/null || true
}

# クエリ文字列 (SAS など) を落として URL を出す。
strip_query() { sed 's/?.*$/?<redacted>/'; }

# 匿名アクセスの結果をグローバルに置く。
# **command substitution で呼ばない**こと — サブシェルだと PROBE_ERR が親に伝わらず、
# 「到達できず: (理由が空)」という役に立たない出力になる (実際そうなった)。
PROBE_CODE=""
PROBE_ERR=""
probe() {
  local url="$1"
  shift
  PROBE_CODE=""
  PROBE_ERR=""
  local rc=0
  set +e
  PROBE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$CURL_TIMEOUT" "$@" "$url" 2>"$ERRFILE")"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    PROBE_ERR="$(tr '\n' ' ' <"$ERRFILE" | head -c 160)"
    [[ -z "$PROBE_ERR" ]] && PROBE_ERR="curl exit code $rc"
    PROBE_CODE=""
    return 1
  fi
  return 0
}

# probe の結果を表セル / 箇条書き用の 1 語にする。
probe_cell() {
  if [[ -n "$PROBE_CODE" ]]; then
    echo "\`$PROBE_CODE\`"
  else
    echo "**未検証** ($PROBE_ERR)"
  fi
}

h() {
  echo
  echo "## $1"
  echo
}

# --- 0. 環境 ------------------------------------------------------------------

SUB_NAME="$(az account show --query name -o tsv)"
SUB_ID="$(az account show --query id -o tsv)"
run az deployment group show -g "$RG" -n "$DEPLOYMENT" --query properties.timestamp -o tsv --only-show-errors || true
DEPLOY_TS="${RUN_OUT:-(取得失敗)}"

echo "# 実環境ダンプ — $RG"
echo
echo "read-only。判定はしない (合否は \`smoke-test.sh\` の役割)。"
echo
echo "| 項目 | 値 |"
echo "| --- | --- |"
echo "| Subscription | $SUB_NAME (\`$SUB_ID\`) |"
echo "| Resource group | \`$RG\` |"
echo "| Deployment | \`$DEPLOYMENT\` (最終適用 $DEPLOY_TS) |"

SWA_HOST="$(out staticSiteDefaultHostname)"
FUNC_HOST="$(out functionAppDefaultHostname)"
EASYAUTH_ENABLED="$(out functionEasyAuthEnabled)"

# Function App のリソース名は deployment output に無いので既定ホスト名から取る
# (`<name>.azurewebsites.net`)。output を増やすには再デプロイが要るため、
# 読み取り専用のこのスクリプトでは導出で済ませる。
FUNC_APP="${FUNC_HOST%%.*}"

[[ -z "$CORS_ORIGIN" && -n "$SWA_HOST" ]] && CORS_ORIGIN="https://$SWA_HOST"

# --- 1. 認可 ------------------------------------------------------------------

h "認可 (Functions EasyAuth)"

echo "| 項目 | 値 |"
echo "| --- | --- |"
echo "| bicep の \`functionEasyAuthEnabled\` | ${EASYAUTH_ENABLED:-(出力なし)} |"

if [[ -z "$FUNC_APP" ]]; then
  echo "| 実設定 | (未取得: \`functionAppDefaultHostname\` が空でアプリ名を導出できない) |"
else
  echo "| Function App | \`$FUNC_APP\` |"
  # **`az webapp auth show` は使わない。** あれは V1 (classic) 射影を返し、
  # authsettingsV2 が `Return401` でも `RedirectToLoginPage` と答える (この環境で実測)。
  # 設定の真実は authsettingsV2 にしか無いので ARM を直接 GET する。
  SITE_ID=""
  run az functionapp show -g "$RG" -n "$FUNC_APP" --query id -o tsv --only-show-errors && SITE_ID="$RUN_OUT"
  if [[ -z "$SITE_ID" ]]; then
    echo "| 実設定 | (取得失敗: リソース ID を引けない — $RUN_ERR) |"
  elif run az rest --method GET \
    --url "https://management.azure.com${SITE_ID}/config/authsettingsV2?api-version=2023-01-01" -o json; then
    AUTH_JSON="$RUN_OUT"
    gv() { echo "$AUTH_JSON" | jq1 "import json,sys;print(json.load(sys.stdin).get('properties',{}).get('globalValidation',{}).get('$1','(なし)'))"; }
    echo "| platform.enabled (EasyAuth 本体) | $(echo "$AUTH_JSON" | jq1 "import json,sys;print(json.load(sys.stdin).get('properties',{}).get('platform',{}).get('enabled','(なし)'))") |"
    echo "| requireAuthentication | $(gv requireAuthentication) |"
    echo "| unauthenticatedClientAction | $(gv unauthenticatedClientAction) |"
    echo "| excludedPaths | $(echo "$AUTH_JSON" | jq1 "import json,sys;v=json.load(sys.stdin).get('properties',{}).get('globalValidation',{}).get('excludedPaths');print(', '.join(v) if v else '(なし)')") |"
    echo "| allowedAudiences | $(echo "$AUTH_JSON" | jq1 "import json,sys;v=(((json.load(sys.stdin).get('properties',{}).get('identityProviders') or {}).get('azureActiveDirectory') or {}).get('validation') or {}).get('allowedAudiences');print(', '.join(v) if v else '(なし)')") |"
    echo "| issuer | $(echo "$AUTH_JSON" | jq1 "import json,sys;print((((json.load(sys.stdin).get('properties',{}).get('identityProviders') or {}).get('azureActiveDirectory') or {}).get('registration') or {}).get('openIdIssuer','(なし)'))") |"
  else
    echo "| 実設定 | (取得失敗: $RUN_ERR) |"
  fi
fi

echo
echo "**振る舞い** — 設定ではなく、実際に叩いた結果:"
echo

if [[ -z "$FUNC_HOST" ]]; then
  echo "- (未検証: deployment output \`functionAppDefaultHostname\` が空で叩き先が分からない)"
else
  PING_URL="https://$FUNC_HOST/api/trpc/health.ping"
  probe "$PING_URL" || true
  echo "- 未認証 GET \`$PING_URL\` → $(probe_cell)"

  if [[ -n "$CORS_ORIGIN" ]]; then
    probe "$PING_URL" -X OPTIONS -H "Origin: $CORS_ORIGIN" -H "Access-Control-Request-Method: POST" || true
    echo "- CORS preflight OPTIONS (Origin: \`$CORS_ORIGIN\`) → $(probe_cell)"
  else
    echo "- CORS preflight → (未検証: Origin が決まらない。\`CORS_ORIGIN=\` で明示指定できる)"
  fi
fi

# --- 2. 露出 (Container Apps) --------------------------------------------------

h "露出 (Container Apps)"

CA_NAMES=""
if run az containerapp list -g "$RG" --query "[].name" -o tsv --only-show-errors; then
  CA_NAMES="$RUN_OUT"
  if [[ -z "$CA_NAMES" ]]; then
    echo "Container App が 1 件も見つからない (この RG には無い)。"
  fi
else
  echo "(取得失敗: $RUN_ERR) — 露出を検査できていない。"
fi

if [[ -n "$CA_NAMES" ]]; then
  echo "| Container App | external | IP 制限ルール数 | 匿名 GET |"
  echo "| --- | --- | --- | --- |"
  while IFS= read -r CA; do
    [[ -z "$CA" ]] && continue
    if ! run az containerapp show -g "$RG" -n "$CA" -o json --only-show-errors; then
      echo "| $CA | (取得失敗) | (取得失敗) | **未検証** ($RUN_ERR) |"
      continue
    fi
    CA_JSON="$RUN_OUT"
    EXTERNAL="$(echo "$CA_JSON" | jq1 'import json,sys;d=json.load(sys.stdin);i=(d.get("properties") or {}).get("configuration",{}).get("ingress");print("(ingress なし)" if not i else i.get("external"))')"
    RULES="$(echo "$CA_JSON" | jq1 'import json,sys;d=json.load(sys.stdin);i=(d.get("properties") or {}).get("configuration",{}).get("ingress") or {};print(len(i.get("ipSecurityRestrictions") or []))')"
    FQDN="$(echo "$CA_JSON" | jq1 'import json,sys;d=json.load(sys.stdin);i=(d.get("properties") or {}).get("configuration",{}).get("ingress") or {};print(i.get("fqdn") or "")')"
    if [[ -z "$FQDN" ]]; then
      ACCESS="(ingress なし = 外から到達しない)"
    else
      probe "https://$FQDN/" || true
      ACCESS="$(probe_cell)"
    fi
    echo "| $CA | ${EXTERNAL:-(取得失敗)} | ${RULES:-?} | $ACCESS |"
  done <<<"$CA_NAMES"
  echo
  echo "> 匿名 GET が \`401\`/\`403\` なら門で止まっている。\`200\` 等でアプリに到達しているなら無認可で公開されている ([#86](https://github.com/yomote/mind-inbox/issues/86))。"
fi

# --- 3. 配置 (Functions) -------------------------------------------------------

h "配置 (Functions)"

if [[ -z "$FUNC_APP" ]]; then
  echo "(未取得: Function App 名を導出できない)"
else
  if run az functionapp function list -g "$RG" -n "$FUNC_APP" --query "[].name" -o tsv --only-show-errors; then
    if [[ -z "$RUN_OUT" ]]; then
      echo "- 認識されている関数: **0 件** (配置が入っていない / 起動に失敗している疑い)"
    else
      echo "- 認識されている関数: $(echo "$RUN_OUT" | tr '\n' ' ')"
    fi
  else
    echo "- 認識されている関数: (取得失敗: $RUN_ERR)"
  fi

  if run az functionapp config appsettings list -g "$RG" -n "$FUNC_APP" \
    --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value | [0]" -o tsv --only-show-errors; then
    if [[ -z "$RUN_OUT" ]]; then
      echo "- \`WEBSITE_RUN_FROM_PACKAGE\`: (未設定)"
    else
      echo "- \`WEBSITE_RUN_FROM_PACKAGE\`: \`$(echo "$RUN_OUT" | strip_query)\`"
    fi
  else
    echo "- \`WEBSITE_RUN_FROM_PACKAGE\`: (取得失敗: $RUN_ERR)"
  fi

  run az functionapp show -g "$RG" -n "$FUNC_APP" --query lastModifiedTimeUtc -o tsv --only-show-errors || true
  echo "- アプリ最終更新 (UTC): ${RUN_OUT:-(取得失敗)}"
fi

# --- 4. コスト ----------------------------------------------------------------

h "コスト"

if run az consumption budget list --query "[].{n:name,a:amount,s:currentSpend.amount,u:currentSpend.unit}" -o json --only-show-errors; then
  BUDGETS="$RUN_OUT"
  COUNT="$(echo "$BUDGETS" | jq1 'import json,sys;print(len(json.load(sys.stdin)))')"
  if [[ "$COUNT" == "0" ]]; then
    echo "予算アラートが **1 件も設定されていない**。"
  else
    echo "| 予算 | 上限 | 当月実績 |"
    echo "| --- | --- | --- |"
    echo "$BUDGETS" | jq1 '
import json,sys
for b in json.load(sys.stdin):
    spend = b.get("s")
    unit = b.get("u") or ""
    print("| {} | {} | {} |".format(
        b.get("n"), b.get("a"),
        "(実績なし)" if spend is None else "{} {}".format(round(float(spend), 2), unit).strip()))
'
  fi
else
  echo "(取得失敗: $RUN_ERR)"
fi

echo
echo "---"
echo
echo "生成: \`cicd/scripts/smoke-test/inspect-env.sh\` ([ADR 0018](../../../docs/adr/0018-runtime-verification-in-the-loop.md))"
