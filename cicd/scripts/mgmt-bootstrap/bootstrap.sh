#!/usr/bin/env bash
# GitHub App + Key Vault + mgmt 層初回 apply のワンショットキット (Issue #387 / #390)。
#
# ============================================================================
# これが無いと何が静かに通るか
# ============================================================================
#
# 1. **pem がシェル履歴・ログ・コミットに残る** — 手作業だと `cat key.pem | az ...`
#    や「とりあえず環境変数へ」が起き、リポジトリが public なので露出 = 即失効対応
#    になる。このスクリプトは pem を**ファイルパスでしか受け取らず**、値を argv /
#    環境変数 / 標準出力に一切出さない。
# 2. **budgetContactEmails の渡し忘れ** — mgmt RG に予算が作られず、この RG の
#    コストがどの予算にも載らない (docs/runbooks/mgmt-layer-apply.md)。手順書では
#    「注意書き」だが、ここでは必須引数なので構造的に忘れられない。
# 3. **検証の省略** — 「apply した = できた」と読み、層タグ漏れ (撤収ガードから
#    見えない資源) や budget 不在に気づかない。ここでは検証が落ちたら exit≠0。
# 4. **GitHub 設定への意図しない適用** — terraform plan に差分 (add/change/destroy)
#    がある状態での apply は、#387 (enforce_admins の裁定待ち) を黙って踏み抜く。
#    ここでは import-only の plan しか apply させない。
# 5. **過剰権限の App が「動くので」そのまま定着する** — App は手動フォームで作る
#    (#497) ので、権限を 2 つに絞れているかを機械が見る場所はここしかない。
#    「administration が write か」だけの下限チェックだと、Contents や Actions を
#    余分に付けた App でも通ってしまい、その pem が Key Vault に入って以後の
#    plan/apply が過剰権限で回り続ける。ここでは**完全一致**で検証する。
#
# ============================================================================
# 信頼境界 (誰が・どの資格情報で・どこまでやるか)
# ============================================================================
#
# - **実行者は PO 本人のローカル環境のみ**。エージェント環境では動かさない
#   (pem という長期クレデンシャルを扱うため / ADR 0031 の思想)。
# - **Azure**: PO の `az login` 済みセッション。必要ロールは
#   Contributor (リソース作成) + Key Vault Secrets Officer (pem の格納 / data-plane)。
#   ロール割り当ての作成はしない (keyVaultCryptoUserPrincipalIds は既定の空)。
# - **GitHub**: pem から 10 分間有効の App JWT を作り、対象リポジトリに絞った
#   installation token (1 時間有効 / administration:write + metadata:read) を得る。
#   token は terraform の子プロセス環境にだけ渡し、標準出力には出さない。
# - **このスクリプトが書き込む先**: rg-mgmt-mindbox (Azure) / kv-dev-mindbox の
#   シークレット 1 本 / 一時ディレクトリ (terraform の作業用)。GitHub 設定は
#   import-only apply (= 実設定の変更ゼロ) 以外では書き込まない。
#
# 使い方・前提・巻き戻しは docs/runbooks/mgmt-layer-apply.md と同ディレクトリの
# README.md。テストは test_bootstrap.py (az / terraform / curl をスタブして全分岐 +
# check_permissions.py の判定を直接叩く単体)。
#
# ⚠️ `set -x` をこのファイルに足さないこと — トレースは変数展開 (installation
#    token を含む) をそのまま stderr に吐く。テストが grep で禁止を固定している。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IAC_DIR="$REPO_ROOT/cicd/iac"
TF_DIR="$REPO_ROOT/cicd/github/terraform"

