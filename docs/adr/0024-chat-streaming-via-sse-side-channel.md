# 0024. チャット応答の逐次表示は SSE サイドチャネル (`/api/chat/stream`) で通す

- Status: Proposed
- Date: 2026-08-08
- Deciders: (エージェント起案 — debrief で Accept/Reject)
- Consulted: —
- Informed: —

Technical Story: [Issue #120](https://github.com/yomote/mind-inbox/issues/120) — 対話の即時性が低い。コスト分析 (2026-08-08 コメント) で「ストリーミング表示が体感効果最大の無料の工学」と整理された。

## Context and Problem Statement

現在の `/chat` は LLM の全文生成を待ってから一括で返すため、体感レイテンシ = LLM 生成時間の全部になる。トークン逐次表示にすれば初トークン到達時点 (通常 1〜2 秒) で会話が動き出す。ただし現構成には 2 つの制約がある:

1. **BFF は tRPC 単一エントリポイント** (ADR 0001)。tRPC の mutation は request/response 型で、fetch アダプタ経由ではトークン単位の逐次配信ができない (subscription は WS/SSE リンクの導入と router 全体の設定変更が必要)
2. **BFF は Azure Functions (Consumption)**。応答ストリーミングはホスト側の対応が要る

Frontend → BFF → ai-agent → Azure OpenAI の E2E でトークンをどう運ぶかを決める。

## Decision Drivers

- 体感即時性の最大化 (初トークンまでの時間短縮) — #120 の主目的
- ADR 0001 (tRPC = 型安全な BFF) を壊さない
- 追加課金なし (無料の工学) / 工事の小ささ
- ストリーミング非対応環境でも壊れない (graceful degradation)

## Considered Options

- Option A: **SSE サイドチャネル** — 非 tRPC の `POST /api/chat/stream` を追加 (既存 `/api/tts` と同じ「tRPC に乗らないものは別経路」パターン)。ai-agent も `POST /chat/stream` (SSE) を追加し、BFF はバイトストリームを素通しする
- Option B: **tRPC subscription (SSE リンク)** — tRPC v11 の subscription + `httpSubscriptionLink`
- Option C: **チャンクポーリング** — 非ストリーミングのまま、BFF に部分応答バッファを持たせフロントがポーリング

## Decision Outcome

Chosen option: **"Option A" (SSE サイドチャネル)**。

- **Azure Functions の応答ストリーミングは可能** — Node.js v4 モデルは `app.setup({ enableHttpStream: true })` で HTTP ストリーミングに対応 (host 4.28+ / `@azure/functions` 4.3+、GA)。本リポジトリは `@azure/functions` ^4.5.0 で条件を満たす
- イベント形式は `data: {"type":"delta"|"done"|"error", ...}` の JSON SSE。`done` が従来 `/chat` と同じ `ChatResponse` 全体 (requiresApproval / citations 含む) を運ぶので、**既存の非ストリーミング契約が真実のまま**残る
- フロントは失敗時 (404 / ネットワーク断 / SSE 解釈不能) に**既存 tRPC mutation へ自動フォールバック**する。ストリーミングは強化であって依存にしない
- ホストがストリーミング未対応でも SSE ボディが一括到着するだけで、機能は劣化なしに成立する (全文一括表示 = 従来挙動)

### Positive Consequences

- 初トークン到達で表示が始まり、体感レイテンシが「全文生成待ち」から「初トークン待ち」に縮む
- tRPC router / 生成 OpenAPI (`bff-trpc.yaml`) は不変。契約の真実は `done` イベントが参照する既存 zod / pydantic のまま
- `/api/tts` と同じ「非 JSON・非 request/response は別経路」の既存パターンに乗る

### Negative Consequences

- BFF に tRPC 外のエンドポイントが 1 本増える (tts に続き 2 本目)。増殖しはじめたら ADR 0001 の見直しシグナル
- SSE イベント契約 (delta/done/error) は tRPC の型共有に乗らない — ai-agent の pydantic (`ChatStreamDelta` 等) を真実とし、BFF は素通し、フロントはパーサで防御的に読む
- ストリーミング中の途中失敗はフォールバック再送になり、その 1 往復分は二重生成になる (稀なケースとして許容)

## Pros and Cons of the Options

### Option A: SSE サイドチャネル (採用)

- Good, because 工事が最小で、既存契約 (ChatResponse) を壊さない
- Good, because 劣化モードが自然に定義できる (バッファされても全文一括で成立 / 失敗は tRPC へフォールバック)
- Bad, because tRPC の型安全の外に SSE イベント契約が 1 つ増える

### Option B: tRPC subscription

- Good, because 型安全がストリームにも及ぶ
- Bad, because httpSubscriptionLink / superjson 等リンク構成の全面変更が要り、単発の逐次応答に対して工事が過大
- Bad, because tRPC subscription over SSE と Functions ストリーミングの組み合わせは前例が薄く、劣化モードの制御が難しい

### Option C: チャンクポーリング

- Good, because ホストのストリーミング対応に依存しない
- Bad, because BFF がセッション毎の部分応答バッファ (状態) を持つことになり、Consumption のマルチインスタンスで壊れる
- Bad, because ポーリング間隔分の遅延と無駄なリクエストが常時発生する

## Links

- 発端: [Issue #120](https://github.com/yomote/mind-inbox/issues/120) (コスト分析コメント 2026-08-08)
- 関連 ADR: [0001](0001-bff-as-trpc-not-rest.md) (tRPC 単一エントリポイント) / [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) (scale-to-zero — warmup ping の背景) / [0017](0017-container-apps-access-via-auth-gate.md) (認証の門 — 新経路も同じ門を通る)
- 同時実装 (ADR 級ではない可逆な工学): ホーム表示時の warmup ping / TTS の文単位分割合成 + 逐次プリフェッチ (実装は PR 参照)
