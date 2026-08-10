# Azureトポロジ図（見栄え強化版）

このスクリプトは、Azure Resource Graph の結果から
「RG → VNet → Subnet」クラスタ付きのトポロジ図を生成します。

> **通常は手で回す必要はありません。** 週次で CI が実行し、差分があれば PR を開きます。
> 運用手順は [Runbook](../../../docs/runbooks/refresh-infra-diagram.md)。
> 以下はローカルで回したいときの手順です。

- Azure公式アイコン対応
- 関係線（Private Link / Linked Backend など）の色分け
- コードベース由来の論理依存関係を補完（OpenAI / VOICEVOX / Cosmos / Speech 連携など）— 実装は [`enrich.py`](enrich.py)（pytest 対象）
- ブラウザ擬似ノード — SWA は linked backend を使わない設計 (ADR 0013) で Azure リソース間の構造エッジが無いため、「User (Browser) → SWA / BFF / Speech」を描いて入口の流れを可視化する
- 各リソースの役割を別表で出力（TSV / Markdown）
- 凡例付き
- VS Codeプレビュー互換（PNG埋め込み）

## 1) 公式アイコンを取得

```bash
cd cicd/scripts/viz-structure
./download-azure-icons.sh
```

## 2) 図を生成

```bash
cd cicd/scripts/viz-structure
./viz-structure.sh --subs "<subscription-id-or-name>" --rgs "<rg-name>"
```

必要に応じて、コード検索のルートを変更できます。

```bash
./viz-structure.sh --subs "<subscription-id-or-name>" --rgs "<rg-name>" --codebase-root .
```

> `icons` 配下に PNG があれば自動で利用します。
> 手動指定したい場合は `--icons "$(pwd)/icons"` を使ってください。

## 3) オフラインで検証する（Azure アクセス不要）

`az graph query` の `.data` 相当の JSON（リソースの配列）を渡すと、az を呼ばずに
図・役割表の生成ロジックだけを検証できます。**`--docs-dir` を必ず指定すること** —
省略すると commit 対象の `docs/cicd/iac/` を合成データで上書きしてしまいます。

```bash
./viz-structure.sh \
  --subs "<GUID>" --rgs "rg-dev-mind-inbox" \
  --resources-json /tmp/resources.json \
  --docs-dir /tmp/viz-docs --out /tmp/viz-out
```

論理エッジ・役割表のロジック（`enrich.py`）は pytest でもテストされます
（`npm run test:fast` の `test:scripts` に含まれる）。

## 出力

- `artifacts/topology/latest/topology.svg`
- `artifacts/topology/latest/topology.dot`
- `artifacts/topology/latest/graph.json`
- `artifacts/topology/latest/logical-edges.json`
- `artifacts/topology/latest/resource-roles.tsv`
- `artifacts/topology/latest/resource-roles.md`
- `docs/cicd/iac/infra_arch.svg`
- `docs/cicd/iac/infra_arch_resource_roles.md`（commit 対象の生成物。手書きで直さない）