# 名前は宣言側の既定値と揃える (cicd/iac/main-mgmt.bicep / persistent_layer_guard.py)。
# rg-mgmt-mindbox は撤収ガードが名指しで保護している名前なので変更可能にしない。
MGMT_RG="rg-mgmt-mindbox"
MGMT_LOCATION="japaneast"
VAULT_NAME="kv-dev-mindbox"
# 命名は既存の Key Vault シークレット規約 (`e2e-artifact-private-key`) に合わせる。
SECRET_NAME="github-app-mgmt-private-key"
BUDGET_NAME="budget-mgmt-mindbox"
# 定数。上書きノブにしない — 環境変数で API 先を差し替えられると、資格情報 (JWT /
# installation token) を別ホストへ送らせる足場になる (sec info)。
GITHUB_API="https://api.github.com"

# App に許す権限の**完全集合**。正典は README.md の「Repository permissions」の表で、
# ここはその機械側の写し (増やすときは README の 2 表と同じ PR で)。
# ⚠️ 「administration が write か」だけを見る**下限**チェックにしないこと — App は
#    手動フォームで作るので、Contents や Actions を余分に付けた App でも下限は満たす。
#    素通りさせると、過剰権限 App の pem をそのまま Key Vault に格納してしまう。
EXPECTED_PERMISSIONS=("administration=write" "metadata=read")

# ---------------------------------------------------------------------------
# 進捗の台帳 — 途中で落ちたとき「何が済んで何が済んでいないか」を必ず出す。
# bash 3.2 (macOS 既定) に連想配列が無いので、平行配列で持つ。
# ---------------------------------------------------------------------------
STEP_NAMES=(
  "前提確認 (ツール / 引数 / az ログイン)"
  "Azure: リソースグループ確保"
  "Azure: bicep コンパイル"
  "Azure: what-if (差分確認)"
  "Azure: mgmt 層 apply"
  "Azure: apply 後の検証 (層タグ / 鍵 / 予算)"
  "GitHub: App 認証確認 (JWT → installation token)"
  "Key Vault: pem 格納"
  "Terraform: mgmt 層 plan"
  "Terraform: mgmt 層 apply (import-only のみ)"
)
STEP_STATUS=() # 未着手 / 実行中 / 済 / スキップ / 失敗
for _ in "${STEP_NAMES[@]}"; do STEP_STATUS+=("未着手"); done
CURRENT_STEP=-1

step_begin() { CURRENT_STEP="$1"; STEP_STATUS[$1]="実行中"; echo; echo "== [$(($1 + 1))/${#STEP_NAMES[@]}] ${STEP_NAMES[$1]}"; }
step_done() { STEP_STATUS[$1]="済"; }
step_skip() { STEP_STATUS[$1]="スキップ ($2)"; }

print_ledger() {
  echo
  echo "---- 進捗 (何が済んで何が済んでいないか) ----"
  local i
  for i in "${!STEP_NAMES[@]}"; do
    printf '  [%s] %s\n' "${STEP_STATUS[$i]}" "${STEP_NAMES[$i]}"
  done
  echo "---------------------------------------------"
}

# pem の格納 (または格納済みの照合) が完了したかどうか。失敗時のエピローグで
# 「もうローカル pem を消してよいか」を言い分けるための状態。
PEM_STORED="false"
PEM_PATH=""

# 失敗時の共通エピローグ。台帳に加えて、**KV 格納が済んでいる場合だけ** pem 削除の
# 手順を出す (格納前に削除を促すと、失敗からの再実行で鍵を失う)。
failure_epilogue() {
  print_ledger >&2
  echo "再実行は安全です (冪等)。上の台帳で「失敗」のステップから原因を直してください。" >&2
  if [ "$PEM_STORED" = "true" ]; then
    cat >&2 <<EOF
なお pem の Key Vault 格納は完了しています。この失敗の切り分けにローカル pem が
不要なら、先に削除して構いません: rm "$PEM_PATH"
EOF
  fi
}

# fail-closed: どこで落ちても台帳を出して非ゼロで止まる。握り潰さない。
on_error() {
  local rc=$?
  if [ "$CURRENT_STEP" -ge 0 ]; then STEP_STATUS[$CURRENT_STEP]="失敗"; fi
  echo
  echo "ERROR: 手順の途中で失敗しました (exit=$rc)。" >&2
  failure_epilogue
  exit "$rc"
}
trap on_error ERR

