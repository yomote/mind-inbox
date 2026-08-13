#!/usr/bin/env bash
set -euo pipefail

# 一度きり: GitHub Actions が Azure へ OIDC ログインするための連携を作る。
#   1. Entra アプリ登録(+ SP) を **2 つ** — 書き込み用 (deploy) と読み取り専用 (inspect)
#   2. federated credential（GitHub の OIDC subject を信頼 = main ブランチ限定）
#   3. 常設 dev の RG を **ここで** 作る（CD には作らせない）
#   4. ロール付与 — 書き込み用は **RG スコープのみ**（#46）
#
# 実行: device-code でログイン後（Owner 相当が要る）。出力の ID を GitHub の repo Variables に登録する。
# 冪等: 何度実行しても同じ状態に収束する。**既存の割当を削除することは無い**。
#
# ── なぜ RG スコープなのか（#46 / 2026-08-12） ────────────────────────────────
# 以前は `Contributor` を **サブスクリプションスコープ**で付けていた。理由は
# ADR 0009 のオンデマンド CD、つまり「使う時だけ RG ごと建てて、終わったら RG ごと
# 消す」運用で、RG の作成/削除・soft-delete の purge がサブスクリプションレベルの
# 操作だったため。
#
# その前提は **ADR 0013 で消えている**（dev は常設。夜間 teardown は廃止）。
# 日常の CD 経路（main マージ → provision.sh）が触るのは RG の中身だけで、
# RG そのものを作ったり消したりしない（provision.sh は RG が既に在れば
# `az group create` を呼ばない）。よって CD の identity に必要なのは RG スコープだけ。
#
# 残る「RG 作成 / 削除 + purge」は:
#   - 作成 … このスクリプト（初回・人間が Owner 権限で実行）
#   - 削除 … `cicd/scripts/env/cleanup-env.sh` を **人間が device-code で** 実行する
#            （deploy.yml の手動 down は CD の SP では権限不足で失敗する = 意図どおり。
#             不可逆な破壊操作を、main に書ける主体が持たないための設計）
#
# ── identity を 2 つに分ける理由 ─────────────────────────────────────────────
# 同じ client id を 4 本の workflow が共有していた:
#   deploy.yml (書き込み) / ops-inspect.yml / refresh-infra-diagram.yml /
#   golden-path-monitor.yml (いずれも読むだけ)
# 読むだけの 3 本が書き込み権限を持ち歩く必要は無いので、Reader だけの SP を分ける。
# 近く足す予定のドリフト検査 (実状態 vs bicep) も Reader で足りる。
#
# ── 付与するロール ───────────────────────────────────────────────────────────
# [書き込み用 = $APP_NAME]
#   1. Contributor @ RG
#        RG の中身（Functions / SWA / Container Apps / Cosmos / Key Vault / 予算…）を
#        作成・更新する。bicep は全て targetScope = 'resourceGroup'。
#   2. Role Based Access Control Administrator @ RG（ABAC condition つき）
#        Contributor は `Microsoft.Authorization/*/write` を **持たない**が、
#        bootstrap の bicep と deploy-ai-agent.sh は Managed Identity へ
#        Cognitive Services のロールを付ける（cicd/modules/bootstrap-core.bicep の
#        speechRoleAssignment / aiAgentOpenAiRoleAssignment、
#        cicd/scripts/deploy/deploy-ai-agent.sh の az role assignment create）。
#        そのため「ロールを付ける権限」だけを足す。ただし **付けられるロールを
#        OpenAI User / Speech User の 2 つに condition で縛る**ので、Owner や
#        User Access Administrator を配る経路にはならない。
#   3. (移行期のみ) Cost Management Reader @ subscription
#        ops-inspect の cost-summary が書き込み用 identity で動いている間だけ必要。
#        読み取り専用 identity へ 3 本を切り替えたら GRANT_COST_READER=false で外す。
#
# [読み取り専用 = ${APP_NAME}-ro]
#   1. Reader @ RG                      … 構成・リソースの参照 / Resource Graph
#   2. Cost Management Reader @ sub     … 課金データはサブスクリプション単位でしか読めない
#
# ⚠️ このスクリプトは **古いサブスクリプションスコープの割り当てを削除しない**。
#    新しい権限を付けてから、CD が緑であることを実測した上で人間が外す。
#    手順と検証方法: docs/runbooks/azure-oidc-cd-setup.md
#
# 関連: docs/runbooks/azure-oidc-cd-setup.md / ADR 0009 / ADR 0013 / Issue #46

