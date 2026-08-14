#!/usr/bin/env bash
# Deploy BFF (Node.js + Azure Functions v4 + tRPC) to the existing Function App.
#
# 1. apps/bff で pnpm install + tsc build
# 2. 配布用ツリーを別ディレクトリに作り (prod 依存だけ / node_modules を実体で展開)、
#    dist / node_modules / package.json / host.json を zip
#    → pnpm 既定の node_modules は symlink 構造で、Functions の zip deploy では壊れる。
#      `--node-linker=hoisted` で実体を作り、**symlink が 1 本も無いことを機械で確認**する (#420)
# 3. config-zip で Function App にデプロイ
# 4. Container App の FQDN を取得して AI_AGENT_BASE_URL / VOICEVOX_BASE_URL を BFF env に注入
# 5. Function App を再起動して env を反映
#
# 前提: main-bootstrap が完了して Function App / Container Apps が存在する状態
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}

need az
# pnpm / zip / zipinfo の有無は lib/build-bff-package.sh 側で確認する

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# ── Resolve deployment outputs ────────────────────────────────────────────────
echo "=== Resolving deployment outputs ==="
_OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$_OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

FUNC_APP_NAME="${FUNC_APP_NAME:-}"
if [[ -z "$FUNC_APP_NAME" ]]; then
  FUNC_HOST="$(_val functionAppDefaultHostname)"
  FUNC_APP_NAME="${FUNC_HOST%%.*}"
fi
if [[ -z "$FUNC_APP_NAME" ]]; then
  echo "ERROR: FUNC_APP_NAME is empty (set FUNC_APP_NAME or ensure deployment outputs.functionAppDefaultHostname exists)" >&2
  exit 1
fi

AI_AGENT_CA_NAME="${AI_AGENT_CA_NAME:-$(_val aiAgentContainerAppName)}"
VV_WRAPPER_CA_NAME="${VV_WRAPPER_CA_NAME:-$(_val voicevoxWrapperContainerAppName)}"

echo "RG=$RG"
echo "DEPLOYMENT=$DEPLOYMENT"
echo "FUNC_APP_NAME=$FUNC_APP_NAME"
echo "AI_AGENT_CA_NAME=${AI_AGENT_CA_NAME:-<unset>}"
echo "VV_WRAPPER_CA_NAME=${VV_WRAPPER_CA_NAME:-<unset>}"

# ── Preflight: clean WEBSITE_RUN_FROM_PACKAGE if pointing at a URL ───────────
echo "--- preflight (app settings) ---"
RUN_FROM_PACKAGE_VALUE="$(az functionapp config appsettings list -g "$RG" -n "$FUNC_APP_NAME" --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value | [0]" -o tsv)"
if [[ -n "$RUN_FROM_PACKAGE_VALUE" && "$RUN_FROM_PACKAGE_VALUE" == http* ]]; then
  echo "Deleting URL-based WEBSITE_RUN_FROM_PACKAGE (conflicts with config-zip)"
  az functionapp config appsettings delete -g "$RG" -n "$FUNC_APP_NAME" --setting-names WEBSITE_RUN_FROM_PACKAGE >/dev/null
fi
echo "Track deployment: https://$FUNC_APP_NAME.scm.azurewebsites.net/api/deployments/latest"

# ── Build BFF deployment package ──────────────────────────────────────────────
# パッケージ生成 (build → stage → symlink 検査 → zip) は az を使わない別スクリプトに
# 切り出してある。デプロイせずローカルで同じ工程を回して zip の中身を確認できる (#420)。
ZIP_PATH="$ROOT_DIR/.local/functionapp.zip"
export ZIP_PATH
echo ""
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/build-bff-package.sh"

# ── Deploy ────────────────────────────────────────────────────────────────────
echo ""
echo "=== Deploying to Function App ==="
cd "$ROOT_DIR"