# ⚠️ abort は明示 exit で止める。`false` で ERR トラップに落とす作りにしてはいけない —
#    `X || abort` の右辺では errexit / ERR トラップが無効化され (bash の仕様)、
#    abort したのに次のステップへ進んでしまう (fail-closed が静かに崩れる)。
abort() {
  if [ "$CURRENT_STEP" -ge 0 ]; then STEP_STATUS[$CURRENT_STEP]="失敗"; fi
  echo "ERROR: $*" >&2
  failure_epilogue
  exit 1
}

usage() {
  cat <<'USAGE'
使い方:
  bootstrap.sh --pem <pemファイルパス> --app-id <GitHub App ID> \
               --budget-email <通知先メール> [--owner <owner>] [--repo <repo>] \
               [--tf-apply] [--yes]

  --pem          GitHub App の private key (.pem) のファイルパス。
                 値は argv/環境変数では受け取らない (パスのみ)。
  --app-id       GitHub App の数値 ID (App の settings ページに表示)。
  --budget-email mgmt RG の予算アラート通知先。渡さないと予算が作られないため必須。
  --owner/--repo 適用先。省略時は git remote origin から導出する。
  --tf-apply     terraform plan が import-only (add/change/destroy 全て 0) の
                 ときだけ apply まで実行する。差分があれば適用せず止まる (#387)。
  --yes          確認プロンプトを省略する (what-if の目視は代替されない。注意)。
USAGE
}

confirm() {
  # $1: プロンプト。--yes なら聞かない。
  if [ "$ASSUME_YES" = "true" ]; then return 0; fi
  local ans
  read -r -p "$1 [y/N]: " ans
  if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
    abort "中断しました (ユーザーが承認しませんでした)"
  fi
}

# ---------------------------------------------------------------------------
# 引数
# ---------------------------------------------------------------------------
PEM_PATH=""
APP_ID=""
BUDGET_EMAIL=""
GH_OWNER=""
GH_REPO=""
TF_APPLY="false"
ASSUME_YES="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --pem) PEM_PATH="${2:?--pem に値がありません}"; shift 2 ;;
    --app-id) APP_ID="${2:?--app-id に値がありません}"; shift 2 ;;
    --budget-email) BUDGET_EMAIL="${2:?--budget-email に値がありません}"; shift 2 ;;
    --owner) GH_OWNER="${2:?--owner に値がありません}"; shift 2 ;;
    --repo) GH_REPO="${2:?--repo に値がありません}"; shift 2 ;;
    --tf-apply) TF_APPLY="true"; shift ;;
    --yes) ASSUME_YES="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; abort "不明な引数: $1" ;;
  esac
done

# ===========================================================================
# 0. 前提確認
# ===========================================================================
step_begin 0

[ -n "$PEM_PATH" ] || { usage >&2; abort "--pem は必須です"; }
[ -n "$APP_ID" ] || { usage >&2; abort "--app-id は必須です"; }
[ -n "$BUDGET_EMAIL" ] || { usage >&2; abort "--budget-email は必須です (渡さないと予算が作られない)"; }

case "$APP_ID" in
  ''|*[!0-9]*) abort "--app-id は数値です (App settings ページの App ID): '$APP_ID'" ;;
esac
case "$BUDGET_EMAIL" in
  *@*.*) : ;;
  *) abort "--budget-email がメールアドレスの形をしていません: '$BUDGET_EMAIL'" ;;
esac

for tool in az python3 curl openssl git; do
  command -v "$tool" >/dev/null 2>&1 || abort "$tool が見つかりません (PATH を確認)"
done
# plan は常に回すので terraform は常に必要 (1.5+ / import ブロックのため)
command -v terraform >/dev/null 2>&1 || abort "terraform が見つかりません (1.5+ が必要)"