REPO="${REPO:-yomote/mind-inbox}"
APP_NAME="${APP_NAME:-gha-oidc-mind-inbox-cd}"
READER_APP_NAME="${READER_APP_NAME:-${APP_NAME}-ro}"
BRANCH="${BRANCH:-main}"
# CD が触ってよい唯一の RG。deploy.yml / provision.sh の既定と一致させること
# （ズレると CD が AuthorizationFailed になる）。
RG="${RG:-rg-dev-mind-inbox}"
LOCATION="${LOCATION:-japaneast}"
# ABAC condition で「付けられるロール」を縛るか。false にすると RG 内で任意のロールを
# 付けられる（Owner 含む）ので、意図的に緩める時だけ false にする。
CONSTRAIN_RBAC_ADMIN="${CONSTRAIN_RBAC_ADMIN:-true}"
# 読み取り専用 identity を作るか。
CREATE_READER_IDENTITY="${CREATE_READER_IDENTITY:-true}"
# 書き込み用 identity に課金参照を残すか（移行期は true。切替が終わったら false）。
GRANT_COST_READER="${GRANT_COST_READER:-true}"

# 組み込みロール定義 ID（表示名はテナントの言語設定で揺れるので GUID で指定する）
ROLE_CONTRIBUTOR="b24988ac-6180-42a0-ab88-20f7382dd24c"
ROLE_RBAC_ADMIN="f58310d9-a9f6-439a-9e8d-f62e7b41a168"
ROLE_READER="acdd72a7-3385-48ef-bd42-f606fba81ae7"
ROLE_COST_READER="72fafb9e-0641-4937-9268-a91bfd8191a3"
# CD が MI に付けてよいロール（cicd/modules/bootstrap-core.bicep と同じ GUID）
ROLE_OPENAI_USER="5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
ROLE_SPEECH_USER="f2dc8367-1007-4938-bd23-fe263f013447"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}
need az
need jq
az account show >/dev/null

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
TENANT_ID="$(az account show --query tenantId -o tsv)"
SUB_SCOPE="/subscriptions/${SUBSCRIPTION_ID}"
RG_SCOPE="${SUB_SCOPE}/resourceGroups/${RG}"
SUBJECT="repo:${REPO}:ref:refs/heads/${BRANCH}"

