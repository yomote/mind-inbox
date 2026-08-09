# 0031. サンドボックスの外にある事実は GitHub Actions 経由で取る (その場しのぎの回避策を作らない)

- Status: Proposed
- Date: 2026-08-09
- Deciders: omoteforlab (方向は 2026-08-09 の対話で選択済み。Accept は debrief で)
- Consulted: —
- Informed: —

Technical Story: 2026-08-09、ADR 0030 (永続化) の設計中に Azure の料金・仕様の一次情報が取得できず、月額の判断材料がすべて二次情報の概算になった。

## Context and Problem Statement

エージェントのセッションはサンドボックスの中で動いており、外の世界に届かない経路が 3 種類ある。これまでその場しのぎで個別に迂回してきたが、**同じ壁に 4 回ぶつかって 4 回別々の回避策を作っている**ため、パターンとして固定する。

| 壁 | 実測 | これまでの回避 |
| --- | --- | --- |
| **egress ポリシー** — 環境の Network access が `Trusted` で、許可リスト外は CONNECT が 403 | `learn.microsoft.com` / `azure.microsoft.com` / `prices.azure.com` がプロキシログに `connect_rejected` として記録。`*.blob.core.windows.net` も同様 | bicep CLI が取れない → `iac-validate.yml` を作って runner でビルド (#—) / artifact が落とせない → Issue コメント運搬へ ([ADR 0029](0029-probe-record-transport-via-issue-comment.md)) |
| **Azure のトークンが無い** — 対話ログインは device code が要り、無人セッションでは不可 ([ADR 0006](0006-azure-access-via-device-code.md))。ただし `management.azure.com` 自体は到達可能 (400 応答を確認) | 「Portal で確認してください」という人手宿題が発生し続けている | `inspect-env.sh` を書いたが、実行は人間 ([ADR 0018](0018-runtime-verification-in-the-loop.md) ①) |
| **MCP の承認ゲート** — `claude-code-remote` は読み取り専用の `list_environments` すら `-32003 requires approval` | 子セッション起動・Routine 登録が不可 | 起票パケット + user の 1 クリック ([ADR 0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) D1) |

一方で GitHub Actions の runner は **egress 制限を受けず**、**OIDC で Azure に入れ** (`deploy.yml` / `golden-path-monitor.yml` で実績あり)、しかも**エージェントは GitHub MCP でワークフローを起動でき (`actions_run_trigger`)、ログを読める (`get_job_logs`)**。つまり「エージェントの手足」として既に使える状態にある。

実際、リポジトリはこのパターンを**無自覚に 2 回発明している** — `iac-validate.yml` (冒頭コメントに「ローカルの agent 環境では egress ポリシーで塞がれるため runner でビルドする」と明記) と、PR を開くためだけの使い捨て `tmp-open-pr-107.yml` (git 履歴に無く、ブランチ上に置かれたまま Actions 一覧に残存)。

## Decision Drivers

- **ループを止めない** — 「人間が Portal を見て教える」が挟まるたびにループが同期待ちになる
- **追加課金を発生させない** — サブスク枠で完結させる ([ADR 0008](0008-pr-review-via-cloud-routine.md) の Driver をそのまま継承)
- **秘密を増やさない** — サンドボックスに長期クレデンシャルを置かない ([ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md) の no stored secret)
- **その場しのぎを増やさない** — 使い捨てワークフローが残り続けると、何が正規の経路か分からなくなる
- **権限を広げすぎない** — 迂回路そのものが新しい扉にならないこと

## Considered Options

- Option A: 現状維持 (壁にぶつかるたびに個別回避 + 人手宿題)
- Option B: 環境の Network access を `Full` にする
- Option C: **read-only の `ops-inspect` ワークフローを正規の経路として置き、エージェントが起動して読む**
- Option D: Azure のサービスプリンシパル秘密をサンドボックスの環境変数に置く

## Decision Outcome

Chosen option: **"Option C"**。runner は egress 制限を受けず OIDC で Azure に入れ、エージェントは GitHub MCP で起動と結果取得ができる。追加課金も長期クレデンシャルも発生しない。

あわせて **Option B の一部を併用する** — ネットワークは `Full` ではなく **`Custom` で必要ドメインのみ**開ける (下記 D5)。

### 決定の内訳

- **D1 `.github/workflows/ops-inspect.yml` を `workflow_dispatch` で置く。** エージェントは `actions_run_trigger` で起動し、`get_job_logs` で結果を読む。出力は job summary に人間可読で、末尾に機械可読な JSON ブロックを 1 つ置く (ADR 0029 と同じ流儀)
- **D2 このワークフローは読み取りしかしない。** 入力は**固定の enum** (例: `azure-resources` / `cosmos-free-tier` / `cost-summary` / `fetch-doc`) で受け、**自由入力のコマンドやスクリプトは受け取らない**。理由は権限にある — デプロイ用 SP はサブスクリプション Contributor のままであり ([#46](https://github.com/yomote/mind-inbox/issues/46))、任意コマンドを受ける口を作ると**この迂回路自体が最も強い書き込み経路になる**
- **D3 Claude に考えさせる定期実行は Routine のまま。** Actions に寄せるのは**決定論的な取得だけ**。ADR 0008 が「Actions + API キーはメーター課金が避けられない」として撤去した判断を崩さない。線引きは「**考える必要があるか / 事実を取ってくるだけか**」
- **D4 使い捨てワークフロー (`tmp-*`) を作らない。** 必要が出たら `ops-inspect` に読み取り項目を足す。既存の `tmp-open-pr-107.yml` は撤去する
- **D5 ネットワーク許可の変更は needs-human に積み、許可ドメインは Runbook に記録する。** 環境設定は claude.ai 側にありリポジトリ管理外なので、**何をなぜ許可したか**が repo に残らないと再現できない (ADR 0008 が Routine について受け入れたのと同じ形の負債)

### Positive Consequences

- 「Portal を見て教えてください」の人手宿題を、エージェントが自分で解けるようになる
- 壁にぶつかったときの正解が 1 つに決まり、次のその場しのぎが生まれない
- 追加課金ゼロ・長期クレデンシャルゼロを維持できる
- ADR 0018 の `inspect-env.sh` (実行が人間依存で使われていなかった) に実行経路がつく

### Negative Consequences

- **1 回の確認に数分かかる** (workflow のキュー + 起動)。対話の速度では返ってこない
- **enum を足すたびにワークフローの変更が要る** — 自由度を捨てて安全を取っているので、想定外の調べ物には即応できない
- ネットワークを `Custom` で開けても、**許可リストの管理はリポジトリの外**に残る (D5 の Runbook は写しでしかなく、実体と乖離しうる)
- **MCP の承認ゲート (子セッション起動 / Routine 登録) はこの ADR では解決しない**。ADR 0028 D1 のまま

## Pros and Cons of the Options

### Option A: 現状維持

- Good, because 何も作らなくてよい
- Bad, because 同じ壁に 4 回ぶつかって 4 回別の回避策を作った実績がある。5 回目も起きる
- Bad, because 判断の材料が二次情報の概算にとどまる (ADR 0030 の月額がまさにこれ)

### Option B: Network access を `Full` にする

- Good, because 調べ物で詰まることが原理的に消える。設定 1 箇所で済む
- Good, because Azure の実 API (`management.azure.com`) は元から通っているので、ドキュメントさえ読めれば判断材料は揃う
- Bad, because セッションから任意の宛先へ通信できる状態になる。機微な個人データを扱うリポジトリで、許可範囲を無制限にする理由が弱い
- Bad, because **Azure の実態 (どのリソースがどうなっているか) は結局取れない** — トークンの問題は別だから。壁の 2 つ目は解けない

### Option C: `ops-inspect` ワークフロー (採用)

- Good, because egress とトークンの両方を同時に解く。runner は制限外で、OIDC で Azure に入れる
- Good, because エージェントが自力で起動・取得できる (`actions_run_trigger` / `get_job_logs`)
- Good, because 実行の記録が Actions のログとして残る (誰がいつ何を見たかが追える)
- Bad, because 応答が数分単位になる
- Bad, because 調べたい項目を事前に enum で列挙する必要がある

### Option D: SP 秘密をサンドボックスに置く

- Good, because サンドボックスから直接 `az` が叩ける。最も速い
- Bad, because **長期クレデンシャルをサンドボックスに置くことになり、ADR 0009 の「保存する秘密を作らない」を正面から崩す**
- Bad, because その SP はサブスクリプション Contributor (#46)。漏れたときの被害が最大

## 動作検証

1. エージェントが `ops-inspect` を `actions_run_trigger` で起動し、`get_job_logs` で結果を読める
2. **ADR 0030 の宿題が解ける** — Cosmos DB の free tier がこのサブスクリプションで未使用かを、人手を介さずに判定できる
3. 自由入力を受け付けないこと — enum 以外の入力でジョブが失敗する
4. `tmp-open-pr-107.yml` が Actions 一覧から消えている

## Links

- Issue: 本 ADR の実装 / [#46](https://github.com/yomote/mind-inbox/issues/46) (SP ロール最小化 — D2 の前提)
- 関連 ADR: [0006](0006-azure-access-via-device-code.md) (Azure 対話ログインの制約) / [0008](0008-pr-review-via-cloud-routine.md) (Actions + API キーを課金理由で却下 — D3 が継承) / [0009](0009-on-demand-cd-via-github-actions-oidc.md) (OIDC / no stored secret) / [0018](0018-runtime-verification-in-the-loop.md) (実態の読み取り) / [0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) (MCP ゲートは別問題) / [0029](0029-probe-record-transport-via-issue-comment.md) (artifact が落とせない件の個別回避)
- 参考: `.github/workflows/iac-validate.yml` (同じ理由で先に存在していた実例)
