# Cost monitoring scripts

予算管理のために、Azure サブスクリプションの利用料を確認するスクリプト。

## Show Cost

```bash
cd cicd
./scripts/cost/show-cost.sh
```

既定で **当月（MTD）の総コストとリソースグループ別の内訳** を表示します。

### 出力例

```
================================================================
 Cost report
================================================================
 Subscription : My Sub (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
 Timeframe    : MonthToDate
 Group by     : ResourceGroup
----------------------------------------------------------------
 Total:      1234.56 JPY
----------------------------------------------------------------
 Breakdown by ResourceGroup (top 30):
        950.00  rg-dev-mind-inbox
        200.00  rg-stg-mind-inbox
         84.56  NetworkWatcherRG
        ...

 Note: Cost Management data typically lags real usage by 8-24h.
```

### 主な環境変数

| 変数           | 既定値            | 役割                                                                                                    |
| -------------- | ----------------- | ------------------------------------------------------------------------------------------------------- |
| `SUBSCRIPTION` | 現在の az account | 対象サブスクリプション                                                                                  |
| `TIMEFRAME`    | `MonthToDate`     | `MonthToDate` / `TheLastMonth` / `BillingMonthToDate` / `TheLastBillingMonth` / `WeekToDate` / `Custom` |
| `GROUP_BY`     | `ResourceGroup`   | `ResourceGroup` / `Service` / `Location` / `None`                                                       |
| `RG`           | 無し              | 単一の RG にフィルタ                                                                                    |
| `TOP`          | `30`              | 内訳の上位 N 件                                                                                         |
| `START_DATE`   | 無し              | `TIMEFRAME=Custom` 時の開始日 (YYYY-MM-DD)                                                              |
| `END_DATE`     | 無し              | `TIMEFRAME=Custom` 時の終了日 (YYYY-MM-DD)                                                              |
| `API_VERSION`  | `2023-11-01`      | Cost Management API バージョン                                                                          |

### 例

```bash
# 当月のサービス別内訳
GROUP_BY=Service ./scripts/cost/show-cost.sh

# dev RG のみ
RG=rg-dev-mind-inbox ./scripts/cost/show-cost.sh

# 先月の総額
TIMEFRAME=TheLastMonth ./scripts/cost/show-cost.sh

# 任意期間
TIMEFRAME=Custom START_DATE=2026-04-01 END_DATE=2026-04-30 ./scripts/cost/show-cost.sh

# 内訳なしで合計だけ
GROUP_BY=None ./scripts/cost/show-cost.sh

# ヘルプ
./scripts/cost/show-cost.sh --help
```

### 必要な権限

- サブスクリプションに対する **Cost Management Reader**（または Reader / Contributor / Owner）。
- 内部では `az rest` で Cost Management REST API（`Microsoft.CostManagement/query`）を直接叩きます。az 拡張のバージョン揺れに依存しません。

### 注意

- Cost Management API のデータは実際の利用から **8〜24h 遅延** します。今日のリアルタイムコストは出ません。
- 表示は **Pre-Tax Cost**（税抜）。実際の請求額は税込で別途確認してください。
- 通貨は Azure 側の billing currency に従います（JPY / USD など）。