# ── アプリ登録 + SP + federated credential を冪等に用意する ────────────────────
# 出力: グローバル変数 APP_ID_OUT / SP_OBJECT_ID_OUT（呼び出し側で受け取る）
ensure_app_with_federated_credential() {
  local display_name="$1"
  local app_id sp_object_id app_count fed_list existing cred_name fed_params extra

  echo "==> Entra app registration: $display_name"
  # 同名アプリが複数あると [0] をサイレント選択してしまう。意図しないアプリ操作を避けて中断。
  app_count="$(az ad app list --display-name "$display_name" --query 'length(@)' -o tsv)"
  if [[ "${app_count:-0}" -gt 1 ]]; then
    echo "ERROR: 同名アプリが ${app_count} 件あります（$display_name）。手動で整理してから再実行してください。" >&2
    exit 1
  fi
  app_id="$(az ad app list --display-name "$display_name" --query '[0].appId' -o tsv)"
  if [[ -z "${app_id}" ]]; then
    app_id="$(az ad app create --display-name "$display_name" --query appId -o tsv)"
  fi
  az ad sp show --id "$app_id" >/dev/null 2>&1 || az ad sp create --id "$app_id" >/dev/null
  sp_object_id="$(az ad sp show --id "$app_id" --query id -o tsv)"

  echo "    federated credential (subject = ${SUBJECT})"
  # 既存判定は jq でフィルタ（subject に ' 等が入っても JMESPath 直展開で壊れないように）。
  fed_list="$(az ad app federated-credential list --id "$app_id" -o json 2>/dev/null || echo '[]')"
  existing="$(jq -r --arg s "$SUBJECT" '[.[] | select(.subject == $s) | .name][0] // empty' <<<"$fed_list")"
  if [[ -z "${existing}" ]]; then
    # credential 名は Azure リソース名制約があるため英数._- 以外を - に正規化（ブランチに / 等が入る場合）。
    cred_name="gha-$(printf '%s' "$BRANCH" | tr -c 'A-Za-z0-9_.-' '-')"
    # JSON は jq で生成（ブランチ名に "や\ が入っても壊れないように raw 展開を避ける）。
    fed_params="$(jq -n --arg name "$cred_name" --arg subject "$SUBJECT" \
      '{name: $name, issuer: "https://token.actions.githubusercontent.com", subject: $subject, audiences: ["api://AzureADTokenExchange"]}')"
    az ad app federated-credential create --id "$app_id" --parameters "$fed_params" >/dev/null
    echo "    作成しました: $cred_name"
  else
    echo "    既存を再利用: $existing"
  fi

  # subject が main 固定であることが「main に書ける主体しかこの資格情報を使えない」の実体。
  # 後から手で足された広い subject（refs/heads/* / pull_request / 別リポ）は、その保証を
  # 静かに無効化するので、**在るなら見えるように**する（削除は不可逆なので人間が判断）。
  extra="$(jq -r --arg s "$SUBJECT" '[.[] | select(.subject != $s) | "\(.name)\t\(.subject)"] | .[]' <<<"$fed_list" || true)"
  if [[ -n "${extra}" ]]; then
    echo ""
    echo "⚠️  想定外の federated credential があります（main 以外からもこの SP を使えます）:"
    printf '%s\n' "$extra" | sed 's/^/      /'
    echo "    使っていないなら削除してください（不可逆）:"
    echo "      az ad app federated-credential delete --id ${app_id} --federated-credential-id <name>"
    echo ""
  fi

  APP_ID_OUT="$app_id"
  SP_OBJECT_ID_OUT="$sp_object_id"
}

