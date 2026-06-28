# 0009. デプロイは GitHub Actions のオンデマンド CD（手動 up/down + 夜間 teardown）で行う

- Status: Proposed
- Date: 2026-06-28
- Deciders: omoteforlab
- Consulted: —
- Informed: —

関連: [ADR 0006](0006-azure-access-via-device-code.md)（無人 LLM 運用エージェントは見送り） / [ADR 0003](0003-two-phase-bicep.md)（2-phase Bicep） / [ADR 0002](0002-container-apps-not-aks.md)（scale-to-zero）

## Context and Problem Statement

CI（`test.yml`: test / lint / build）は GitHub Actions に乗っているが、**CD（デプロイ）が無く手動**。
実環境を「ぱっと触りたい」（特にスマホから = 公開 URL が要る）一方、**常時起動はコストになる**ため、
「使う時だけ立て、終わったら ¥0 に戻す」運用を CI/CD に正式に組み込みたい。

[ADR 0006](0006-azure-access-via-device-code.md) は「**無人で動く LLM 運用エージェント**（Reader 診断＋自動操作、
`ANTHROPIC_API_KEY` 常設）」を見送った。本件は LLM をCIで動かす話ではなく、**素のデプロイパイプライン**で
あり論点が異なる。ただし「GitHub Actions から Azure を触る」点は 0006 が機構として保留した領域なので、
新しい判断として記録する。

## Decision Drivers

- 公開 URL で（スマホ含め）すぐ触れる環境を、必要な時に得られる
- 普段は ¥0（使わない時に課金を残さない）/ 消し忘れの保険がある
- 静的シークレットを保存しない（[ADR 0006](0006-azure-access-via-device-code.md) のドライバー継承）
- 既存 Iact（2-phase Bicep）/ デプロイスクリプト / `cleanup-env.sh` を再利用し、二重管理しない
- 無人で「考える」エージェントは増やさない（0006 の見送りは維持）

## Considered Options

- Option A: **オンデマンド CD**。`workflow_dispatch` で `up`/`down` を手動実行 + 夜間 schedule で自動 teardown。認証は Azure OIDC（federated credential）
- Option B: **main マージで自動デプロイ**（常時起動の CD）
- Option C: CD を組まず、**device-code セッションから手動スクリプト**のまま（[ADR 0006](0006-azure-access-via-device-code.md) の延長）

## Decision Outcome

Chosen option: **Option A（オンデマンド CD + OIDC + 夜間 teardown ガード）**。

「使う時だけ立てて潰す」という運用要件に最も合い、OIDC により**保存シークレット0**で 0006 のドライバーを
満たす。立ち上げは常に**手動 `up`**（人の意思）であり、コストは使った分だけ。**夜間 schedule の `down`** が
消し忘れの保険になる（落ちていれば no-op に近い）。中身は既存スクリプト（`provision.sh` = IaC `最短ルート`
＋コンテナ反映、`cleanup-env.sh` = 撤収）を呼ぶだけで、IaC/スクリプトを唯一の真実として再利用する。

無人の **LLM** 運用エージェント（0006 の Option B/C）は引き続き採用しない。本 CD は LLM を含まない
ただのデプロイ自動化であり、0006 と棲み分ける（0006 は supersede しない）。

### Positive Consequences

- 公開 URL を必要時に取得でき、スマホからも UX 検証できる
- 普段 ¥0 + 夜間 teardown の保険でコスト事故を防ぐ
- OIDC で静的シークレットを持たない（リポジトリ Variables に client/tenant/subscription id のみ）
- デプロイ手順がコード化され、device-code セッションと CD で同じ `provision.sh` を共有

### Negative Consequences

- 一度きりの OIDC 連携設定（Entra アプリ + federated credential + ロール付与）が要る（管理権限）
- federated SP に **Contributor（サブスクリプションスコープ）** を与える = 権限は広め（RG 作成/削除のため）。スコープ最小化は将来課題
- `up` は初回 ~20〜40 分（IaC + イメージビルド + コンテナ反映）。所要時間は別途 Runbook に明記
- schedule teardown は「立てっぱなしで翌朝消える」挙動。長時間使いたい時は再 `up` するか schedule を一時無効化する運用が要る

## Pros and Cons of the Options

### Option A: オンデマンド CD + OIDC（採用）

- Good, because 使う時だけ課金 / 夜間 teardown の保険 / 静的シークレット0
- Good, because 既存 IaC・スクリプトを再利用、CI/CD に正式に乗る
- Bad, because 一度きりの OIDC 設定と広めのロールが要る

### Option B: main マージで自動デプロイ（不採用）

- Good, because 完全自動で常に最新が公開される
- Bad, because **常時起動 = 継続課金**。個人開発のコスト要件に反する

### Option C: device-code 手動のまま（不採用）

- Good, because 追加設定ゼロ・[ADR 0006](0006-azure-access-via-device-code.md) のまま
- Bad, because CI/CD に乗らず、手順が属人的。`up`/`down` を毎回手で叩く

## Links

- ワークフロー: `.github/workflows/deploy.yml`
- 立ち上げ: `cicd/scripts/deploy/provision.sh` / 撤収: `cicd/scripts/env/cleanup-env.sh`
- 一度きり設定: `cicd/scripts/cloud-env/setup-oidc.sh` / [Runbook](../runbooks/azure-oidc-cd-setup.md)
- 関連 ADR: [0006](0006-azure-access-via-device-code.md) / [0003](0003-two-phase-bicep.md)