[ -f "$PEM_PATH" ] || abort "pem ファイルがありません: $PEM_PATH"
# pem の中身は読み上げない。openssl に**パスだけ**渡して秘密鍵として妥当かを見る。
# 出力は捨てる (成功時の出力は無いが、失敗時のエラーは秘密を含まないので stderr に残す)。
openssl pkey -in "$PEM_PATH" -noout >/dev/null \
  || abort "pem が秘密鍵として読めません: $PEM_PATH (App settings の 'Generate a private key' で取得した .pem を指定)"

# 適用先の導出 — 宣言に適用先を書かない規律 (cicd/github/settings.yml) に合わせ、
# 既定値をコードに置かず実行文脈 (git remote) から取る。
if [ -z "$GH_OWNER" ] || [ -z "$GH_REPO" ]; then
  origin_url="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
  # 握り潰し注記: origin が無い場合はこの直後の空チェックで止まる (黙って続行しない)。
  derived="$(printf '%s' "$origin_url" | sed -E 's#\.git$##; s#^.*[:/]([^/]+)/([^/]+)$#\1 \2#')"
  GH_OWNER="${GH_OWNER:-${derived%% *}}"
  GH_REPO="${GH_REPO:-${derived##* }}"
fi
[ -n "$GH_OWNER" ] && [ -n "$GH_REPO" ] && [ "$GH_OWNER" != "$GH_REPO" ] \
  || abort "適用先を導出できません。--owner / --repo を明示してください"

# az のエラーは隠さない (ログイン切れの理由がそのまま stderr に出る)
acct_json="$(az account show --query '{name:name, id:id}' -o json)" \
  || abort "az にログインしていません (az login してから再実行)"
echo "Azure サブスクリプション:"
echo "$acct_json"
echo "適用先 GitHub リポジトリ: $GH_OWNER/$GH_REPO"
confirm "このサブスクリプション / リポジトリで進めますか?"
step_done 0

# ===========================================================================
# 1〜5. Azure mgmt 層 (docs/runbooks/mgmt-layer-apply.md の Steps 1〜5 相当 / 冪等)
# ===========================================================================
step_begin 1
az group create -n "$MGMT_RG" -l "$MGMT_LOCATION" --query properties.provisioningState -o tsv
step_done 1

step_begin 2
az bicep build --file "$IAC_DIR/main-mgmt.bicep"
step_done 2

step_begin 3
# what-if の出力は加工せずそのまま見せる (作られるものの目視が目的)。
az deployment group what-if \
  -g "$MGMT_RG" -n main-mgmt \
  -f "$IAC_DIR/main-mgmt.bicep" -p @"$IAC_DIR/main-mgmt.parameters.json" \
  -p budgetContactEmails="[\"$BUDGET_EMAIL\"]"
confirm "上の what-if の内容で apply しますか?"
step_done 3

step_begin 4
provisioning_state="$(az deployment group create \
  -g "$MGMT_RG" -n main-mgmt \
  -f "$IAC_DIR/main-mgmt.bicep" -p @"$IAC_DIR/main-mgmt.parameters.json" \
  -p budgetContactEmails="[\"$BUDGET_EMAIL\"]" \
  --query properties.provisioningState -o tsv)"
[ "$provisioning_state" = "Succeeded" ] \
  || abort "デプロイが Succeeded になりませんでした: '$provisioning_state'"
echo "デプロイ: Succeeded"
step_done 4

step_begin 5
# 検証は Runbook の Verification 2/3/5 を機械化したもの。1 つでも落ちたら止まる。
# (4 の撤収ガード検証は cleanup-env.sh の実行が必要で対話も長いので Runbook の手動確認に残す)

# 5a. 層タグ — 付いていない資源は撤収ガードから見えず、撤収で黙って消える。
untagged="$(az resource list -g "$MGMT_RG" -o json \
  | python3 -c 'import json,sys; rs=json.load(sys.stdin); print("\n".join(r["name"] for r in rs if (r.get("tags") or {}).get("mindInboxLayer")!="management"))')"