# ── ロール付与（冪等） ────────────────────────────────────────────────────────
# 失敗（権限不足等）は握り潰さず exit する。ここを飲み込むと「✅ 完了」と表示されたまま、
# 後段 CD で初めて AuthorizationFailed として顕在化し、原因（ロール未付与）に
# たどり着けなくなる。
#
# 既存判定に --assignee は使わない（Microsoft Graph 参照を伴い、また roleDefinitionId の
# 大文字小文字/表示名に左右される）。scope 直下の一覧を引いて principalId +
# roleDefinitionId の末尾 GUID で自前照合する。
ensure_role_assignment() {
  local principal_id="$1" role_id="$2" scope="$3" label="$4" condition="${5:-}"
  local existing existing_condition args

  existing="$(az role assignment list --scope "$scope" -o json 2>/dev/null \
    | jq -r --arg p "$principal_id" --arg r "$role_id" \
        '[.[] | select((.principalId // "" | ascii_downcase) == ($p | ascii_downcase))
              | select(.roleDefinitionId // "" | ascii_downcase | endswith($r | ascii_downcase))][0] // empty')"

  if [[ -n "$existing" ]]; then
    existing_condition="$(jq -r '.condition // ""' <<<"$existing")"
    if [[ -n "$condition" && -z "$existing_condition" ]]; then
      echo "    ⚠️ 既存の割当を再利用: ${label}（**条件なし**で付いています）"
      echo "       条件付きに締め直すには、CD が緑であることを確認した上で削除 → 再実行:"
      echo "         az role assignment delete --ids \"$(jq -r '.id' <<<"$existing")\""
    else
      echo "    既存の割当を再利用: ${label}"
    fi
    return 0
  fi

  args=(role assignment create
    --assignee-object-id "$principal_id"
    --assignee-principal-type ServicePrincipal
    --role "$role_id"
    --scope "$scope" -o none)
  if [[ -n "$condition" ]]; then
    args+=(--condition "$condition" --condition-version "2.0")
  fi

  if az "${args[@]}"; then
    echo "    割当を作成: ${label}"
  else
    echo "ERROR: ロール付与に失敗しました: ${label}" >&2
    echo "       Owner 相当の権限で実行し直してください。付与されないと CD で AuthorizationFailed になります。" >&2
    exit 1
  fi
}

# ── 1. RG（CD には作らせないので、ここで作る） ────────────────────────────────
echo "==> Resource group: $RG ($LOCATION)"
# 常設 dev（ADR 0013）なので RG の作成は初回の 1 回だけで足りる。
if az group show -n "$RG" -o none 2>/dev/null; then
  echo "    既存を再利用"
else
  az group create -n "$RG" -l "$LOCATION" -o none
  echo "    作成しました"
fi

# ── 2. 書き込み用 identity（deploy.yml） ──────────────────────────────────────
ensure_app_with_federated_credential "$APP_NAME"
CD_APP_ID="$APP_ID_OUT"
CD_SP_OBJECT_ID="$SP_OBJECT_ID_OUT"

# 付けてよいロールを OpenAI User / Speech User に限定する ABAC condition（バージョン 2.0）。
# write は「これから付けるロール」、delete は「今付いているロール」を見るので両方書く。
RBAC_CONDITION=""
if [[ "$CONSTRAIN_RBAC_ADMIN" == "true" ]]; then
  RBAC_CONDITION="(
 (
  !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})
 )
 OR
 (
  @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals{${ROLE_OPENAI_USER}, ${ROLE_SPEECH_USER}}
 )
)
AND
(
 (
  !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})
 )
 OR
 (
  @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals{${ROLE_OPENAI_USER}, ${ROLE_SPEECH_USER}}
 )
)"
fi

echo "==> 書き込み用 identity のロール（RG スコープのみ）"
ensure_role_assignment "$CD_SP_OBJECT_ID" "$ROLE_CONTRIBUTOR" "$RG_SCOPE" "Contributor @ RG(${RG})"
if [[ "$CONSTRAIN_RBAC_ADMIN" != "true" ]]; then
  echo "    ⚠️ CONSTRAIN_RBAC_ADMIN=false — RG 内で任意のロールを付けられます（非推奨）"
fi
ensure_role_assignment "$CD_SP_OBJECT_ID" "$ROLE_RBAC_ADMIN" "$RG_SCOPE" \
  "RBAC Administrator @ RG(${RG})$( [[ "$CONSTRAIN_RBAC_ADMIN" == "true" ]] && echo "（OpenAI/Speech User 限定の条件つき）")" \
  "$RBAC_CONDITION"

if [[ "$GRANT_COST_READER" == "true" ]]; then
  # 移行期のみ: 読み取り専用 identity へ ops-inspect を移すまでの間、cost-summary を生かす。
  ensure_role_assignment "$CD_SP_OBJECT_ID" "$ROLE_COST_READER" "$SUB_SCOPE" \
    "Cost Management Reader @ subscription（読み取り専用 / 移行期のみ）"
else
  echo "    skip: Cost Management Reader（GRANT_COST_READER=false）"
  echo "          → 書き込み用 identity はサブスクリプションスコープの権限を一切持ちません"
fi

