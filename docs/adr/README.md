# Architecture Decision Records (ADR)

> アーキテクチャに関わる判断を不変の記録として残す場所。
> 戦略全体: [`docs/documentation/strategy.md`](../documentation/strategy.md)

## ADR とは

「なぜそういう構成 / 技術選択をしたか」を残すドキュメント。実装が変わってもこの判断記録は残し続ける。

## いつ書くか

次のような判断をする**前に**書く:

- フレームワーク / ライブラリ / クラウドサービスの採用・廃止
- 異なる選択肢があり得るアーキテクチャ判断 (例: REST vs tRPC、AKS vs Container Apps)
- 後から覆すと影響範囲が広い設計上の前提 (例: mockApi.ts を真実にする)
- セキュリティ / コンプライアンスに関わる構造的な決定

書かなくて良いもの:

- 実装詳細 (関数名、ファイル分割の仕方)
- 一時的な対処 / バグ修正
- 運用手順 (Runbook の領域)

## 書き方

### 1. 番号を決める

**`origin/main` の最大番号 +1** を使う (4 桁):

```bash
git fetch origin main -q
git ls-tree -r origin/main --name-only docs/adr/ | grep -oE '[0-9]{4}' | sort -n | tail -1
```

⚠️ **`ls docs/adr/` のローカル最大値を使わないこと。** 並行セッションが同じ番号を取り、過去 2 回衝突している (0015→0019 / 0026→0027)。セッション開始時のフックが「次に使える番号」を自動提示し、CI (`adr-number-guard`) が衝突を赤にする ([ADR 0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) D3)。

### 2. ファイルを作る

`docs/adr/NNNN-{kebab-case-slug}.md` の形式。

```bash
cp docs/adr/template.md docs/adr/0006-my-decision.md
```

### 3. 書く

[`template.md`](./template.md) は MADR 3.0 形式。最低限埋めるセクション:

- Status (`Proposed` で開始)
- Context and Problem Statement
- Considered Options
- Decision Outcome (chosen option + 理由)
- Consequences (positive / negative)

### 4. レビュー

ADR-only の PR を出す。**実装より先に承認**を得る。承認時に Status を `Accepted` に変更。

## Status 遷移

```
Proposed  ─→  Accepted  ─→  Deprecated  (使われなくなった)
                       └→  Superseded by NNNN  (別 ADR が代替)
                       └→  Rejected            (採用しなかったが記録は残す)
```

過去 ADR は**書き換えない**。状態が変わった時のみ Status 行を更新する (もしくは新規 ADR で supersede する)。

## CLAUDE.md からの参照

