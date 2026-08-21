# 0002. サービス基盤に AKS ではなく Container Apps (scale-to-zero) を選ぶ

- Status: Accepted
- Date: 2026-06-21
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: 既存実装の遡及的記録 (#11) — 判断自体はリポジトリ初期から下されている

## Context and Problem Statement

AI Agent (FastAPI + Semantic Kernel) と VOICEVOX Wrapper / Engine はコンテナとして動かす。
これらをどのコンテナ基盤 (AKS / Container Apps / App Service / VM) で動かすかを決める必要がある。
PoC〜小規模運用フェーズであり、トラフィックは断続的でアイドル時間が長い。
運用人員は限られ、クラスタ運用に割ける工数は小さい。

## Decision Drivers

- アイドル時のコスト (使われていない時間に課金され続けたくない)
- 運用負荷 (クラスタ/ノードの面倒を見たくない)
- スケール特性 (断続的なリクエストに対しゼロ〜数インスタンスで足りる)
- Azure 内の他リソース (Functions / ACR / VNet) との統合のしやすさ

## Considered Options

- Option A: **Azure Container Apps** (サーバーレスコンテナ、scale-to-zero)
- Option B: Azure Kubernetes Service (AKS)
- Option C: App Service for Containers

## Decision Outcome

Chosen option: **"Option A" (Container Apps)**。
ワークロードが断続的でアイドルが長いため、**scale-to-zero でアイドル課金をなくせる**ことが
最大の価値。Container Apps は KEDA ベースの自動スケールとリビジョン管理を標準で持ち、
ノードやコントロールプレーンの面倒を見る必要がない。AKS の柔軟性 (任意の k8s リソース、
細かいネットワーク制御) は現フェーズの要件に対して過剰で、クラスタ運用コストに見合わない。
App Service は scale-to-zero を持たず、コンテナ複数構成の取り回しも Container Apps ほど素直でない。

## Positive Consequences

- アイドル時にインスタンス 0 まで縮退し、コストが使用量に比例する
- ノード/クラスタ運用が不要 (パッチ・アップグレード・容量管理を Azure が担う)
- リビジョンによる無停止デプロイ・ロールバックが標準機能
- ACR / Functions / Container App 環境を同一リソースグループで統合しやすい

## Negative Consequences

- k8s の表現力 (任意の Operator / CRD / 細かい scheduling) は使えない
- scale-to-zero からの最初のリクエストでコールドスタートが発生する
- 大規模・高度なネットワーク制御が必要になった場合は AKS への移行を再検討する必要がある

## Pros and Cons of the Options

### Option A: Azure Container Apps (採用)

サーバーレスコンテナ。KEDA 自動スケール + scale-to-zero + リビジョン管理。

- Good, because アイドル課金をなくせる (scale-to-zero)
- Good, because クラスタ運用が不要で運用負荷が低い
- Good, because リビジョンで無停止デプロイ/ロールバックできる
- Bad, because k8s の表現力やコールドスタートの制約がある

### Option B: AKS

フルマネージド k8s。最大の柔軟性。

- Good, because 任意の k8s リソースと細かい制御が可能
- Bad, because クラスタ/ノード運用コストが現フェーズに対して過剰
- Bad, because scale-to-zero を素直に得にくい (アイドルでもノード課金)

### Option C: App Service for Containers

PaaS でのコンテナ実行。

- Good, because 既存 App Service の運用知見をそのまま使える
- Bad, because scale-to-zero がなくアイドル課金が残る
- Bad, because 複数コンテナサービス構成の取り回しが Container Apps ほど素直でない

## Links

- 実装: `cicd/scripts/deploy/deploy-ai-agent.sh` / `deploy-voicevox-wrapper.sh`
- 関連 ADR: [0003](0003-two-phase-bicep.md) — Container App 環境を含む IaC の 2-phase 構成
- 戦略: [`cicd/CLAUDE.md`](../../cicd/CLAUDE.md)「コストと公開面で覆さない前提」 (#387 で参照先を追随)