# Linux Consumption では config-zip が「パッケージ配置は成功しているのに、直後の
# 状態確認 (SCM の deployments/latest) が Bad Request を返す」ことがある。
# set -e のままだと *成功したデプロイ* でスクリプトが止まり、この後の env 配線と
# フロント配置まで巻き添えで実行されない (2026-08-07 に実環境で発生)。
# よって CLI の終了コードを鵜呑みにせず、**実際の配置結果**で成否を判定する。
set +e
az functionapp deployment source config-zip -g "$RG" -n "$FUNC_APP_NAME" --src "$ZIP_PATH"
deploy_rc=$?
set -e

# この zip に含まれるはずの関数。「何か 1 つでも登録されていれば OK」にすると、
# 一部が欠けた壊れたデプロイや、前回のパッケージが残っているだけの状態を通してしまう。
EXPECTED_FUNCTIONS=(trpc tts)

if [[ "$deploy_rc" -ne 0 ]]; then
  echo "WARN: config-zip が非ゼロ終了 (rc=$deploy_rc)。実際の配置結果を検証します..." >&2

  # Azure 側の反映には数秒〜のラグがあるため、即断せず数回リトライする
  # (待たずに読むと、成功したデプロイを false negative で落として CD がフレーキーになる)。
  verified=0
  for attempt in 1 2 3 4 5; do
    # 検証1: 直前に URL 版を削除しているので、URL が入っていれば「今回の実行が書いた」証拠。
    new_pkg="$(az functionapp config appsettings list -g "$RG" -n "$FUNC_APP_NAME" \
      --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value | [0]" -o tsv 2>/dev/null || true)"
    # 検証2: プラットフォームがパッケージを読み込み、**期待する関数が揃って**認識されているか。
    fn_list="$(az functionapp function list -g "$RG" -n "$FUNC_APP_NAME" --query "[].name" -o tsv 2>/dev/null || true)"

    missing=()
    for fn in "${EXPECTED_FUNCTIONS[@]}"; do
      grep -qE "(^|/)${fn}$" <<<"$fn_list" || missing+=("$fn")
    done

    if [[ "$new_pkg" == http* && ${#missing[@]} -eq 0 ]]; then
      verified=1
      break
    fi
    echo "  (attempt $attempt/5) 反映待ち: package=${new_pkg:0:40}… missing=${missing[*]:-none}" >&2
    sleep 10
  done

  if [[ "$verified" -eq 1 ]]; then
    echo "  検証OK: パッケージが更新され、期待する関数が揃っています:" >&2
    echo "$fn_list" | sed 's/^/    - /' >&2
    echo "  → CLI の状態確認のみの失敗とみなして続行します" >&2
  else
    echo "ERROR: 配置自体が失敗しています" >&2
    echo "  package  = ${new_pkg:-<unset>}" >&2
    echo "  functions= ${fn_list:-<none>}" >&2
    echo "  missing  = ${missing[*]:-<none>}" >&2
    exit 1
  fi
fi

# ── Wire BFF env vars to live Container Apps ──────────────────────────────────
echo ""
echo "=== Wiring BFF env vars ==="

ai_agent_url=""
if [[ -n "$AI_AGENT_CA_NAME" ]]; then
  fqdn="$(az containerapp show -g "$RG" -n "$AI_AGENT_CA_NAME" \
    --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || true)"
  if [[ -n "$fqdn" ]]; then
    ai_agent_url="https://${fqdn}"
    echo "  AI_AGENT_BASE_URL=$ai_agent_url"
  else
    echo "  WARN: AI Agent Container App '$AI_AGENT_CA_NAME' not found or has no ingress; AI_AGENT_BASE_URL will not be set" >&2
  fi
fi

vv_wrapper_url=""
if [[ -n "$VV_WRAPPER_CA_NAME" ]]; then
  fqdn="$(az containerapp show -g "$RG" -n "$VV_WRAPPER_CA_NAME" \
    --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || true)"
  if [[ -n "$fqdn" ]]; then
    vv_wrapper_url="https://${fqdn}"
    echo "  VOICEVOX_BASE_URL=$vv_wrapper_url"
  else
    echo "  WARN: VOICEVOX Wrapper Container App '$VV_WRAPPER_CA_NAME' not found or has no ingress; VOICEVOX_BASE_URL will not be set" >&2
  fi
fi

settings=()
[[ -n "$ai_agent_url" ]] && settings+=("AI_AGENT_BASE_URL=$ai_agent_url")
[[ -n "$vv_wrapper_url" ]] && settings+=("VOICEVOX_BASE_URL=$vv_wrapper_url")

if [[ ${#settings[@]} -gt 0 ]]; then
  az functionapp config appsettings set \
    -g "$RG" -n "$FUNC_APP_NAME" \
    --settings "${settings[@]}" >/dev/null
  echo "  Applied ${#settings[@]} env vars; restarting Function App"

  # appsettings set 直後の restart は設定の伝播とレースし、ワーカーが旧スナップショット
  # (bicep full-replace 直後 = BASE_URL なし) のまま起動しうる (2026-08-08 実障害:
  # 結線済みのはずが tts が 204 stub のまま配信)。「設定したか」ではなく挙動
  # (tts が 204 でないこと) で収束を確認し、stub のままなら伝播を待って restart を
  # やり直す (ADR 0018)。トークンは golden-path.sh と同じ実行主体の az 資格で取る。
  SPA_CLIENT_ID="$(_val functionAuthEntraClientId)"
  converged=0
  for attempt in 1 2 3; do
    az functionapp restart -g "$RG" -n "$FUNC_APP_NAME" >/dev/null
    # 既知の限界: 鮮度の代理指標は tts (204/非204 が機械判定できる) のみ。
    # VOICEVOX 結線が無く AI_AGENT_BASE_URL だけ wire する構成ではこのループは
    # 自己修復できず、後段の golden-path (hop3/4 の stub 検出) が検知網になる
    if [[ -z "$SPA_CLIENT_ID" || -z "$vv_wrapper_url" ]]; then
      echo "  (収束確認スキップ: SPA client ID か VOICEVOX 結線が無く、tts 非 204 を期待できない)"
      converged=1
      break
    fi
    TOKEN="$(az account get-access-token --scope "api://$SPA_CLIENT_ID/.default" --query accessToken -o tsv 2>/dev/null || true)"
    if [[ -z "$TOKEN" ]]; then
      echo "  WARN: トークン取得不可のため収束確認をスキップ (後段の golden path が検証する)" >&2
      converged=1
      break
    fi
    sleep 20 # ホスト起動待ち
    # 000 (接続断) や restart 直後の一時 401/5xx は「収束の証拠」ではない (レビュー指摘)。
    # 確定応答 — 200 = env 反映済み / 204 = 旧スナップショット — が得られるまで probe を粘る
    code=""
    for probe in 1 2 3 4; do
      code="$(curl -s -o /dev/null -m 180 -w '%{http_code}' -X POST \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
        -d '{"text":"結線確認なのだ","speaker":3}' \
        "https://$FUNC_APP_NAME.azurewebsites.net/api/tts" || true)"
      case "$code" in
        200 | 204) break ;;
        *)
          echo "  (probe $probe/4) tts=$code — 起動途中/一時断として再試行" >&2
          sleep 15
          ;;
      esac
    done
    if [[ "$code" == "200" ]]; then
      echo "  tts=200 — env 反映を挙動で確認"
      converged=1
      break
    elif [[ "$code" == "204" ]]; then
      echo "  (attempt $attempt/3) tts=204 stub — 設定が未反映。伝播を待って再 restart" >&2
      sleep 20
    else
      echo "  (attempt $attempt/3) tts=$code — 確定応答が得られない。再 restart して再試行" >&2
    fi
  done
  if [[ "$converged" -ne 1 ]]; then
    echo "ERROR: env 反映を確認できなかった (最後の tts=${code:-<none>})。204 なら VOICEVOX_BASE_URL 未反映、それ以外なら BFF/wrapper の死活を確認すること" >&2
    exit 1
  fi
else
  echo "  No Container Apps available to wire; BFF will use stub responses"
fi

# ── Smoke ─────────────────────────────────────────────────────────────────────
echo ""
echo "--- smoke (direct function) ---"
# Even if EasyAuth returns 401, status line is what we care about (not 404 / 500).
curl -sS -D- -o /dev/null "https://$FUNC_APP_NAME.azurewebsites.net/api/trpc/health.ping" | sed -n '1,10p' || true
