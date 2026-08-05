# 0013. dev 環境は「常設・待機最小コスト + main マージ自動デプロイ」にする（0009 のオンデマンド teardown を置き換える）

- Status: Proposed
- Date: 2026-08-05
- Deciders: omoteforlab
- Consulted: —
- Informed: —

関連: [ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md)（これを supersede 予定）/ [ADR 0002](0002-container-apps-not-aks.md)（scale-to-zero）/ [ADR 0003](0003-two-phase-bicep.md)（2-phase Bicep）/ [ADR 0006](0006-azure-access-via-device-code.md)（静的シークレット0 のドライバー継承）/ [ADR 0010](0010-voicevox-cpu-gpu-deploy-tier.md)（up 高速化・低コスト化）

## Context and Problem Statement

[ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md) は「使う時だけ `up` し、夜間 schedule で RG ごと teardown して ¥0 に戻す」オンデマンド CD を選んだ。コスト最適だが、**「できたものを本物の UX として、ぱっと触って確認したい」という要件を満たせていない**。実測の痛み:

- `up` は毎回ゼロからのフル構築で **初回 ~20〜40 分**。「見たい」と言ってから待たされる。
- 毎晩 teardown するため、この構築コストを**毎回**払う。翌朝には環境が消えている。
- モック UI（[ADR 0004](0004-mockapi-as-frontend-truth.md)/[0005](0005-mdx-ui-spec-as-truth.md)）だけでは実 AI 応答を含む体験にならず、UX 確認の代替にならない。

「常時起動＝継続課金」を避けたいのは変わらないが、**待機コストの実態を精査すると、常設しても継続課金が発生する主因は SQL DB のみ**だった（下記 Decision Drivers 参照）。ここを潰せば「常設 × 低コスト × 速い」が両立できる。オンデマンド teardown という 0009 の前提を、実装より前に判断として覆す。

## Decision Drivers

- **本物の UX をぱっと確認できる** — 実 AI 応答まで含む環境に、待たずに（スマホ含め）到達できる
- **待機コストは最小** — 個人開発。使っていない時に金を垂れ流さない
- **静的シークレット0** — [ADR 0006](0006-azure-access-via-device-code.md) のドライバーを継承（OIDC 維持）
- **既存 IaC / スクリプトを再利用** — 2-phase Bicep・`provision.sh`・`deploy-*.sh` を唯一の真実として使い回す
- **scale-to-zero を活かす** — [ADR 0002](0002-container-apps-not-aks.md) で選んだ Container Apps は待機 ¥0

### 待機コストの実測内訳（常設した場合）

| リソース | 現行 SKU | 待機時コスト | 対策 |
| --- | --- | --- | --- |
| **SQL DB（+ Key Vault / Private Endpoint / DNS / VNet）** | **S0（固定 DTU・フラグなしで常時作成）** | **常時課金（月 ¥2,000 前後）** | **アプリ未使用**（実装は in-memory dict、永続化ロードマップは Redis）→ `enableSql=false` で dev から撤去 → ¥0・かつ `up` の最遅リソースが消える |
| Container Apps ×3 | Consumption | アプリは scale-to-zero → ~¥0 | 3→1 環境に統合（プロビジョニング短縮） |
| Azure Functions | Y1（従量） | 呼ばれた分のみ → ~¥0 | 変更なし |
| Azure OpenAI | S0 | トークン従量 → ~¥0 | 変更なし |
| Static Web Apps | Standard | 小額固定（~¥1,300/月） | linked backend に必要なため維持 |
| ACR | Basic | 小額固定（~¥750/月） | 維持（事前ビルド image の置き場） |
| Log Analytics | 従量 | 取込量依存・小額 | 保持期間を短めに |

→ **未使用の SQL 一式を dev から外せば**、待機は「小額固定（SWA Standard + ACR Basic ≒ 月 ¥2,000 前後）」だけに収まり、かつ `up` から最遅リソース（Private Endpoint / DNS / VNet）が消えて短縮される。SQL はアプリが一度も参照しておらず（BFF / AI Agent に SQL 参照ゼロ、実装は in-memory dict で差し替え先は Redis）、撤去は挙動に影響しない。当初は「サーバーレス化でコスト最適化」を想定したが、精査の結果「そもそも未使用 → 撤去」がコスト・速度とも上回るため方針を変更した。

## Considered Options

