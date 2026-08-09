# 構成図の週次自動再生成 (refresh-infra-diagram)

> 実体: [`.github/workflows/refresh-infra-diagram.yml`](../../.github/workflows/refresh-infra-diagram.yml)。
> `docs/cicd/iac/` の構成図は `viz-structure.sh` の生成物だが、実行に Azure アクセスが要るため
> 「誰も手元で回さない → 静かに陳腐化する」状態だった。それを CI に載せたもの。

## Trigger

- 自動: **毎週月曜 05:00 JST** (cron は UTC `0 20 * * 0`)
- 手動: Actions → `refresh-infra-diagram` → Run workflow
- インフラを変えた直後に図を合わせたいときも手動 dispatch

## Prerequisites

- リポジトリ Variables に `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` ([`azure-oidc-cd-setup.md`](azure-oidc-cd-setup.md))。**未設定なら赤くせず skip する**
- CD 用 identity が対象 RG を Resource Graph で読めること (Reader 相当)

## Steps

自動なので通常は不要。手動で回す場合:

```bash
# Actions から dispatch する (推奨)。ローカルで回すなら:
cicd/scripts/viz-structure/viz-structure.sh --subs "<sub>" --rgs "rg-dev-mind-inbox"
```

ワークフローがやること: OIDC ログイン → graphviz と `az` の resource-graph 拡張を入れる → 再生成 → **差分があるときだけ** `chore/refresh-infra-diagram` ブランチへ push して PR を開く (main へ直 push しない)。

## Verification

- 差分なし → run の Summary に `構成図に差分なし` の notice。**PR は作られない (正常)**
- 差分あり → `chore/refresh-infra-diagram` の PR が開く。**中身を確認すること** — 想定外のリソース増減が写っているなら、それは図の問題ではなく**インフラ側の変化**
- 既に PR が開いていれば新規に立てず、そこへ force push で反映する

## Rollback

- **止める**: Actions → `refresh-infra-diagram` → `⋯` → Disable workflow (ファイルは残してよい)
- **頻度を変える**: workflow の `cron` を変更 (UTC 指定)
- 自動で開いた PR は close するだけでよい。次回 run で作り直される

## Common Issues

| 症状                                   | 原因 / 対処                                                                                                                              |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| skip される (warning)                  | OIDC の 3 変数が未登録。[`azure-oidc-cd-setup.md`](azure-oidc-cd-setup.md)                                                               |
| `AADSTS700213` で Azure login が落ちる | ブランチ ref から dispatch した。federated credential は main 用なので、**main の workflow として実行する**                              |
| `az graph` が権限エラー                | CD identity に対象 RG の読み取り権限が無い。Reader 以上を付与する                                                                        |
| 図にアイコンが出ない                   | 公式アイコンの取得に失敗 (`continue-on-error`)。図自体は出るので実害なし。恒久的に出ないなら `download-azure-icons.sh` の ZIP URL を確認 |
| 毎週 PR が開くが中身が同じに見える     | `generatedAt` 等のタイムスタンプ差分。うるさければ cron を伸ばす                                                                         |

## Related

- [`azure-oidc-cd-setup.md`](azure-oidc-cd-setup.md) — OIDC の初回設定
- `cicd/scripts/viz-structure/README.md` — スクリプト本体の仕様とローカル実行
