# 0031. サンドボックスの外にある事実は GitHub Actions 経由で取る (その場しのぎの回避策を作らない)

- Status: Accepted (2026-08-10 / 対話にて PO 承認)
- Date: 2026-08-09
- Deciders: omoteforlab (方向は 2026-08-09 の対話で選択済み。Accept は debrief で)
- Consulted: —
- Informed: —

Technical Story: 2026-08-09、ADR 0030 (永続化) の設計中に Azure の料金・仕様の一次情報が取得できず、月額の判断材料がすべて二次情報の概算になった。

## Context and Problem Statement

エージェントのセッションはサンドボックスの中で動いており、外の世界に届かない経路が 3 種類ある。これまでその場しのぎで個別に迂回してきたが、**同じ壁に 4 回ぶつかって 4 回別々の回避策を作っている**ため、パターンとして固定する。

| 壁                                                                                                                                                                                                             | 実測                                                                                                                                                | これまでの回避                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **egress ポリシー** — 環境の Network access が `Trusted` で、許可リスト外は CONNECT が 403                                                                                                                     | `learn.microsoft.com` / `azure.microsoft.com` / `prices.azure.com` がプロキシログに `connect_rejected` として記録。`*.blob.core.windows.net` も同様 | bicep CLI が取れない → `iac-validate.yml` を作って runner でビルド (#—) / artifact が落とせない → Issue コメント運搬へ ([ADR 0029](probe-record-transport-via-issue-comment.md)) |
| **Azure のトークンが無い** — 対話ログインは device code が要り、無人セッションでは不可 ([ADR 0006](../../0006-azure-access-via-device-code.md))。ただし `management.azure.com` 自体は到達可能 (400 応答を確認) | 「Portal で確認してください」という人手宿題が発生し続けている                                                                                       | `inspect-env.sh` を書いたが、実行は人間 ([ADR 0018](runtime-verification-in-the-loop.md) ①)                                                                                      |
| **MCP の承認ゲート** — `claude-code-remote` は読み取り専用の `list_environments` すら `-32003 requires approval`                                                                                               | 子セッション起動・Routine 登録が不可                                                                                                                | 起票パケット + user の 1 クリック ([ADR 0028](dispatch-packet-in-issue-and-session-start-preflight.md) D1)                                                                       |

一方で GitHub Actions の runner は **egress 制限を受けず**、**OIDC で Azure に入れ** (`deploy.yml` / `golden-path-monitor.yml` で実績あり)、しかも**エージェントは GitHub MCP でワークフローを起動でき (`actions_run_trigger`)、ログを読める (`get_job_logs`)**。つまり「エージェントの手足」として既に使える状態にある。

実際、リポジトリはこのパターンを**無自覚に 2 回発明している** — `iac-validate.yml` (冒頭コメントに「ローカルの agent 環境では egress ポリシーで塞がれるため runner でビルドする」と明記) と、PR を開くためだけの使い捨て `tmp-open-pr-107.yml` (git 履歴に無く、ブランチ上に置かれたまま Actions 一覧に残存)。

## Decision Drivers

- **ループを止めない** — 「人間が Portal を見て教える」が挟まるたびにループが同期待ちになる
- **追加課金を発生させない** — サブスク枠で完結させる ([ADR 0008](pr-review-via-cloud-routine.md) の Driver をそのまま継承)
- **秘密を増やさない** — サンドボックスに長期クレデンシャルを置かない ([ADR 0009](../../0009-on-demand-cd-via-github-actions-oidc.md) の no stored secret)
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
- **D6 セッション内の定期チェックインは native の `CronCreate` を使い、MCP の `send_later` を使わない。** PR / CI の追従で「あとで見に来る」を仕込むとき、`send_later` は claude-code-remote MCP の承認ゲートに当たり**毎回確認を求められる**。一方 `CronCreate` / `CronList` / `CronDelete` は MCP ではなくセッション組み込みのため**承認なしで通る** (2026-08-09 実測: `list_triggers` → `-32003 requires approval` / `CronList` → 正常応答)。制約は「**そのセッションが生きている間だけ・最長 7 日**」だが、PR 追従は追従するセッション自身が持つ仕事なので実害が無い。**セッションを跨いで残る定期実行が要るときだけ Routine (人手登録) に上げる**

### Positive Consequences

- 「Portal を見て教えてください」の人手宿題を、エージェントが自分で解けるようになる
- 壁にぶつかったときの正解が 1 つに決まり、次のその場しのぎが生まれない
- 追加課金ゼロ・長期クレデンシャルゼロを維持できる
- ADR 0018 の `inspect-env.sh` (実行が人間依存で使われていなかった) に実行経路がつく

### Negative Consequences

- **1 回の確認に数分かかる** (workflow のキュー + 起動)。対話の速度では返ってこない
- **enum を足すたびにワークフローの変更が要る** — 自由度を捨てて安全を取っているので、想定外の調べ物には即応できない
- ネットワークを `Custom` で開けても、**許可リストの管理はリポジトリの外**に残る (D5 の Runbook は写しでしかなく、実体と乖離しうる)
- **MCP の承認ゲート (子セッション起動 / Routine 登録) はこの ADR では解決しない**。ADR 0028 D1 のまま。D6 が救うのは「セッション内のチェックイン」だけで、**セッションを跨ぐ定期実行は依然として人手登録**
- **D6 のチェックインはセッションと運命を共にする**。セッションが終われば消えるので、「PR を最後まで見届ける」の保証にはならない (見届ける主体が消えるという意味では整合しているが、取りこぼしはありうる)

## 補足: claude-code-remote MCP が使えない根本原因 (2026-08-09 特定)

> ⚠️ **本節は 2 回書き直している。** ①「サーバー名が UUID だから許可規則が照合できない」→ **誤り** (Gmail も UUID 名だが正常に動く)。②「`requiresUserInteraction` 注釈が付いているから」→ **機構としては近いが不正確**。以下がセッションの実設定ファイルとログを読んで確定させた事実。

### 事実 1: セッションの MCP 設定が、全ツールを `always_ask` にしている

`/tmp/mcp-config-<session>.json` にセッションの MCP 設定が実在し、**ツール単位の `permission_policy`** を持つ。

| サーバー               | 読み取り系                                                                                                   | 書き込み系   | `X-MCP-Server-Origin` |
| ---------------------- | ------------------------------------------------------------------------------------------------------------ | ------------ | --------------------- |
| Gmail                  | `always_allow` (`list_labels` / `search_threads` / `get_message` …)                                          | `always_ask` | `directory`           |
| **claude-code-remote** | **`always_ask`** (`list_triggers` / `list_sessions` / `list_environments` / `list_repos` / `get_session` も) | `always_ask` | **`statsig`**         |

**Gmail は同じファイル内で読み書きを正しく分けている。** つまり設定形式は読み取りの自動許可に対応しており、claude-code-remote 側で**読み取り専用ツールまで一律 `always_ask` になっているのは設定の異常**と考えるのが自然。配信元が `statsig` (フィーチャーフラグ) である点もこれと整合する。

### 事実 2: サーバー側 `always_ask` は `permissions.allow` で上書きできない

**2 通りの名前で実験し、どちらも失敗した。**

| 実験                                                                             | 結果                |
| -------------------------------------------------------------------------------- | ------------------- |
| `permissions.allow` に呼び出し名 `mcp__bf7c680d-…__list_triggers` を追加         | `-32003` のまま     |
| `permissions.allow` に**正式名** `mcp__Claude_Code_Remote__list_triggers` を追加 | **`-32003` のまま** |

これは文書化された挙動と一致する — 「サーバー側で `ask` に設定されたツールは、allow 規則が効かず毎回プロンプトが出る。プロンプトを出せないモードでは拒否される」([permissions docs](https://code.claude.com/docs/en/permissions))。**リポジトリ側の設定では原理的に解除できない。**

### 事実 3: 呼び出し名と承認名が食い違っている

MCP ログの実物:

```text
Tool 'list_triggers' returned -32003 needs_approval
  (tool_name=mcp__Claude_Code_Remote__list_triggers) — surfacing retroactive approval card
```

**呼ぶときは `mcp__bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a__list_triggers`、承認カードと許可判定は `mcp__Claude_Code_Remote__list_triggers`。** 手で許可規則を書こうとしたときに必ず間違える。ログディレクトリも `mcp-logs-Claude-Code-Remote` と `mcp-logs-bf7c680d-…` の 2 つに分裂している。

### 事実 4: 承認カードは「事後」に出るため、承認しても呼び出しは成功しない (2026-08-09 に PO と共同で実証)

PO の「あなたが 2 回呼んで NG にしているのでは」という仮説を検証するため、**1 回だけ呼んでターンを終える**実験を行った。結果:

```text
05:43:42  set_session_title 失敗 -32003 → surfacing retroactive approval card
05:43:47  失敗 -32003                        ← 5 秒後。呼び出しは 1 回なのに 2 件目
          ← ここで PO が承認カードを押した (承認は成功)
05:44:32  set_session_title 失敗 -32003 → また承認カード提示
05:44:35  失敗 -32003
```

確定した 3 点:

1. **2 件目の失敗はエージェントのリトライではない。** 1 回の呼び出しに対して必ず 2 件の失敗が記録される = **ハーネス側の内部リトライ**。前版で「判別できていない」としていた点が決着した
2. **承認は永続しない。** PO が承認した後の呼び出しも `needs_approval` になり、新しいカードが出た (`always_ask` の設定どおり)
3. **承認カードは `retroactive` — 呼び出しが失敗した後に出る。** 既に失敗した呼び出しは承認しても復活せず、次の呼び出しはまた失敗から始まる

**したがって成功する経路が構造的に存在しない。** 「実行前に承認を要する」という方針に対し、承認の提示が「実行後」になっているため噛み合っていない。これは方針 (読み取り系まで `always_ask` にするか) の是非とは別の、機構としての不整合である。

なお **承認 UI 自体は正常に動作している** — カードは表示され、PO は押せた。ローカルの設定ファイルには何も書かれず、承認はサーバー側に記録される。

### 公開されている同種の報告 (2026-08-09 調査)

**この症状は広く報告されている。** `anthropics/claude-code` の Issue に同型のものが複数あり、いずれも **open・Anthropic からの公式回答なし**。

| Issue                                                                                                                                                                                                  | 内容                                                                                                                                                                                                                             | 一致度                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| [#61044](https://github.com/anthropics/claude-code/issues/61044)                                                                                                                                       | Routine (無人 CCR セッション) で MCP ツールが `requires approval` で失敗。承認 UI が出ない。ラベル `area:mcp` / `area:permissions` / `area:routines` / `platform:web` / `duplicate`。**「以前は動いていた (regression)」と明記** | 症状は同じ。ただし**無人 Routine 限定**の報告      |
| [#61027](https://github.com/anthropics/claude-code/issues/61027) / [#61097](https://github.com/anthropics/claude-code/issues/61097) / [#61143](https://github.com/anthropics/claude-code/issues/61143) | 同上。コネクタを Routine に追加し「承認不要」と UI が表示していても失敗                                                                                                                                                          | 同上                                               |
| [#43397](https://github.com/anthropics/claude-code/issues/43397)                                                                                                                                       | クラウドの定期タスクが MCP コネクタにアクセスできない                                                                                                                                                                            | 近縁                                               |
| [#76264](https://github.com/anthropics/claude-code/issues/76264)                                                                                                                                       | **読み取り専用のセッション管理 MCP ツールが毎回承認を要求し、事前承認する方法が無い。** `permissions.allow` / `bypassPermissions` / `PreToolUse` フックの **3 つすべてが効かない**ことを報告者が検証済み                         | **本 ADR の実験結果と独立に一致**。事実 2 の裏付け |

**未報告と思われる点 (本リポジトリ固有の発見)**: 上記はいずれも「無人だから承認 UI が出ない」という筋書きだが、**本セッションは対話セッションで承認カードが実際に出て、PO が承認に成功したにもかかわらず呼び出しが失敗した**。カードが `retroactive` (失敗後) に出ること、および約 5 秒後のハーネス内部リトライは、公開 Issue には見当たらない。報告する価値がある。

### 結論

- **リポジトリ側でも user の設定でも直せない。** 原因は Anthropic 側がセッションごとに配信する MCP 設定にあり、読み取り専用ツールまで `always_ask` になっている
- **「前はできたはず」は妥当** — 配信がフラグ由来で、ログのサーバー名が旧 `Claude-Code-Remote` から UUID へ移行した形跡がある
- 取れる手は変わらない: ① MCP を経由しない代替 (D6 の `CronCreate`) ② `claude --teleport` でターミナルに引き込む (事実 4 の 2 秒再試行があるため成功するとは限らない) ③ user が web UI で操作する

**未確認の 1 点**: 事実 4 の「2 秒後の再試行」がクライアント側の実装なのかサーバー応答なのかは、ログからは判別できていない。

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
- 関連 ADR: [0006](../../0006-azure-access-via-device-code.md) (Azure 対話ログインの制約) / [0008](pr-review-via-cloud-routine.md) (Actions + API キーを課金理由で却下 — D3 が継承) / [0009](../../0009-on-demand-cd-via-github-actions-oidc.md) (OIDC / no stored secret) / [0018](runtime-verification-in-the-loop.md) (実態の読み取り) / [0028](dispatch-packet-in-issue-and-session-start-preflight.md) (MCP ゲートは別問題) / [0029](probe-record-transport-via-issue-comment.md) (artifact が落とせない件の個別回避)
- 参考: `.github/workflows/iac-validate.yml` (同じ理由で先に存在していた実例)
