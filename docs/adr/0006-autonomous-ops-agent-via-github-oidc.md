# 0006. 運用保守エージェントを GitHub Actions + OIDC で動かす (診断・PR 提案まで)

- Status: Proposed
- Date: 2026-06-18
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: —

## Context and Problem Statement

開発・運用保守の中で、実 Azure リソースの状況確認や異常の一次診断をエージェントに任せたい。
一方で、認証に**静的なクライアントシークレットを保存し続ける**のは避けたい (同じ値が漏れたら使い回される)。
無人で動かす保守エージェントに、どの認証方式・どの権限・どこまでの自律性を与えるかを決める必要がある。

## Decision Drivers

- 静的シークレットを保存しない (短命トークンで都度認証)
- 自律エージェントの blast radius を最小化する (暴走・誤操作で本番を壊さない)
- 認証情報の権限スコープを最小化する (1 リソースグループ / 読み取り限定)
- 既存の CD 方針 (GitHub Actions + OIDC フェデレーション) と仕組みを共有する

## Considered Options

- Option A: サンドボックスで対話型 (device code) のみ — 無人運用ができない
- Option B: GitHub Actions + OIDC フェデレーション、**Reader 権限で診断＋PR 提案まで (B1+B2)**
- Option C: B に加えて、エージェントが直接 `az` で書き込み操作する (B3、再起動/スケール等)

## Decision Outcome

Chosen option: **"Option B"**。
無人運用が必要な保守タスクは GitHub Actions で実行し、Azure へは **OIDC ワークロード ID フェデレーション**で認証する (保存シークレットなし・実行ごとに短命トークン)。
権限は **`rg-dev-mind-inbox` スコープの Reader 限定**とし、エージェントは「状況を読む → Issue で報告 (B1) / 直す案を PR で提案 (B2)」までに留める。
実リソースへの変更は必ず **PR の merge → 既存 CD** を通すため、エージェントは Azure に対して書き込み権限を持たない (B3 は不採用)。

### Positive Consequences

- 保存する Azure シークレットがゼロ。漏洩面が消える
- 認証情報が漏れても Reader / 1 RG のみで、被害は閲覧に限定
- すべての変更が PR を通るため、自律エージェントでも本番を直接壊せない
- CD 用フェデレーションと同じ仕組みで、学習コスト・運用面を共有できる

### Negative Consequences

- 緊急の自動修復 (例: 即時再起動) はできない — 必ず人の merge を挟む
- エージェント実行のために GitHub 側に 1 つだけ静的シークレット (`ANTHROPIC_API_KEY`) が残る (将来 Bedrock/Vertex の OIDC で解消可能、別 ADR 候補)
- Reader では取得できないメトリクス/ログ系は、後日スコープを足す判断が要る

## Pros and Cons of the Options

### Option A: サンドボックスで対話型 (device code) のみ

開発者が居るセッション中だけ `az login --use-device-code` で認証する。

- Good, because 保存シークレットが一切ない
- Good, because 開発中の対話的な状況確認には十分
- Bad, because 無人 / 定期 / アラート反応の保守ができない

### Option B: GitHub Actions + OIDC、Reader で診断＋PR 提案 (採用)

無人ジョブを Actions 側に寄せ、OIDC で短命トークン認証。Reader 限定・変更は PR 経由。

- Good, because 静的 Azure シークレットなし・最小権限・PR ゲートで安全
- Good, because CD のフェデレーション基盤を再利用できる
- Bad, because 即時自動修復はできない (人の merge が必須)

### Option C: B + エージェントが直接書き込み (B3)

エージェントが `az` で再起動/スケール等を自分で実行する。

- Good, because 即時の自動修復が可能
- Bad, because LLM エージェントに無人の書き込み権限を渡す = blast radius が大きい
- Bad, because 誤操作・暴走時に本番を直接破壊しうる

## Links

- 関連 Runbook: [ops-agent-azure-oidc-setup.md](../runbooks/ops-agent-azure-oidc-setup.md)
- ワークフロー: `.github/workflows/ops-agent.yml`
- 関連 ADR: [0003](0003-two-phase-bicep-iac.md) (IaC), [0002](0002-container-apps.md) (Container Apps)
