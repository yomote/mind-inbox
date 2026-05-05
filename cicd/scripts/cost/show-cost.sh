#!/usr/bin/env bash
set -euo pipefail

SUBSCRIPTION="${SUBSCRIPTION:-}"
TIMEFRAME="${TIMEFRAME:-MonthToDate}"
GROUP_BY="${GROUP_BY:-ResourceGroup}"
RG="${RG:-}"
TOP="${TOP:-30}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
API_VERSION="${API_VERSION:-2023-11-01}"

usage() {
  cat <<'EOF'
Show subscription cost (Month-to-Date by default), aggregated by resource group,
service, or location. Calls the Azure Cost Management REST API via `az rest`.

Environment variables:
  SUBSCRIPTION  Subscription ID or name (default: current az account)
  TIMEFRAME     MonthToDate | TheLastMonth | BillingMonthToDate |
                TheLastBillingMonth | WeekToDate | Custom (default: MonthToDate)
                With Custom, set START_DATE and END_DATE (YYYY-MM-DD).
  GROUP_BY      ResourceGroup | Service | Location | None (default: ResourceGroup)
  RG            Filter to a single resource group (default: no filter)
  TOP           Show top N rows in the breakdown (default: 30)
  START_DATE    YYYY-MM-DD, used when TIMEFRAME=Custom
  END_DATE      YYYY-MM-DD, used when TIMEFRAME=Custom
  API_VERSION   Cost Management API version (default: 2023-11-01)

Examples:
  ./scripts/cost/show-cost.sh
  GROUP_BY=Service ./scripts/cost/show-cost.sh
  RG=rg-dev-mind-inbox ./scripts/cost/show-cost.sh
  TIMEFRAME=TheLastMonth ./scripts/cost/show-cost.sh
  TIMEFRAME=Custom START_DATE=2026-04-01 END_DATE=2026-04-30 ./scripts/cost/show-cost.sh

Notes:
  - Cost Management data typically lags real usage by 8-24h.
  - Caller needs at least "Cost Management Reader" on the subscription.
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

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq command not found" >&2
  exit 1
fi

if [[ -n "$SUBSCRIPTION" ]]; then
  sub_info="$(az account show --subscription "$SUBSCRIPTION" -o json)"
else
  sub_info="$(az account show -o json)"
fi
sub_id="$(echo "$sub_info" | jq -r '.id')"
sub_name="$(echo "$sub_info" | jq -r '.name')"

case "$GROUP_BY" in
  ResourceGroup) dimension="ResourceGroupName" ;;
  Service)       dimension="ServiceName" ;;
  Location)      dimension="ResourceLocation" ;;
  None)          dimension="" ;;
  *)
    echo "ERROR: invalid GROUP_BY=${GROUP_BY} (use ResourceGroup | Service | Location | None)" >&2
    exit 1
    ;;
esac

build_body() {
  local jq_args=(
    --arg timeframe "$TIMEFRAME"
    --arg dimension "$dimension"
    --arg rg "$RG"
    --arg start_date "$START_DATE"
    --arg end_date "$END_DATE"
  )

  jq -n "${jq_args[@]}" '
    def grouping:
      if $dimension == "" then []
      else [{type: "Dimension", name: $dimension}]
      end;

    def rg_filter:
      if $rg == "" then null
      else {dimensions: {name: "ResourceGroupName", operator: "In", values: [$rg]}}
      end;

    def time_period:
      if $timeframe == "Custom" then
        {"from": ($start_date + "T00:00:00+00:00"), "to": ($end_date + "T23:59:59+00:00")}
      else null
      end;

    {
      type: "ActualCost",
      timeframe: $timeframe,
      dataset: ({
        granularity: "None",
        aggregation: {
          totalCost: {name: "PreTaxCost", function: "Sum"}
        },
        grouping: grouping
      } + (if rg_filter == null then {} else {filter: rg_filter} end))
    }
    + (if time_period == null then {} else {timePeriod: time_period} end)
  '
}

if [[ "$TIMEFRAME" == "Custom" && ( -z "$START_DATE" || -z "$END_DATE" ) ]]; then
  echo "ERROR: TIMEFRAME=Custom requires START_DATE and END_DATE (YYYY-MM-DD)" >&2
  exit 1
fi

body="$(build_body)"
url="https://management.azure.com/subscriptions/${sub_id}/providers/Microsoft.CostManagement/query?api-version=${API_VERSION}"

echo "================================================================"
echo " Cost report"
echo "================================================================"
echo " Subscription : ${sub_name} (${sub_id})"
if [[ "$TIMEFRAME" == "Custom" ]]; then
  echo " Timeframe    : Custom (${START_DATE} .. ${END_DATE})"
else
  echo " Timeframe    : ${TIMEFRAME}"
fi
echo " Group by     : ${GROUP_BY}"
[[ -n "$RG" ]] && echo " RG filter    : ${RG}"
echo "----------------------------------------------------------------"

result="$(az rest --method post --url "$url" --body "$body" -o json)"

cost_idx="$(echo "$result" | jq -r '
  .properties.columns
  | map(.name)
  | (index("PreTaxCost") // index("totalCost") // index("Cost"))
')"
currency_idx="$(echo "$result" | jq -r '.properties.columns | map(.name) | index("Currency")')"

if [[ "$cost_idx" == "null" || -z "$cost_idx" ]]; then
  echo "ERROR: Could not find cost column in API response" >&2
  echo "$result" | jq '.properties.columns' >&2
  exit 1
fi

total="$(echo "$result" | jq --argjson ci "$cost_idx" '
  [.properties.rows[]? | .[$ci]] | add // 0
')"

if [[ "$currency_idx" != "null" && -n "$currency_idx" ]]; then
  currency="$(echo "$result" | jq -r --argjson ci "$currency_idx" '.properties.rows[0][$ci] // ""')"
else
  currency=""
fi

printf " Total: %12.2f %s\n" "$total" "$currency"
echo "----------------------------------------------------------------"

if [[ -n "$dimension" ]]; then
  group_idx="$(echo "$result" | jq -r --arg d "$dimension" '.properties.columns | map(.name) | index($d)')"
  if [[ "$group_idx" == "null" || -z "$group_idx" ]]; then
    echo "WARN: Grouping column '${dimension}' not present in response; skipping breakdown." >&2
  else
    echo " Breakdown by ${GROUP_BY} (top ${TOP}):"
    echo "$result" \
      | jq -r --argjson gi "$group_idx" --argjson ci "$cost_idx" '
          .properties.rows
          | map({name: ((.[$gi] // "(unassigned)") | tostring), cost: .[$ci]})
          | sort_by(-.cost)
          | .[]
          | "\(.cost)\t\(.name)"
        ' \
      | head -n "$TOP" \
      | awk -F'\t' '{ printf "  %12.2f  %s\n", $1, $2 }'
  fi
fi

echo ""
echo " Note: Cost Management data typically lags real usage by 8-24h."
