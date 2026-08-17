# Azure アイコン (commit 対象)

`viz-structure.sh` が構成図のノードに埋め込む PNG。**このディレクトリは ignore しない。**

## なぜ commit するか

元は `download-azure-icons.sh` が実行のたびに公式パックを取得する設計だったが、
**配布元 `arch-center.azureedge.net` (Microsoft の旧 CDN) が廃止され、取得できなくなった**。

その結果 2026-08-09 に、CI が生成した図からアイコンが 19 個 → 0 個に落ち、
人間が読めない図で既存の図を上書きする事故が起きた。**図の再現性を外部 CDN の生死に
依存させない**ため、実体をリポジトリに置く。

現在の PNG は、事故前に生成された図 (`46b0e46` 時点の `infra_arch.svg`) に
埋め込まれていた base64 から復元したもの。公式パックと同一。

## ファイル名 = 種別マッピング

`viz-structure.sh` の `iconMap` が参照する名前。**変えると図からアイコンが消える。**

| ファイル                         | Azure リソース種別                         |
| -------------------------------- | ------------------------------------------ |
| `container-apps.png`             | `microsoft.app/containerapps`              |
| `container-apps-environment.png` | `microsoft.app/managedenvironments`        |
| `function-app.png`               | `microsoft.web/sites`                      |
| `static-web-app.png`             | `microsoft.web/staticsites`                |
| `app-service-plan.png`           | `microsoft.web/serverfarms`                |
| `storage-account.png`            | `microsoft.storage/storageaccounts`        |
| `log-analytics.png`              | `microsoft.operationalinsights/workspaces` |
| `vnet.png`                       | `microsoft.network/virtualnetworks`        |
| `private-endpoint.png`           | `microsoft.network/privateendpoints`       |
| `private-dns.png`                | `microsoft.network/privatednszones`        |
| `keyvault.png`                   | `microsoft.keyvault/vaults`                |
| `sql-server.png`                 | `microsoft.sql/servers`                    |

## 未対応 (アイコンが無く、素の箱で描かれる)

- **`microsoft.cognitiveservices/accounts`** — Azure OpenAI (`oai-*`) と Speech (`spch-*`)。
  事故前の図にも無く、復元元が存在しない。公式パックの入手経路が復活したら追加する
- `microsoft.sql/servers/databases` / `microsoft.network/privatednszones/virtualnetworklinks` —
  SQL は既定オフ (ADR 0013) のため現在は描画対象外

## 追加するとき

1. 64x64 の PNG を上の命名規則で置く
2. `viz-structure.sh` の `iconMap` に種別 → ファイル名を足す
3. `refresh-infra-diagram` が「図に 1 個も入っていなければ中止」を見ているので、
   壊れれば CI が赤になる

## 未入手のアイコン

`iconMap` には以下も登録済みだが、配布 CDN 廃止のため **PNG 実体は未入手**。
無い間は箱 (アイコン無しノード) で描画される。
公式パックを入手できたら、この名前で PNG を置くだけで図に載る。

| ファイル (未入手)        | Azure リソース種別                       |
| ------------------------ | ---------------------------------------- |
| `cosmos-db.png`          | `microsoft.documentdb/databaseaccounts`  |
| `cognitive-services.png` | `microsoft.cognitiveservices/accounts`   |
| `app-insights.png`       | `microsoft.insights/components` (#478)   |
| `action-group.png`       | `microsoft.insights/actiongroups` (#478) |

アイコンが出せないノードは無言で箱にならず、生成時に stderr へ
`WARN: アイコン未登録: <type>` (iconMap に無い) /
`WARN: アイコン PNG 未配置: <file>` (登録済みだが実体が無い) が出る (#478)。