if [ -n "$untagged" ]; then
  abort "層タグ mindInboxLayer=management の無い資源があります (撤収ガードの対象外 = 撤収で消える): $untagged"
fi
echo "検証 5a: 層タグ OK"

# 5b. E2E trace 鍵がエクスポート不可で作られていること。
exportable="$(az keyvault key show --vault-name "$VAULT_NAME" -n e2e-artifacts --query key.exportable -o tsv)"
[ "$exportable" = "false" ] || abort "e2e-artifacts 鍵が exportable=$exportable です (false でなければ設計が崩れている)"
echo "検証 5b: 鍵は非エクスポート OK"

# 5c. 予算の**実在** — deployment output は見ない (パラメータの写しにすぎない)。
#     az consumption が使えない環境があるので ARM を直接叩く。存在しなければ
#     BudgetNotFound で非ゼロになり、沈黙と区別できる。
subscription_id="$(az account show --query id -o tsv)"
budget_contacts="$(az rest --method get \
  --url "https://management.azure.com/subscriptions/$subscription_id/resourceGroups/$MGMT_RG/providers/Microsoft.Consumption/budgets/$BUDGET_NAME?api-version=2023-05-01" \
  --query "properties.notifications.actual50.contactEmails | length(@)" -o tsv)" \
  || abort "予算 $BUDGET_NAME が確認できません (BudgetNotFound なら budgetContactEmails の渡し忘れ)"
[ "$budget_contacts" -ge 1 ] || abort "予算 $BUDGET_NAME の通知先が空です"
echo "検証 5c: 予算あり (通知先 $budget_contacts 件) OK"
step_done 5

# ===========================================================================
# 6. GitHub App の認証確認 — pem と App ID の対応 / インストール / 権限
# ===========================================================================
# ⚠️ **KV 格納より先に**やる (Codex P1)。逆順だと、別 App の「有効な」pem を渡した
#    取り違えで、KV の最新バージョンを誤った鍵で潰してから不一致に気づく —
#    既存資格情報の破壊経路になる。ここで pem↔App ID の対応まで実測してから格納する。
step_begin 6

# 作業用の一時ディレクトリ (認証ヘッダと terraform の作業場)。
# ヘッダをファイル渡しにするのは、`curl -H "Authorization: ..."` だと共有マシンで
# プロセス一覧 (ps) から資格情報が読めてしまうため。
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/mgmt-bootstrap.XXXXXX")"
chmod 700 "$WORKDIR"

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# App JWT (RS256 / 10 分)。秘密鍵は openssl にパスで渡す。JWT 自体も資格情報なので
# 標準出力へは出さず、ヘッダファイル経由でだけ使う。
jwt_iat=$(( $(date +%s) - 60 ))
jwt_exp=$(( jwt_iat + 600 ))
jwt_head_payload="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url).$(printf '%s' "{\"iat\":$jwt_iat,\"exp\":$jwt_exp,\"iss\":\"$APP_ID\"}" | b64url)"
jwt_sig="$(printf '%s' "$jwt_head_payload" | openssl dgst -sha256 -sign "$PEM_PATH" -binary | b64url)"
printf 'Authorization: Bearer %s.%s\n' "$jwt_head_payload" "$jwt_sig" > "$WORKDIR/jwt.h"
printf 'Accept: application/vnd.github+json\n' > "$WORKDIR/accept.h"

