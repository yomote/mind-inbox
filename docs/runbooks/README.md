# Runbooks

運用手順の正典。**Trigger / Prerequisites / Steps / Verification / Rollback / Common Issues** で書く ([`template.md`](./template.md))。

- **なぜそうするか**は書かない → [ADR](../adr/README.md)
- **スクリプト個別の引数・仕様**は書かない → `cicd/scripts/*/README.md` / `cicd/iac/README.md`
- 長い調査ログ・障害の経緯は `<details>` に畳むか [debrief](../debrief/journal.md) へ

## 一覧

| Runbook                                                        | いつ使うか                                                                                                                                                                                           |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`status-page.md`](status-page.md)                             | **今どれが動いていて何が問題か**を見る / 監視項目を足す・外す                                                                                                                                        |
| [`local-fullstack-dev.md`](local-fullstack-dev.md)             | ローカルで VOICEVOX + BFF + frontend を起動し、声の UX を評価する                                                                                                                                    |
| [`merge-queue.md`](merge-queue.md)                             | Merge Queue を有効化・運用する / pm-accept 引き継ぎ (review-gate) を読む                                                                                                                             |
| [`claude-web-azure-access.md`](claude-web-azure-access.md)     | Claude Code (web) セッションから device-code で Azure を操作する                                                                                                                                     |
| [`azure-oidc-cd-setup.md`](azure-oidc-cd-setup.md)             | 常設 dev の CD (GitHub Actions + OIDC) を設定する / up・down する                                                                                                                                    |
| [`ghcr-images.md`](ghcr-images.md)                             | コンテナ image をビルド / タグ差し替えでデプロイ / ロールバックする                                                                                                                                  |
| [`entra-spa-auth-and-budget.md`](entra-spa-auth-and-budget.md) | 常設 dev の認可 (Entra SPA + Functions EasyAuth) と予算アラートを設定する                                                                                                                            |
| [`container-apps-auth-gate.md`](container-apps-auth-gate.md)   | Container Apps を組み込み認証 + Managed Identity で閉じる                                                                                                                                            |
| [`cosmos-persistence.md`](cosmos-persistence.md)               | 永続化 (Cosmos DB) を確認・切り戻す / ユーザーのデータを全部消す                                                                                                                                     |
| [`cd-watchdog.md`](cd-watchdog.md)                             | CD の赤を無人診断・修正する Routine を止める / 変える                                                                                                                                                |
| [`refresh-infra-diagram.md`](refresh-infra-diagram.md)         | 構成図を実環境から再生成する / 週次ワークフローを止める・頻度を変える                                                                                                                                |
| [`review-agents.md`](review-agents.md)                         | PR / リリース PR で独立 judge (code / security / QA / biz-owner / release) を回す                                                                                                                    |
| [`ux-probe-judge.md`](ux-probe-judge.md)                       | UX プローブの記録を採点する / 採点 Routine を運用する                                                                                                                                                |
| [`ops-inspect.md`](ops-inspect.md)                             | サンドボックス外の事実 (Azure の実態 / egress の外) をエージェントが取る                                                                                                                             |
| [`github-projects-setup.md`](github-projects-setup.md)         | **退役** — board は再建しない (ADR 0044。地図は `stream:*` ラベル / 見る手段は `/status`)                                                                                                            |
| [`claude-pr-review.md`](claude-pr-review.md)                   | **退役** — PR レビュー Routine は削除済み (#225 / ADR 0008 は Superseded)。基準は現役 ([`review-rubric.md`](../../.github/claude/review-rubric.md))、担い手は [`review-agents.md`](review-agents.md) |

## 新しく書くとき

```bash
cp docs/runbooks/template.md docs/runbooks/my-procedure.md   # kebab-case
```

その手順を**実際に踏んだ人**が書くか、レビューに入ること。机上のものは劣化が速い。
