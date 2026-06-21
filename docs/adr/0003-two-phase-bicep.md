# 0003. IaC を bootstrap → config の 2-phase Bicep に分ける

- Status: Accepted
- Date: 2026-06-21
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: 既存実装の遡及的記録 (#11) — 判断自体はリポジトリ初期から下されている

## Context and Problem Statement

Azure インフラ (SWA / Function App / SQL / Key Vault / Log Analytics / Container App 環境 / ACR) を
Bicep で IaC 管理する。このうち Entra ID 認証の有効化や secret 投入は、基盤リソースが
存在し、かつ前提 (アプリ登録・UAMI 等) が揃ってからでないと適用できない。
全部を 1 つの Bicep デプロイにまとめるか、フェーズを分けるかを決める必要がある。

## Decision Drivers

- 初回構築のべき等性・再実行のしやすさ (基盤作成を何度流しても壊れない)
- 認証/secret 設定の前提依存 (アプリ登録や UAMI が先に要る)
- 失敗時の切り分けやすさ (どのフェーズで落ちたか分かる)
- 環境 (dev/stg/prod) ごとの差分適用のしやすさ

## Considered Options

- Option A: **2-phase** — `main-bootstrap.bicep` (基盤) → `main-config.bicep` (認証/secret)
- Option B: 単一 Bicep にすべてを含める
- Option C: フェーズをさらに細かく分割 (network / data / compute / auth …)

## Decision Outcome

Chosen option: **"Option A" (2-phase)**。
基盤リソース作成 (bootstrap) と、前提が揃ってから載せる認証/secret (config) は
**ライフサイクルと前提条件が異なる**。分けることで bootstrap を何度でも安全に再実行でき、
認証周りの失敗を config フェーズに局所化できる。Entra アプリ登録や UAMI といった
「環境外で先に用意する前提」を config 側に寄せられるのも利点。
3 phase 以上への細分化 (Option C) は現リソース規模では管理オーバーヘッドが見合わない。

## Positive Consequences

- bootstrap を再実行しても認証/secret 適用に巻き込まれず、基盤更新が安全
- 認証 (Entra) の有効化を環境準備が整ったタイミングで後追いできる
- 失敗箇所が bootstrap / config のどちらかに切り分けられる
- `cleanup-env.sh` 等の運用スクリプトと phase 境界が揃う

## Negative Consequences

- デプロイ手順が 2 ステップになり、初回オンボーディングで順序を知る必要がある
- bootstrap と config の間で渡すパラメータ (リソース名等) の整合を保つ必要がある
- phase 間の依存を Runbook / README で明示し続ける運用コストがある

## Pros and Cons of the Options

### Option A: 2-phase (bootstrap → config) (採用)

基盤を bootstrap、認証/secret を config に分離。

- Good, because 前提依存のある認証/secret を後追いで安全に適用できる
- Good, because bootstrap の再実行が安全・べき等
- Bad, because 手順が 2 ステップに増える

### Option B: 単一 Bicep

すべてを 1 デプロイに含める。

- Good, because デプロイ手順が 1 つで単純
- Bad, because 認証/secret の前提未充足で全体が失敗しやすい
- Bad, because 基盤だけ更新したいケースで認証適用に巻き込まれる

### Option C: 多段 (network/data/compute/auth)

phase をさらに細分化する。

- Good, because 大規模環境では関心の分離が効く
- Bad, because 現リソース規模に対して phase 管理が過剰

## Links

- 実装: `cicd/iac/main-bootstrap.bicep` / `cicd/iac/main-config.bicep`
- 手順: [cicd/iac/README.md](../../cicd/iac/README.md)
- 関連 ADR: [0002](0002-container-apps-not-aks.md) — Container App 環境は bootstrap フェーズで作成
