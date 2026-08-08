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

# 一覧取得に失敗したり空だったりすると for が 0 回まわり、検査区画ごと無言でスキップされる。
# それでは「検査して問題なかった」のか「検査できていない」のかが出力から区別できず、
# この検査自身が「静かに通る」経路になる。取得の成否と期待するアプリの存在を明示的に確かめる。
# stderr は stdout に混ぜない。az containerapp は preview 警告バナーを stderr に出すことがあり、
# 2>&1 で取り込むと警告文が一覧に紛れ込み、単語分割されて偽のアプリ名としてループに入る
# (しかも rc=0 のままなので ng/warn にもならず気づけない)。
# --only-show-errors で警告を抑え、エラー出力は別ファイルに退避して失敗時だけ読む。
CA_ERR="$(mktemp)"
set +e
CA_NAMES="$(az containerapp list -g "$RG" --query "[].name" -o tsv --only-show-errors 2>"$CA_ERR")"
ca_list_rc=$?
set -e

if [[ "$ca_list_rc" -ne 0 ]]; then
  ng "Container Apps の一覧取得に失敗したため露出検査を実行できませんでした: $(head -c 160 "$CA_ERR")"
  CA_NAMES=""
elif [[ -z "$CA_NAMES" ]]; then
  warn "Container App が 1 件も見つかりませんでした (未デプロイなら正常。デプロイ済みならこの検査が効いていない)"
fi

# deployment outputs で「居るはず」とされているアプリが一覧に無ければ、検査対象の取りこぼし。
# voicevox エンジン自身も必ず含める: 過去に ingress 制限が 3 回剥がれた実績があり
# (bicep 管理なので手動設定が再適用で上書きされる)、恒久対策として internal ingress に
# したのが #86 / ADR 0017。一覧から漏れると「internal に戻っているか」を誰も見なくなる。
for expected in \
  "$(out aiAgentContainerAppName || true)" \
  "$(out voicevoxWrapperContainerAppName || true)" \
  "$(out voicevoxContainerAppName || true)"; do
  [[ -z "$expected" ]] && continue
  grep -qx "$expected" <<<"$CA_NAMES" \
    || ng "$expected が Container Apps 一覧に見つかりません (露出検査の対象から漏れています)"
done

