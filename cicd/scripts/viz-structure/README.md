# Azureトポロジ図（見栄え強化版）

このスクリプトは、Azure Resource Graph の結果から
「RG → VNet → Subnet」クラスタ付きのトポロジ図を生成します。

- Azure公式アイコン対応
- 関係線（Private Link / Linked Backend など）の色分け
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

> `icons` 配下に PNG があれば自動で利用します。
> 手動指定したい場合は `--icons "$(pwd)/icons"` を使ってください。

## 出力

- `artifacts/topology/latest/topology.svg`
- `artifacts/topology/latest/topology.dot`
- `artifacts/topology/latest/graph.json`
- `docs/iac/topology.svg`
