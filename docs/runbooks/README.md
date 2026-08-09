# Runbooks

運用手順の正典。**Trigger / Prerequisites / Steps / Verification / Rollback / Common Issues** で書く ([`template.md`](./template.md))。

- **なぜそうするか**は書かない → [ADR](../adr/README.md)
- **スクリプト個別の引数・仕様**は書かない → `cicd/scripts/*/README.md` / `cicd/iac/README.md`
- 長い調査ログ・障害の経緯は `<details>` に畳むか [debrief](../debrief/journal.md) へ

## 一覧

| Runbook                                                        | いつ使うか                                                                |
| -------------------------------------------------------------- | ------------------------------------------------------------------------- |
| [`local-fullstack-dev.md`](local-fullstack-dev.md)             | ローカルで VOICEVOX + BFF + frontend を起動し、声の UX を評価する         |
| [`claude-web-azure-access.md`](claude-web-azure-access.md)     | Claude Code (web) セッションから device-code で Azure を操作する          |
| [`azure-oidc-cd-setup.md`](azure-oidc-cd-setup.md)             | 常設 dev の CD (GitHub Actions + OIDC) を設定する / up・down する         |
| [`ghcr-images.md`](ghcr-images.md)                             | コンテナ image をビルド / タグ差し替えでデプロイ / ロールバックする       |
| [`entra-spa-auth-and-budget.md`](entra-spa-auth-and-budget.md) | 常設 dev の認可 (Entra SPA + Functions EasyAuth) と予算アラートを設定する |
| [`container-apps-auth-gate.md`](container-apps-auth-gate.md)   | Container Apps を組み込み認証 + Managed Identity で閉じる                 |
| [`cd-watchdog.md`](cd-watchdog.md)                             | CD の赤を無人診断・修正する Routine を止める / 変える                     |
| [`refresh-infra-diagram.md`](refresh-infra-diagram.md)         | 構成図を実環境から再生成する / 週次ワークフローを止める・頻度を変える     |
| [`claude-pr-review.md`](claude-pr-review.md)                   | PR 自動レビュー (LLM-as-a-judge) を動かす / 観点を変える / 止める         |
| [`review-agents.md`](review-agents.md)                         | リリース PR で独立 judge (security / QA / biz-owner / release) を回す     |
| [`ux-probe-judge.md`](ux-probe-judge.md)                       | UX プローブの記録を採点する / 採点 Routine を運用する                     |
| [`github-projects-setup.md`](github-projects-setup.md)         | GitHub Projects (実行ダッシュボード) をセットアップする                   |

## 新しく書くとき

```bash
cp docs/runbooks/template.md docs/runbooks/my-procedure.md   # kebab-case
```

その手順を**実際に踏んだ人**が書くか、レビューに入ること。机上のものは劣化が速い。