- Option A: **常設・待機最小コスト + main マージ自動デプロイ**（未使用 SQL 一式の撤去 + Container Apps 環境統合 + image 事前ビルド + scale-to-zero 維持）
- Option B: **[ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md) 維持**（オンデマンド up/down + 夜間 teardown）
- Option C: **オンデマンド維持のまま `up` を高速化**（image 事前ビルド + 環境統合はするが、teardown は残す）

## Decision Outcome

Chosen option: **Option A**。

「本物をぱっと触りたい」に唯一まっすぐ応えるのが Option A。待機コストの主因だった SQL が**アプリから一度も参照されていない未使用リソース**と判明し、dev から撤去できるため、0009 が Option B（自動デプロイ = 常時起動）を却下した根拠（継続課金）が成立しなくなった。scale-to-zero（0002）と OIDC（0006/0009）はそのまま活き、既存 IaC/スクリプトを唯一の真実として再利用する。

**本 ADR が Accepted になった時点で、[ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md) の Status を `Superseded by 0013` に更新する**（OIDC 認証・`provision.sh`/`cleanup-env.sh` の機構は 0013 でも引き続き使うため、破棄ではなく「オンデマンド teardown 方針」の置き換え）。手動 `down`（一時撤収）とスクリプト群自体は残す。

### Positive Consequences

- 常に最新が 1 つの固定 URL で公開され、開けば数秒〜のコールドスタートで**すぐ触れる**（スマホ含む）
- main マージ → 自動デプロイで、確認導線が「待つ」から「常にそこにある」へ
- image 事前ビルド + 環境統合で、デプロイ所要が分オーダーに（毎回の `az acr build` ×2 とフル IaC を除去）
- 未使用の SQL 一式（+ Private Endpoint / DNS / VNet）が消え、待機の高額費が ¥0 になるうえ `up` も最速化される
- 既存 OIDC / スクリプト / 2-phase Bicep を流用（二重管理しない）

### Negative Consequences

- **将来 永続化を入れる段階で別途プロビジョニングが必要**：dev から SQL を外すため、セッション/承認を本当に永続化する時に、コードの意図どおり Redis（または SQL）を改めて立てる判断が要る（現状は in-memory で再起動時に消えても可）
- **待機でも小額固定が残る**：SWA Standard + ACR Basic ≒ 月 ¥2,000 前後。「完全 ¥0」ではない（0009 の強みは失う）
- **「消し忘れ」概念が消える代わりに放置監視が要る**：常設ゆえコスト暴走の芯（SQL・OpenAI）を予算アラートで見張る運用に切り替える
- **Container Apps 環境の 3→1 統合で障害ドメインを共有**：1 環境の不調が ai-agent/voicevox 双方に及びうる（dev では許容）
- **認証の判断が要る**：常設・公開 URL になるため、匿名公開か Entra で自分のアカウントに限定するかを決める（Issue で扱う）

## Pros and Cons of the Options

### Option A: 常設・待機最小コスト + 自動デプロイ（採用）

- Good, because 本物をぱっと・常に触れる（要件に直答）
- Good, because 未使用 SQL 一式の撤去で待機の高額費が ¥0 になり、`up` の最遅リソース（Private Endpoint / DNS / VNet）も消える
- Good, because デプロイが分オーダーになり、確認の待ち時間が実質ゼロ
- Bad, because 待機でも小額固定が残り、完全 ¥0 ではない
- Bad, because 放置監視（予算アラート）とコールドスタートを受け入れる必要

### Option B: ADR 0009 維持（オンデマンド teardown）

- Good, because 待機完全 ¥0 / 消し忘れの保険
- Bad, because 「見たい」→ 20〜40 分待ち、翌朝消える。**今回の要件を満たせない**

### Option C: オンデマンド維持のまま up 高速化

- Good, because 待機 ¥0 を守りつつ up は短縮できる
- Bad, because teardown が残る限り「初回は待つ・翌朝消える」構造は消えない（要件に半分しか応えない）

## Links

- 関連 ADR: [0009](0009-on-demand-cd-via-github-actions-oidc.md)（supersede 予定）/ [0002](0002-container-apps-not-aks.md) / [0003](0003-two-phase-bicep.md) / [0006](0006-azure-access-via-device-code.md) / [0010](0010-voicevox-cpu-gpu-deploy-tier.md)
- 実装 Issue: 分解して GitHub Issues で追跡（本 ADR 承認後に着手）