# 行単位で読む (単語分割に頼らない)。空行はスキップ。
while IFS= read -r CA; do
  [[ -z "$CA" ]] && continue
  # external を先に見る。**internal ingress でも fqdn は空にならない**
  # (`*.internal.<region>.azurecontainerapps.io` が入る) ので、fqdn の有無だけで
  # 判定すると internal の CA を「外部公開されている」扱いで curl しに行き、
  # 名前解決に失敗して warn 止まり = 実際は閉じているのに未検証と報告してしまう。
  # 取得の成否 (rc) と「ingress が実際に無い」を分ける。`|| true` で潰すと、
  # 権限エラーや一時的な API 障害でも空文字になり「ingress なし = ok」に化ける。
  # それはこの検査自身が静かに通る経路で、まさにこの PR が塞ごうとしている失敗モード。
  # 一覧取得 (az containerapp list) を ng にしているのと同じ扱いに揃える。
  set +e
  CA_INGRESS=$(az containerapp show -g "$RG" -n "$CA" \
    --query "properties.configuration.ingress" -o json --only-show-errors 2>"$CA_ERR")
  ca_show_rc=$?
  set -e
  if [[ "$ca_show_rc" -ne 0 ]]; then
    ng "$CA: ingress の取得に失敗したため露出を判定できませんでした: $(head -c 160 "$CA_ERR")"
    continue
  fi

  CA_EXTERNAL=$(printf '%s' "$CA_INGRESS" | python3 -c \
    'import json,sys
raw = sys.stdin.read().strip()
d = json.loads(raw) if raw and raw != "null" else None
print("" if not d else d.get("external"))' 2>/dev/null || true)
  CA_FQDN=$(printf '%s' "$CA_INGRESS" | python3 -c \
    'import json,sys
raw = sys.stdin.read().strip()
d = json.loads(raw) if raw and raw != "null" else None
print("" if not d else (d.get("fqdn") or ""))' 2>/dev/null || true)

  if [[ -z "$CA_INGRESS" || "$CA_INGRESS" == "null" ]]; then
    ok "$CA: ingress なし (外部からも環境内からも HTTP で到達しない)"
    continue
  fi
  # Python の bool は "False"/"True" を出すが、大小文字を正規化して比較する
  # (JSON 側の型が変わっても判定が静かに外れないように)。
  if [[ "$(printf '%s' "$CA_EXTERNAL" | tr '[:upper:]' '[:lower:]')" == "false" ]]; then
    # internal は CAE の外から名前解決できない = 到達経路が存在しない (#86 / ADR 0017)。
    ok "$CA: internal ingress (CAE 内からのみ到達。外部に公開されていない)"
    continue
  fi
  if [[ -z "$CA_FQDN" ]]; then
    warn "$CA: external ingress だが fqdn を取得できず、露出の有無を判定できませんでした"
    continue
  fi

  # 判定はパスに依存させない。ingress の IP 制限はアプリに届く前に 403 を返すため、
  # **アプリ由来の応答が返ってきた時点で「到達できている」= 露出している**。
  # 404 でも NG にするのが要点: voicevox エンジンは /health を持たず (起動確認は /version)、
  # パス固定で 200 だけを見ると「制限が外れているのに 404 で見逃す」ことになる。
  set +e
  ca_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 30 "https://$CA_FQDN/")
  ca_rc=$?
  set -e

  if [[ "$ca_rc" -ne 0 ]]; then
    # ipSecurityRestrictions は TLS 確立後に HTTP 403 を返す (フロントプロキシで判定) ため、
    # **接続段階の失敗は「拒否された」ではなく別の異常**: DNS 障害 / コールドスタートの
    # タイムアウト / コンテナのクラッシュなど可用性側の問題である可能性が高い。
    # 露出の有無も未検証のままなので、ok にはせず warn で拾う。
    warn "$CA: 匿名アクセスの結果を判定できませんでした (curl rc=$ca_rc)。IP 制限が効いていれば 403 が返るはずで、接続失敗は可用性の異常 (DNS/コールドスタート/クラッシュ) の疑い。露出の有無も未検証なので手動で確認すること"
  elif [[ "$ca_code" == "403" ]]; then
    ok "$CA: 匿名アクセスが 403 で拒否された (ingress の IP 制限が効いている)"
  elif [[ "$ca_code" == "401" ]]; then
    ok "$CA: 匿名アクセスが 401 で拒否された (認証が要求されている)"
  else
    ng "$CA: 匿名アクセスに HTTP $ca_code が返った。アプリまで到達しており無認可で公開されている (issue #86)"
  fi
done <<< "$CA_NAMES"
rm -f "$CA_ERR"

# -------- Container Apps の image 据え置き検知 (#107) --------
# `az containerapp update --image <同一文字列>` は ARM 的に変更なし → 新 revision を作らない
# no-op。:latest 運用だと「デプロイ緑・image は前日のまま」が誰にも見えずに続く (2026-08-07 実測)。
#
# 無いと何が静かに通るか: デプロイが no-op に退行しても (スクリプト側の回帰 / 手動 latest 実行)、
# smoke は認可・疎通だけ見て緑のままになり、旧 image が本番相当環境で走り続ける。
# ここで「実際に稼働している revision の image タグ」を期待値 (EXPECTED_IMAGE_TAG) と突合する。
# 検証対象は ghcr の 2 サービスのみ (voicevox エンジンは bicep 管理の公式 image で対象外)。
section "Container Apps running image should match expected tag (#107)"
EXPECTED_IMAGE_TAG=${EXPECTED_IMAGE_TAG:-""}
[[ -z "$EXPECTED_IMAGE_TAG" ]] && warn "EXPECTED_IMAGE_TAG 未指定: タグ一致検証は skip し、稼働タグの表示のみ行う"

# 露出検査と同じく、エラー出力は捨てずに退避して失敗時に読む。2>/dev/null で潰すと
# 「権限が無い」「リソース名が違う」「API が一時的に落ちた」がすべて同じ空文字になり、
# NG は出ても**何を直せばいいか分からない**メッセージになる。
IMG_ERR="$(mktemp)"

for ca in \
  "$(out aiAgentContainerAppName || true)" \
  "$(out voicevoxWrapperContainerAppName || true)"; do
  [[ -z "$ca" ]] && continue

  # 判定材料は「実際に稼働している revision」(latestReadyRevisionName)。
  # app template の image だけを見ると「update は通ったが revision が Ready にならず
  # 旧 revision が走り続けている」ケースを見逃す。
  set +e
  ca_rev="$(az containerapp show -g "$RG" -n "$ca" \
    --query 'properties.latestReadyRevisionName' -o tsv --only-show-errors 2>"$IMG_ERR")"
  ca_rev_rc=$?
  set -e
  if [[ "$ca_rev_rc" -ne 0 ]]; then
    ng "$ca: latestReadyRevisionName の取得に失敗しました: $(head -c 160 "$IMG_ERR")"
    continue
  fi
  if [[ -z "$ca_rev" ]]; then
    ng "$ca: Ready な revision がありません (デプロイした revision が起動に失敗している疑い)"
    continue
  fi

  set +e
  ca_image="$(az containerapp revision show -g "$RG" -n "$ca" --revision "$ca_rev" \
    --query 'properties.template.containers[0].image' -o tsv --only-show-errors 2>"$IMG_ERR")"
  ca_image_rc=$?
  set -e
  if [[ "$ca_image_rc" -ne 0 ]]; then
    ng "$ca: 稼働 revision ($ca_rev) の image 取得に失敗しました: $(head -c 160 "$IMG_ERR")"
    continue
  fi
  if [[ -z "$ca_image" ]]; then
    ng "$ca: 稼働 revision ($ca_rev) の image が空です (コンテナ定義が想定と違う)"
    continue
  fi

  ca_tag="${ca_image##*:}"
  if [[ -n "$EXPECTED_IMAGE_TAG" ]]; then
    if [[ "$ca_tag" == "$EXPECTED_IMAGE_TAG" ]]; then
      ok "$ca: 稼働 revision $ca_rev の image tag = $ca_tag (期待値と一致)"
    else
      ng "$ca: 稼働 image tag '$ca_tag' が期待 '$EXPECTED_IMAGE_TAG' と不一致 (revision $ca_rev)。:latest 差し替え no-op か revision 未昇格の疑い (#107)"
    fi
  elif [[ "$ca_tag" == "latest" ]]; then
    warn "$ca: :latest で稼働中。どのコミットの image か追跡できず、据え置きも検知できない (#107)。IMAGE_TAG=sha-<full-sha> でのデプロイを推奨"
  else
    ok "$ca: 稼働 revision $ca_rev の image tag = $ca_tag (期待値未指定のため表示のみ)"
  fi
done
rm -f "$IMG_ERR"

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
