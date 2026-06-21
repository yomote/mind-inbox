# 0006. 開発・運用での Azure アクセスは device-code を主とする (無人 OIDC エージェントは見送り)

- Status: Accepted
- Date: 2026-06-21
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: PR #29 (無人 OIDC エージェント案、クローズ済 — 設計の記録として履歴に残る)

## Context and Problem Statement

開発・運用保守の中で、実 Azure リソースの状況確認や操作を Claude の開発セッションから行いたい。
一方で、認証に**静的なクライアントシークレットを保存し続ける**のは避けたい (同じ値が漏れたら使い回される)。
どの認証方式でセッションから Azure に触れるようにするかを決める必要がある。

## Decision Drivers

- 静的シークレットを保存しない (短命トークンで都度認証)
- 依存を増やさない (2 つ目のクラウドや CI 基盤を新たに常設しない)
- 開発セッションからの即時性 (人が居るその場で確認・操作できる)
- blast radius を最小化する

## Considered Options

- Option A: 開発セッションから **device-code ログイン** (対話型・自分の権限)
- Option B: GitHub Actions + OIDC の**無人運用保守エージェント** (Reader 限定、診断 + PR 提案)
- Option C: B に加えてエージェントが直接 Azure へ書き込み (再起動/スケール等)

## Decision Outcome

Chosen option: **"Option A" (device-code)**。
実際の要件は「**開発セッションから対話的に Azure を見る/操作する**」であり、セッション内では
Claude 自身が頭脳として動くため、CI に別エージェントを立てる必要がない。
device-code は **静的シークレットなし**・**自分の Azure 権限を継承** (専用 SP やロール設計が不要)・
トークンは**セッション限り** (コンテナ破棄で消える) で、要件に最小構成で合致する。

無人 OIDC エージェント (Option B) は、認証の安全性 (OIDC) は良いものの、別 Claude を CI で動かすため
`ANTHROPIC_API_KEY` の常設や GitHub Actions 基盤が要り、現要件に対して過剰。よって**今回は見送る**。
将来「無人・定期チェック」や「アラート反応の一次診断」が必要になったら再検討する
(その設計・IaC・ワークフロー雛形は PR #29 の git 履歴に残っており復活可能)。
Option C は無人書き込みの blast radius が大きく、いずれにせよ不採用。

### Positive Consequences

- 保存する静的シークレットがゼロ (Azure 側も、エージェント実行キーも不要)
- 2 つ目のクラウド (Bedrock/Vertex の AWS/GCP) も CI 基盤も増えない
- 人が居るセッションでその場で確認・操作でき、即時性が高い
- 専用 SP / ロール設計が不要で運用がシンプル

### Negative Consequences

- 無人実行はできない (人が居るセッション中のみ)。定期チェック等は別途必要になったら Option B を再検討
- 事前設定が要る: 環境の egress 許可リストに `management.azure.com` 等を追加し、setup script で `az` を導入する
- `az` は自分の権限でログインするため、強い権限のアカウントだとセッションからの操作範囲も広い (必要なら絞った別アカウントでログインする)

## Pros and Cons of the Options

### Option A: device-code ログイン (採用)

開発セッション内で `az login --use-device-code` し、ブラウザで承認する。

- Good, because 静的シークレットが一切ない / トークンはセッション限り
- Good, because 別クラウド・別 CI を増やさず最小構成
- Good, because 人が居る前提の対話的な確認・操作にそのまま合う
- Bad, because 無人・定期実行はできない

### Option B: GitHub Actions + OIDC の無人エージェント (見送り)

無人ジョブを Actions に寄せ OIDC 認証。Reader 限定・変更は PR 経由。

- Good, because 無人運用ができ、Azure 側は保存シークレットなし
- Bad, because 別 Claude を CI で動かすため `ANTHROPIC_API_KEY` 常設 + Actions 基盤が要り、現要件には過剰

### Option C: B + 直接書き込み (不採用)

エージェントが `az` で再起動/スケール等を自分で実行する。

- Good, because 即時の自動修復が可能
- Bad, because LLM エージェントに無人の書き込み権限を渡す = blast radius が大きい

## Links

- 関連 Runbook: [claude-web-azure-access.md](../runbooks/claude-web-azure-access.md)
- スクリプト正本: `cicd/scripts/cloud-env/setup.sh`
- 見送った無人案の設計記録: PR #29 (closed)
- docs: https://code.claude.com/docs/en/claude-code-on-the-web#network-access
