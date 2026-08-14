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
- **開発の運用・プロセスの決め事** — エージェントの回し方 / レビュー体制 / PM 機構 / セッション分配 / CI の運転ルール。
  これらは**数日で改訂される**ため不変記録の棚に合わない。置き場は [`CLAUDE.md`](../../CLAUDE.md)。
  過去に ADR として書かれた 30 本は [`archive/`](archive/README.md) へ退避済み
- **開発設備 (CI / 資格情報 / エージェントの実行環境) の運用判断** — プロダクトの構成ではなく
  「開発を回すための設備」をどう運転するかは ADR ではない。置き場は [`docs/runbooks/`](../runbooks/README.md)。
  旧 0054 (調査用 read-only 識別) がこれに当たり、2026-08-14 の debrief で退避した

## 書き方

### 1. 番号を決める

**`origin/main` の最大番号 +1** を使う (4 桁)。**退役番号を必ず合算する**:

```bash
git fetch origin main -q
{ git ls-tree -r origin/main --name-only docs/adr/ | grep -oE 'docs/adr/[0-9]{4}-' | grep -oE '[0-9]{4}'
  git show origin/main:docs/adr/archive/retired-numbers.txt | grep -oE '^[0-9]{4}$'
} | sort -n | tail -1
```

> **採番手順の正典はこの節。** [`/adr` skill](../../.claude/skills/adr/SKILL.md) の Step 2 は同じコマンドの写しなので、ここを変えたら**同じ PR で**揃える (食い違うと、採番だけ skill を通したセッションが違う番号を取る)。

⚠️ **`docs/adr/` の実ファイルだけを数えないこと。** [`archive/`](archive/README.md) のファイルは名前から番号を落としてあるので、実ファイルの最大値では**退役番号が見えない** (合算しないと次番号が退役番号になる)。退役番号の一覧は [`archive/retired-numbers.txt`](archive/retired-numbers.txt)。

⚠️ **`ls docs/adr/` のローカル最大値を使わないこと。** 並行セッションが同じ番号を取り、過去 2 回衝突している (0015→0019 / 0026→0027)。**採番は書く瞬間に上のコマンドで取る** (セッション開始時に取っておいた値は、その間に別 PR が ADR を着地させると腐る)。取り違えは CI (`adr-number-guard`) が退役番号の再利用も含めて赤にする。

⚠️ **欠番は埋めないこと。** 0008 / 0011 / 0014 / 0018〜0022 … が飛んでいるのは、30 本
(運用・プロセス系 29 本 + 開発設備 1 本) を [`archive/`](archive/README.md) へ退避したためです。番号は ID であって順序ではなく、振り直すと
Issue / PR 本文の「ADR 0030 を見て」がすべてリンク切れになります (リポジトリ外なので機械置換が届かない)。
**常に main の最大番号 +1 で続けてください。**

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

## 既存 ADR (24 本)

無印 = Accepted。それ以外は末尾に Status を明記する。
**番号順ではなくテーマ順**に並べている (番号は ID であって順序ではない — 欠番の理由は [`archive/`](archive/README.md))。

### ドメイン / プロダクト

- [0007](0007-problem-centric-two-layer-domain-model.md) — 困りごとを Problem 集約とする 2層ドメインモデル (Mention → Problem)
- [0012](0012-grouping-in-ai-agent-with-bff-supplied-candidates.md) — Mention のグルーピングは AI Agent が担い、BFF が既存 Problem 候補を渡す
- [0015](0015-proactive-agentic-workflow.md) — システムの能動化 — ガードレール付きプロアクティブ・エージェントワークフローを解禁する (思想転換)
- [0016](0016-ai-agent-orchestration-on-maf.md) — AI Agent のオーケストレーション基盤を Semantic Kernel から Microsoft Agent Framework へ移行する
- [0034](0034-remove-legacy-session-centric-flow.md) — UC に無い会話中心モデルの残骸 (整理結果 / 行動プラン / 履歴) を撤去する

### フロントエンド

- [0004](0004-mockapi-as-frontend-truth.md) — `mockApi.ts` を Frontend モックの唯一の真実とする
- [0005](0005-mdx-ui-spec-as-truth.md) — UI 仕様は MDX が真実、実装が乖離したら実装を直す
- [0039](0039-dialogue-live-preview-and-character.md) — 対話画面を「キャラと話すと右で困りごとが形になる」構成にする (読み取り専用プレビュー + 独自マスコット)