gh_api() { # $1: メソッド, $2: パス, $3: ヘッダファイル, [$4: body]
  local body_args=()
  [ $# -ge 4 ] && body_args=(--data "$4")
  curl -sS -H @"$3" -H @"$WORKDIR/accept.h" -X "$1" \
    -o "$WORKDIR/resp.json" -w '%{http_code}' "${body_args[@]}" "$GITHUB_API$2"
}

# 6a. pem と App ID が同じ App のものか。違う App の pem を掴む取り違えをここで止める。
code="$(gh_api GET /app "$WORKDIR/jwt.h")"
[ "$code" = "200" ] || abort "GET /app が $code でした (pem が App のものでない / 期限切れ / 失効の可能性)"
actual_app_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$WORKDIR/resp.json")"
app_slug="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("slug",""))' "$WORKDIR/resp.json")"
[ "$actual_app_id" = "$APP_ID" ] \
  || abort "pem が指す App ID ($actual_app_id) と --app-id ($APP_ID) が一致しません (取り違え)"
echo "App 確認 OK: $app_slug (id=$APP_ID)"

# 6b. 対象 owner にインストールされているか。
code="$(gh_api GET '/app/installations?per_page=100' "$WORKDIR/jwt.h")"
[ "$code" = "200" ] || abort "GET /app/installations が $code でした"
installation_id="$(python3 -c '
import json, sys
owner = sys.argv[2].lower()
for ins in json.load(open(sys.argv[1])):
    if ins.get("account", {}).get("login", "").lower() == owner:
        print(ins["id"]); break
' "$WORKDIR/resp.json" "$GH_OWNER")"
if [ -z "$installation_id" ]; then
  abort "App が $GH_OWNER にインストールされていません。https://github.com/apps/$app_slug/installations/new から $GH_OWNER/$GH_REPO にインストールして再実行してください"
fi

# 6c. installation token (1 時間 / 対象リポジトリに限定)。
code="$(gh_api POST "/app/installations/$installation_id/access_tokens" "$WORKDIR/jwt.h" "{\"repositories\":[\"$GH_REPO\"]}")"
[ "$code" = "201" ] || abort "installation token の発行が $code でした"
# token は変数と子プロセス環境にだけ置く。echo しない。
installation_token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$WORKDIR/resp.json")"
# 6d. 権限集合を **完全一致**で検証する (余分・値ちがい・不足のすべてを見る)。
#     判定そのものは check_permissions.py が持つ (シェルには埋めない —
#     cicd/CLAUDE.md「判定ロジックをシェルや workflow の中に埋めない」)。
#     戻りは「余分|値ちがい|不足」の 3 欄を | 区切りで 1 行 (空欄 = 問題なし)。
perm_report="$(python3 "$SCRIPT_DIR/check_permissions.py" \
  "$WORKDIR/resp.json" "${EXPECTED_PERMISSIONS[@]}")"
rm -f "$WORKDIR/resp.json" # token を含む応答ファイルは用が済んだら消す
# JWT ヘッダも以降使わない。作業ディレクトリに資格情報を残さない (sec P3-1)。
rm -f "$WORKDIR/jwt.h"

perm_extra="${perm_report%%|*}"
perm_rest="${perm_report#*|}"
perm_wrong="${perm_rest%%|*}"
perm_missing="${perm_rest##*|}"
if [ -n "$perm_extra$perm_wrong$perm_missing" ]; then
  # ⚠️ `[ ... ] && msg=...` で組み立ててはいけない — 偽のとき && が非ゼロを返し、
  #    errexit がここで台帳を出さずに落ちる (fail-closed の出力が崩れる)。
  msg="installation token の権限集合が想定と一致しません。"
  if [ -n "$perm_extra" ]; then
    msg="$msg"$'\n'"  余分:     $perm_extra"
  fi
  if [ -n "$perm_wrong" ]; then
    msg="$msg"$'\n'"  値ちがい: $perm_wrong"
  fi
  if [ -n "$perm_missing" ]; then
    msg="$msg"$'\n'"  不足:     $perm_missing"
  fi
  msg="$msg"$'\n'"  想定:     ${EXPECTED_PERMISSIONS[*]}"
  msg="$msg"$'\n'"App settings の Permissions & events を直し、Install App 側で更新後の権限を承認してから再実行してください (根拠は $SCRIPT_DIR/README.md の「App 権限の最小化と根拠」)。"
  abort "$msg"
fi
echo "installation token 発行 OK (権限 ${EXPECTED_PERMISSIONS[*]} と完全一致 / 対象: $GH_REPO / 有効 1 時間)"
step_done 6

# ===========================================================================
# 7. pem を Key Vault へ格納 (冪等) — App の実在確認が済んだ pem だけを入れる
# ===========================================================================
step_begin 7

pem_sha256="$(openssl dgst -sha256 -r "$PEM_PATH" | cut -d' ' -f1)"

# 既存シークレットの sha256 タグと比較して冪等にする。**値は読み戻さない** —
# 比較のためだけに秘密を取得して端末に出すのは本末転倒。
# `2>/dev/null` の握り潰し注記: ここで隠れるのは「シークレットが無い」エラーだけで、
# 無いことは existing_sha が空になる形で見えている (成功と区別できる)。
# 権限エラーもここでは隠れるが、直後の set が同じ権限で走って非ゼロで露見する。
existing_sha="$(az keyvault secret show --vault-name "$VAULT_NAME" -n "$SECRET_NAME" \
  --query 'tags.sha256' -o tsv 2>/dev/null || true)"

if [ "$existing_sha" = "$pem_sha256" ]; then
  echo "同じ内容のシークレットが既にあります (sha256 一致)。格納をスキップします (冪等)。"
  step_skip 7 "格納済み"
else
  if [ -n "$existing_sha" ]; then
    echo "既存シークレットと内容が異なります。新しいバージョンとして格納します (旧バージョンは Key Vault に残る)。"
  fi
  # ⚠️ `az keyvault secret set` は既定で**シークレット値を含む JSON を標準出力に返す**。
  #    --query id で id だけに絞るのは飾りではなく、pem を端末とログに出さないための 1 行。
  #    値の受け渡しは --file (パス渡し) のみ。--value (argv) は使わない。
  if ! secret_id="$(az keyvault secret set \
      --vault-name "$VAULT_NAME" -n "$SECRET_NAME" \
      --file "$PEM_PATH" \
      --tags "app-id=$APP_ID" "sha256=$pem_sha256" "purpose=github-settings-mgmt-terraform" \
      --query id -o tsv)"; then
    vault_scope="$(az keyvault show -n "$VAULT_NAME" --query id -o tsv 2>/dev/null || echo "<vault-resource-id>")"
    cat >&2 <<EOF
ERROR: シークレットの格納に失敗しました。
Key Vault は RBAC 方式で、Contributor / Owner でも data-plane (シークレット書き込み) は
既定で持ちません。自分に Key Vault Secrets Officer を付与してから再実行してください:

  az role assignment create --role "Key Vault Secrets Officer" \\
    --assignee "\$(az ad signed-in-user show --query id -o tsv)" \\
    --scope "$vault_scope"

(付与には Owner / User Access Administrator が必要。反映まで数分かかることがあります)
EOF
    abort "Key Vault へ格納できませんでした (上の指示を実行後に再実行。再実行は安全です)"
  fi
  echo "格納しました: $secret_id"
  step_done 7
fi
# ここから先の失敗では「pem は格納済み」を前提に削除案内を出してよい (Codex P2)。
PEM_STORED="true"

# ===========================================================================
# 8. Terraform: mgmt 層 (cicd/github/terraform) の plan
# ===========================================================================
step_begin 8

# state は保管しない方式 (import ブロック / cicd/github/terraform/README.md) なので、
# リポジトリを汚さないよう一時ディレクトリに写して回す。lockfile は readonly で
# リポジトリ側のハッシュ固定に従う。
mkdir -p "$WORKDIR/tf"
cp -R "$TF_DIR/." "$WORKDIR/tf"
terraform -chdir="$WORKDIR/tf" init -input=false -lockfile=readonly >"$WORKDIR/tf-init.log" 2>&1 \
  || { cat "$WORKDIR/tf-init.log" >&2; abort "terraform init が失敗しました"; }

# GITHUB_TOKEN は terraform の子プロセス環境にだけ載せる (このシェルには export しない)。
# 出力は一旦ファイルに受けてから見せる (集計行の解析と表示を同じ実体にするため)。
plan_rc=0
GITHUB_TOKEN="$installation_token" \
TF_VAR_github_owner="$GH_OWNER" \
TF_VAR_github_repository="$GH_REPO" \
  terraform -chdir="$WORKDIR/tf" plan -input=false -out=tfplan \
  >"$WORKDIR/tf-plan.log" 2>&1 || plan_rc=$?
cat "$WORKDIR/tf-plan.log"
[ "$plan_rc" -eq 0 ] || abort "terraform plan が失敗しました (rc=$plan_rc)。App の権限 / インストール範囲 / imports.tf の未検証 1〜3 (cicd/github/terraform/imports.tf) を確認"

# plan の集計行を読む。読めなかったら「差分不明」であって「差分なし」ではない。
plan_summary="$(grep -E '^(Plan:|No changes\.)' "$WORKDIR/tf-plan.log" | tail -1 || true)"
[ -n "$plan_summary" ] || abort "plan の集計行 (Plan: ... / No changes.) が読めませんでした。未検証のまま進めません"
changes="$(printf '%s' "$plan_summary" | python3 -c '
import re, sys
line = sys.stdin.read()
if line.startswith("No changes."):
    print(0)
else:
    m = re.search(r"(\d+) to add, (\d+) to change, (\d+) to destroy", line)
    print(sum(int(g) for g in m.groups()) if m else -1)
')"
[ "$changes" != "-1" ] && [ -n "$changes" ] || abort "plan の集計行を解釈できませんでした: $plan_summary"
echo "plan 集計: $plan_summary"
step_done 8

# ===========================================================================
# 9. Terraform: apply — import-only のときだけ
# ===========================================================================
step_begin 9

if [ "$changes" -ne 0 ]; then
  # #387 の領域。差分がある = 宣言 (現状の写し) と現実がズレている。どちらに揃えるかは
  # PO の裁定 (enforce_admins A/B) であって、このスクリプトが黙って倒してよい判断ではない。
  STEP_STATUS[9]="拒否 (plan に差分)"
  abort "plan に add/change/destroy の差分があります。apply しません — 差分の裁定は Issue #387 (上の plan 出力を Issue に貼って判断を仰いでください)"
fi

if [ "$TF_APPLY" != "true" ]; then
  step_skip 9 "--tf-apply 未指定"
  echo "plan は import-only でした。apply するには --tf-apply を付けて再実行してください (再実行は安全です)。"
else
  confirm "plan は import-only (GitHub 設定の変更ゼロ) です。apply しますか?"
  GITHUB_TOKEN="$installation_token" \
  TF_VAR_github_owner="$GH_OWNER" \
  TF_VAR_github_repository="$GH_REPO" \
    terraform -chdir="$WORKDIR/tf" apply -input=false tfplan
  echo "apply 完了 (import のみ / GitHub の実設定は変更していません)"
  step_done 9
fi

# ===========================================================================
# 締め
# ===========================================================================
print_ledger
cat <<EOF

すべて完了しました。次にやること:

1. **ローカルの pem を削除してください** (Key Vault に格納済み):

     rm "$PEM_PATH"

   自動では消しません — 格納が「成功に見えて壊れていた」場合 (取り違え・KV 障害) に
   鍵を失うと App の鍵再発行からやり直しになるため、削除の確定は人間の確認を挟みます。
   削除前に確認するなら: az keyvault secret show --vault-name $VAULT_NAME -n $SECRET_NAME --query 'tags' -o json
   (値ではなくタグの sha256 で照合。値を端末に出さないこと)

2. terraform の作業ディレクトリは $WORKDIR に残っています。
   資格情報は残していません (JWT ヘッダ・token 応答は使用後に削除済み) が、state と
   plan ログには GitHub 設定の写しが入ります。用が済んだら消してください: rm -rf "$WORKDIR"

3. 撤収ガードの検証 (Runbook の Verification 4) は対話が要るため未実施です:
   docs/runbooks/mgmt-layer-apply.md の手順で 2 本とも exit 3 になることを確認してください。
EOF
