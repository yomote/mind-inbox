#!/usr/bin/env bash
set -euo pipefail

SUBSCRIPTION="${SUBSCRIPTION:-}"
START_DATE="${START_DATE:-$(date -u +%Y-%m-01)}"
END_DATE="${END_DATE:-$(date -u -d "$(date -u +%Y-%m-01) +1 month -1 day" +%Y-%m-%d)}"
RG="${RG:-}"
COST_TYPE="${COST_TYPE:-ActualCost}"
SHOW_DAILY="${SHOW_DAILY:-true}"
API_VERSION="${API_VERSION:-2023-11-01}"

usage() {
  cat <<'EOF'
Show subscription cost actual + forecast for a given period (current month by default).
Calls the Azure Cost Management Forecast REST API via `az rest`.

Environment variables:
  SUBSCRIPTION  Subscription ID or name (default: current az account)
  START_DATE    Period start, YYYY-MM-DD (default: first day of current UTC month)
  END_DATE      Period end,   YYYY-MM-DD (default: last day of current UTC month)
  RG            Filter to a single resource group (default: no filter)
  COST_TYPE     ActualCost | AmortizedCost | Usage (default: ActualCost)
  SHOW_DAILY    true|false. Show per-day breakdown (default: true)
  API_VERSION   Cost Management API version (default: 2023-11-01)

Examples:
  ./scripts/cost/show-forecast.sh
  RG=rg-dev-mind-inbox ./scripts/cost/show-forecast.sh
  SHOW_DAILY=false ./scripts/cost/show-forecast.sh
  START_DATE=2026-06-01 END_DATE=2026-06-30 ./scripts/cost/show-forecast.sh

Notes:
  - Past days return Actual cost, future days return Forecast.
  - Forecast is a statistical projection from recent usage; volatile workloads diverge.
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

body="$(jq -n \
  --arg start_date "$START_DATE" \
  --arg end_date "$END_DATE" \
  --arg cost_type "$COST_TYPE" \
  --arg rg "$RG" \
  '{
    type: $cost_type,
    timeframe: "Custom",
    timePeriod: {
      "from": ($start_date + "T00:00:00+00:00"),
      "to":   ($end_date + "T23:59:59+00:00")
    },
    dataset: ({
      granularity: "Daily",
      aggregation: {
        totalCost: {name: "Cost", function: "Sum"}
      }
    } + (if $rg == "" then {} else {
      filter: {dimensions: {name: "ResourceGroupName", operator: "In", values: [$rg]}}
    } end)),
    includeActualCost: true,
    includeFreshPartialCost: false
  }')"

url="https://management.azure.com/subscriptions/${sub_id}/providers/Microsoft.CostManagement/forecast?api-version=${API_VERSION}"

echo "================================================================"
echo " Cost forecast"
echo "================================================================"
echo " Subscription : ${sub_name} (${sub_id})"
echo " Period       : ${START_DATE} .. ${END_DATE}"
echo " Cost type    : ${COST_TYPE}"
[[ -n "$RG" ]] && echo " RG filter    : ${RG}"
echo "----------------------------------------------------------------"

result="$(az rest --method post --url "$url" --body "$body" -o json)"

cols="$(echo "$result" | jq -c '.properties.columns | map(.name)')"
cost_idx="$(echo "$cols"   | jq -r 'index("Cost") // index("PreTaxCost")')"
date_idx="$(echo "$cols"   | jq -r 'index("UsageDate")')"
status_idx="$(echo "$cols" | jq -r 'index("CostStatus")')"
curr_idx="$(echo "$cols"   | jq -r 'index("Currency")')"

if [[ "$cost_idx" == "null" || -z "$cost_idx" ]]; then
  echo "ERROR: Could not find cost column in response" >&2
  echo "$result" | jq '.properties.columns' >&2
  exit 1
fi

actual_total="$(echo "$result" | jq --argjson ci "$cost_idx" --argjson si "$status_idx" '
  [.properties.rows[]? | select(.[$si] == "Actual") | .[$ci]] | add // 0
')"

forecast_total="$(echo "$result" | jq --argjson ci "$cost_idx" --argjson si "$status_idx" '
  [.properties.rows[]? | select(.[$si] == "Forecast") | .[$ci]] | add // 0
')"

projected_total="$(jq -n --argjson a "$actual_total" --argjson f "$forecast_total" '$a + $f')"

if [[ "$curr_idx" != "null" && -n "$curr_idx" ]]; then
  currency="$(echo "$result" | jq -r --argjson ci "$curr_idx" '.properties.rows[0][$ci] // ""')"
else
  currency=""
fi

printf " Actual to date  : %12.2f %s\n" "$actual_total"  "$currency"
printf " Forecast (rest) : %12.2f %s\n" "$forecast_total" "$currency"
echo "                   ----------"
printf " Projected total : %12.2f %s\n" "$projected_total" "$currency"
echo "----------------------------------------------------------------"

if [[ "$SHOW_DAILY" == "true" ]]; then
  if [[ "$date_idx" == "null" || -z "$date_idx" || "$status_idx" == "null" || -z "$status_idx" ]]; then
    echo "WARN: Daily breakdown columns missing in response; skipping." >&2
  else
    echo " Daily breakdown:"
    echo "$result" \
      | jq -r --argjson di "$date_idx" --argjson ci "$cost_idx" --argjson si "$status_idx" '
          .properties.rows
          | sort_by(.[$di])
          | .[]
          | "\(.[$di])\t\(.[$si])\t\(.[$ci])"
        ' \
      | awk -F'\t' '{
          d = $1
          # UsageDate is YYYYMMDD numeric; reformat to YYYY-MM-DD
          y = substr(d, 1, 4); m = substr(d, 5, 2); dd = substr(d, 7, 2)
          printf "   %s-%s-%s  %-8s %10.2f\n", y, m, dd, $2, $3
        }'
  fi
fi

echo ""
echo " Note: Actual data lags 8-24h. Forecast is statistical; treat as a guide."