エージェントが過去判断を覆さないよう、CLAUDE.md からこのディレクトリにリンクする (#13 で実施)。

## 既存 ADR

無印 = Accepted。それ以外は末尾に Status を明記する (一覧から Superseded / Proposed が読めるように)。

- [0001](0001-bff-as-trpc-not-rest.md) — BFF を REST ではなく tRPC で書く
- [0002](0002-container-apps-not-aks.md) — サービス基盤に AKS ではなく Container Apps (scale-to-zero) を選ぶ
- [0003](0003-two-phase-bicep.md) — IaC を bootstrap → config の 2-phase Bicep に分ける
- [0004](0004-mockapi-as-frontend-truth.md) — `mockApi.ts` を Frontend モックの唯一の真実とする
- [0005](0005-mdx-ui-spec-as-truth.md) — UI 仕様は MDX が真実、実装が乖離したら実装を直す
- [0006](0006-azure-access-via-device-code.md) — 開発・運用での Azure アクセスは device-code を主とする
- [0007](0007-problem-centric-two-layer-domain-model.md) — 困りごとを Problem 集約とする 2層ドメインモデル (Mention → Problem)
- [0008](0008-pr-review-via-cloud-routine.md) — PR レビューを Claude Code on the web の Routine で行う — **Superseded by 0035**
- [0009](0009-on-demand-cd-via-github-actions-oidc.md) — デプロイは GitHub Actions のオンデマンド CD（手動 up/down + 夜間 teardown, OIDC）で行う — **Superseded by 0013**
- [0010](0010-voicevox-cpu-gpu-deploy-tier.md) — VOICEVOX を `voicevoxTier`(cpu/gpu) 単一スイッチで切替（既定 cpu）で up を高速・安価に
- [0011](0011-github-projects-as-execution-dashboard.md) — GitHub Projects は実行状態のダッシュボードに徹し、設計の真実は docs に置く
- [0012](0012-grouping-in-ai-agent-with-bff-supplied-candidates.md) — Mention のグルーピングは AI Agent が担い、BFF が既存 Problem 候補を渡す
- [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) — dev 環境は「常設・待機最小コスト + main マージ自動デプロイ」にする（0009 のオンデマンド teardown を置き換える）
- [0014](0014-design-comprehension-gate-and-debrief.md) — 設計理解ゲートとゼミ型デブリーフで、user の意思決定・学習をループに組み込む
- [0015](0015-proactive-agentic-workflow.md) — システムの能動化 — ガードレール付きプロアクティブ・エージェントワークフローを解禁する (思想転換)
- [0016](0016-ai-agent-orchestration-on-maf.md) — AI Agent のオーケストレーション基盤を Semantic Kernel から Microsoft Agent Framework へ移行する
- [0017](0017-container-apps-access-via-auth-gate.md) — Container Apps の「第二の扉」は認証の門で閉じる (組み込み認証 + Managed Identity / voicevox は internal ingress)
- [0018](0018-runtime-verification-in-the-loop.md) — 動作検証をループに組み込む（実態の読み取り・PR への証跡・ローカルブラウザ検証）
- [0019](0019-independent-judge-agents-security-qa-release.md) — セキュリティ / QA / リリース判定を実装コンテキストから分離した独立 judge エージェントにする
- [0020](0020-hitl-choice-format-and-needs-human-queue.md) — 人間の確認は選択肢形式で出し、人間宿題は needs-human キューに一元化する
- [0021](0021-parent-session-as-pm-orchestrator.md) — 親セッションを PM ハブにして、並行作業は子セッションへ分配する (hub-and-spoke)
- [0022](0022-autonomous-ux-improvement-loop.md) — UX 品質は自律改善ループで維持する (観測・評価・改善を自動化、人間は基準定義と例外裁定) — 一部 Superseded by 0035 (起動経路のみ)
- [0023](0023-server-stt-azure-speech-f0.md) — 音声入力のサーバー STT に Azure Speech (F0・MI 認証) を採用し、Web Speech をフォールバックに残す
- [0024](0024-chat-streaming-via-sse-side-channel.md) — チャット応答の逐次表示は SSE サイドチャネル (`/api/chat/stream`) で通す
- [0025](0025-deploy-container-images-by-immutable-sha-tag.md) — コンテナ image のデプロイは :latest ではなく不変 sha タグの解決 + 稼働検証で行う (#107)
- [0026](0026-cd-watchdog-routine.md) — CD の赤は毎時の watchdog Routine が検知し、診断と fix PR まで無人で進める — **Superseded by 0035**
- [0027](0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) — UX 自律改善ループ M2: 採点の無人化を先行させ、A/B は実環境の外で回し、改変対象はパスで縛る
- [0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) — 分配は「起票パケットを Issue 本文に残す」形にし、並行の衝突は SessionStart の事前提示と CI で防ぐ — 一部 Superseded by 0035 (パケットの置き場所のみ Issue → PR)
- [0029](0029-probe-record-transport-via-issue-comment.md) — UX プローブ記録は artifact ではなく Issue コメントで採点セッションへ運ぶ
- [0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) — 永続化は Cosmos DB 1 本に寄せ、BFF の内側だけに置く (Redis は廃止予定のため不採用)
- [0031](0031-agent-reaches-outside-via-github-actions.md) — サンドボックスの外にある事実は GitHub Actions 経由で取る (その場しのぎの回避策を作らない)
- [0032](0032-use-case-acceptance-tests-against-real-wiring.md) — ユースケース受け入れテストを「mock を通らない実配線」で持つ (L3-real)
- [0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) — 子セッションを起動できない環境では、親が subagent で実装を回す (ADR 0021 条項の改訂)
- [0034](0034-remove-legacy-session-centric-flow.md) — UC に無い会話中心モデルの残骸 (整理結果 / 行動プラン / 履歴) を撤去する
- [0035](0035-role-split-across-agents-and-actions.md) — 開発ループの役割を分け、それぞれを「生死が見える場所」に置く (実装 Claude / レビュー Codex / 監視 Actions) — 一部 Superseded by 0040 (D1 の「Routine ゼロ」のみ)
- [0036](0036-merge-gate-as-required-check-and-pm-cadence.md) — マージの門を required check (review-gate) で機構化し、PM の運転リズムを定める
- [0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) — 定期評価を「機械計測 = Actions」と「LLM 採点 = PM tick」に分ける (ADR 0035 D1 の残作業)
- [0038](0038-security-checks-as-mechanized-triggers.md) — セキュリティ検査のトリガーを人の判断から機構へ移す (週次 sweep / PR 自動指名 / リリース judge)
- [0039](0039-dialogue-live-preview-and-character.md) — 対話画面を「キャラと話すと右で困りごとが形になる」構成にする (読み取り専用プレビュー + 独自マスコット)
- [0040](0040-project-continuity-three-layers.md) — プロジェクト継続性を 3 層 (機構化された完遂 / 当番 PM / 窓口 PM) で保証する
- [0041](0041-ux-observations-on-git-data-branch.md) — UX 観測データの蓄積先を Issue コメントから git データブランチへ移す — **Proposed**
- [0042](0042-pm-accept-carryover-and-merge-queue.md) — pm-accept は「実装差分が不変の main 追随」に引き継ぎ、直列化は Merge Queue に任せる (0036 の運用改訂) — **Proposed**