### BFF / API / 永続化

- [0001](0001-bff-as-trpc-not-rest.md) — BFF を REST ではなく tRPC で書く
- [0024](0024-chat-streaming-via-sse-side-channel.md) — チャット応答の逐次表示は SSE サイドチャネル (`/api/chat/stream`) で通す
- [0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) — 永続化は Cosmos DB 1 本に寄せ、BFF の内側だけに置く (Redis は廃止予定のため不採用)

### 音声

- [0010](0010-voicevox-cpu-gpu-deploy-tier.md) — VOICEVOX を `voicevoxTier`(cpu/gpu) 単一スイッチで切替（既定 cpu）で up を高速・安価に
- [0023](0023-server-stt-azure-speech-f0.md) — 音声入力のサーバー STT に Azure Speech (F0・MI 認証) を採用し、Web Speech をフォールバックに残す

### インフラ / デプロイ

- [0002](0002-container-apps-not-aks.md) — サービス基盤に AKS ではなく Container Apps (scale-to-zero) を選ぶ
- [0003](0003-two-phase-bicep.md) — IaC を bootstrap → config の 2-phase Bicep に分ける
- [0006](0006-azure-access-via-device-code.md) — 開発・運用での Azure アクセスは device-code を主とする
- [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) — dev 環境は「常設・待機最小コスト + main マージ自動デプロイ」にする
- [0017](0017-container-apps-access-via-auth-gate.md) — Container Apps の「第二の扉」は認証の門で閉じる (組み込み認証 + Managed Identity / voicevox は internal ingress)
- [0025](0025-deploy-container-images-by-immutable-sha-tag.md) — コンテナ image のデプロイは :latest ではなく不変 sha タグの解決 + 稼働検証で行う (#107)
- [0046](0046-environment-rebuildable-from-declaration.md) — 環境を「宣言から作り直せるもの」にする — ライフサイクル 3 層分断 / Entra の Graph Bicep 宣言 / 週次プロビジョンテスト (0013 の「常設」を追補) — **D1 (層の分け方) のみ 0056 が supersede 予定 (0056 は Proposed)。D2〜D10 は現行 (D9 本文の「持続層」は管理系 RG と読み替える)**
- [0056](0056-management-and-app-layers-with-backup-based-data-protection.md) — 層は「管理系 / アプリ系」で分け、データは RG 移動ではなくバックアップ + 復元実証で守る (Accept され次第 0046 D1 を supersede / D2〜D10 は現行 — D9 本文の「持続層」は管理系 RG と読み替える) — **Proposed**
- [0055](0055-bff-telemetry-on-workspace-based-app-insights.md) — BFF のサーバ側観測性を workspace-based Application Insights で持つ (保持 30 日 / 日次上限つき / 本文は名前と値の両面で落とす) — **Proposed**
- [0009](0009-on-demand-cd-via-github-actions-oidc.md) — デプロイは GitHub Actions のオンデマンド CD（手動 up/down + 夜間 teardown, OIDC）で行う — **Superseded by 0013**

### リポジトリ運用

- [0049](0049-github-flow-with-conventional-branch-naming.md) — ブランチ戦略は GitHub Flow と明文化し、命名は Conventional Branch 準拠 + Issue 番号必須にする (`claim/*` `data/*` は予約名前空間) — **Proposed** (「運用・プロセスの決め事は ADR ではない」との分類整合は PO 裁定待ち — [#342](https://github.com/yomote/mind-inbox/pull/342))

## 退避された運用系 (ADR ではない)

[`archive/`](archive/README.md) にあるのは計 30 本 — **開発の運用・プロセスの決め事として
書かれていた 29 本**と、**開発設備の運用判断 1 本** (旧 0054)。
**現行ルールではない** — 今どう動かすかは [`CLAUDE.md`](../../CLAUDE.md) を見ること。

その 1 本 (旧 0054 / 調査用 read-only 識別) は運用ではなく**開発設備の運用判断**として退避した。
現に守るべき条件の正典は Runbook [`azure-oidc-cd-setup.md`](../runbooks/azure-oidc-cd-setup.md) にある。