# ── 3. 読み取り専用 identity（ops-inspect / refresh-infra-diagram / golden-path-monitor） ──
RO_APP_ID=""
if [[ "$CREATE_READER_IDENTITY" == "true" ]]; then
  ensure_app_with_federated_credential "$READER_APP_NAME"
  RO_APP_ID="$APP_ID_OUT"
  RO_SP_OBJECT_ID="$SP_OBJECT_ID_OUT"

  echo "==> 読み取り専用 identity のロール"
  ensure_role_assignment "$RO_SP_OBJECT_ID" "$ROLE_READER" "$RG_SCOPE" "Reader @ RG(${RG})"
  ensure_role_assignment "$RO_SP_OBJECT_ID" "$ROLE_COST_READER" "$SUB_SCOPE" \
    "Cost Management Reader @ subscription（課金データはサブスクリプション単位）"
else
  echo "==> 読み取り専用 identity: skip（CREATE_READER_IDENTITY=false）"
fi

# ── 4. 旧: サブスクリプションスコープの書き込みロールが残っていないか ──────────
# 削除はしない（順序を誤ると CD が止まる）。**見えるようにするだけ**。
echo ""
echo "==> 旧いサブスクリプションスコープの割当を点検"
LEGACY="$(az role assignment list --scope "$SUB_SCOPE" -o json 2>/dev/null \
  | jq -r --arg p "$CD_SP_OBJECT_ID" --arg keep "$ROLE_COST_READER" \
      '[.[] | select((.principalId // "" | ascii_downcase) == ($p | ascii_downcase))
            | select((.roleDefinitionId // "" | ascii_downcase | endswith($keep | ascii_downcase)) | not)
            | "\(.roleDefinitionName // .roleDefinitionId)\t\(.id)"] | .[]' || true)"
if [[ -n "$LEGACY" ]]; then
  cat <<EOF

⚠️ 書き込み用 identity にサブスクリプションスコープの割当が残っています（これが #46 の High です）:
$(printf '%s\n' "$LEGACY" | sed 's/^/      /')

   **まだ消さないでください。** 先に新しい RG スコープの権限で CD が緑になることを
   実測し（docs/runbooks/azure-oidc-cd-setup.md の「権限の縮小（移行）」節）、それから外します:

      az role assignment delete --ids "<上の id>"

   ⚠️ 順序を逆にすると CD が AuthorizationFailed で止まります（不可逆ではないが、
      復旧には Owner 権限での再付与が必要）。
EOF
else
  echo "    残っていません（Cost Management Reader を除く）。"
fi

cat <<EOF

✅ 完了。次を GitHub の repo Variables に登録してください
   (Settings → Secrets and variables → Actions → Variables タブ):

   AZURE_CLIENT_ID=${CD_APP_ID}
   AZURE_TENANT_ID=${TENANT_ID}
   AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}
EOF
if [[ -n "$RO_APP_ID" ]]; then
  cat <<EOF
   AZURE_READER_CLIENT_ID=${RO_APP_ID}
       ↑ 読むだけの workflow（ops-inspect / refresh-infra-diagram / golden-path-monitor）が
         これを使うようになるまで、登録しても何も変わりません（登録しても安全）。
         切り替え手順: docs/runbooks/azure-oidc-cd-setup.md
EOF
fi
cat <<EOF

権限の要約（#46 後）:
   [書き込み用 ${APP_NAME}]
     Contributor                        @ ${RG_SCOPE}
     RBAC Administrator (条件つき)      @ ${RG_SCOPE}$( [[ "$GRANT_COST_READER" == "true" ]] && echo "
     Cost Management Reader             @ ${SUB_SCOPE} … 移行期のみ")
EOF
if [[ -n "$RO_APP_ID" ]]; then
  cat <<EOF
   [読み取り専用 ${READER_APP_NAME}]
     Reader                             @ ${RG_SCOPE}
     Cost Management Reader             @ ${SUB_SCOPE}
EOF
fi
cat <<EOF

   → 書き込み用は **RG の外を書き換えられない / RG を作れない・消せない**。
     環境の作り直し（down → up）は人間が device-code で行う:
       RG=${RG} ./cicd/scripts/env/cleanup-env.sh

その後、Actions → "deploy" → Run workflow で up を実行できます。
EOF
