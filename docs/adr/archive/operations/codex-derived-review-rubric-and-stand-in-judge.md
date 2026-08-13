# 0052. PR レビューの基準を Codex の実レビュー 215 件から導出し、Codex 不在の間は代役 judge が読む

- Status: Proposed
- Date: 2026-08-12
- Deciders: yomote (PO)
- Consulted: — (Codex は本件の対象であり、利用上限で応答不能)
- Informed: —

Technical Story: [Issue #345](https://github.com/yomote/mind-inbox/issues/345)

## Context and Problem Statement

2026-08-12 10:44Z、Codex が **code review の利用上限に到達**し「You have reached your Codex usage limits for code reviews」を返した ([Issue #345](https://github.com/yomote/mind-inbox/issues/345))。技術レビューを Codex が担う構造 ([ADR 0035](role-split-across-agents-and-actions.md) D4) と、**レビュースレッドの resolve は指摘者 (Codex) の再レビューが OK を出してから**という 2026-08-11 の PO 決定 (CLAUDE.md) が組み合わさり、**Codex が黙ると修正を push してもスレッドが畳めず、コード PR のマージ経路が閉じる**。復旧は人間の課金操作か上限回復待ちで、エージェントからは動かせない。

自前の PR レビュー rubric ([`.github/claude/review-rubric.md`](../../../../.github/claude/review-rubric.md) / [ADR 0008](pr-review-via-cloud-routine.md) 由来の軸 A/B/C) は残っていたが、PO 評価は「**Codex ほど洗練されていない**」だった。この評価は測られていなかった — [ADR 0035](role-split-across-agents-and-actions.md) の `:132` に「Codex の指摘の質 — 最初の 3〜5 本で『Claude が見落としたものを拾えたか』を分類して測る」が**未決のまま残り続けていた**。

そこで先に測った ([`docs/reviews/codex-review-analysis-2026-08-12.md`](../../../reviews/codex-review-analysis-2026-08-12.md))。**2026-08-10 13:55 UTC 〜 2026-08-12 10:39 UTC の約 45 時間・46 PR・Codex の inline finding 215 件 (P1 58 / P2 155 / P3 2)** を全件読んだ結果:

- **既存 rubric の軸 C (PR 本文の評価) は 215 件中 0 件。** Codex は PR 本文を根拠に引用はするが、本文自体を finding にしたことが一度もない
- **軸 B の「簡素化」「過剰な抽象化 / 既存ユーティリティの再実装」も 0 件。** スタイル・命名・可読性の指摘も 0 件
- **既存 rubric のどの軸にも無い型が最大勢力だった** — 「**宣言と参照面の乖離**」(決定・型・既定値を変えたのに、それを引用している別ファイルが古い主張のまま残る) が **~60 件 = 全体の 28%**。既存の軸 A-3 は「UI なら MDX / 運用手順なら Runbook」しか見ておらず、射程が狭すぎた
- 「洗練」の実体は観点の網羅ではなく**書き方**だった — 215 件すべてが「命令形の見出し + 条件 → 挙動 → 誤った帰結 + 反証可能な根拠 + 2 択の remedy」という同一テンプレートに乗っており、感想文 (「〜が気になります」) は 0 件

つまり「洗練されていない」の正体は測定可能で、**書き直せる**。

## Decision Drivers

- **Codex 不在でマージ経路が止まらないこと** — 復旧は人間依存で、待つ間もコードは書かれる
- **基準を推測ではなく実測から作ること** — ADR 0035 `:132` の未決を今度こそ実行する
- **独立性の劣化を隠さないこと** — 代役は Claude であり、ADR 0035 D4 の前提 (実装者とレビュアーは別モデル系統) を満たさない
- **Codex が戻ったとき素直に戻せること** — 代役が居座って本来の分離を溶かさない
- 判断の根拠が後から検証できること (原文引用がリポジトリに残ること)

## Considered Options

- Option A: rubric を実データ由来に全面改訂し、それを読む代役 judge (`code-reviewer` subagent) を新設する
- Option B: 既存 rubric のまま、`/code-review` skill で PM が回す (rubric は直さない)
- Option C: Codex の復旧を待つ (レビュー必須を一時的に外し、マージだけ通す)

## Decision Outcome

Chosen option: **"Option A"**。**PO が 2026-08-12 に選択肢形式で「置き換える」「分析はリポジトリに残す」を選択した** ([ADR 0020](hitl-choice-format-and-needs-human-queue.md))。

決定の内訳:

- **D1 ~~`REVIEW_GATE_REQUIRE_CODEX` を false にする~~ (PO がクリック)。Codex レビューの有無を required check の条件から外し、マージ経路を開ける。Codex 復帰時に true へ戻す**
  → **2026-08-12 に D7 が置き換えた** (PO 裁定)。門を**開ける**のではなく、門が要求する独立レビューの**担い手を差し替える**。変数名は改名しない。**ただし 2026-08-12 時点の実測では `REVIEW_GATE_REQUIRE_CODEX` は既に `false`** (`review-gate` の欠落理由が pm-accept の 1 行だけ / [#352](https://github.com/yomote/mind-inbox/issues/352) の記述と整合) — **D7 を実際に効かせるには `true` に戻すクリックが要る**。戻すまでこの PR の変更は「基準と judge と起動経路を足すだけ」で、門はレビューの有無を見ない。戻す順序は「巡回が痕跡を残すのを 1 回確認してから」が安全 (閉じた瞬間に代役レビュー待ちで全コード PR が止まるため)
- **D2 `.github/claude/review-rubric.md` を実データ由来の内容に全面置換する。** ファイル名は変えない ([`security-rubric.md`](../../../../.github/claude/security-rubric.md) `:113` 等から参照されているため)。構成は「指摘の書き方 (R1〜R7) → Severity → 何を探すか (C1〜C9・頻度順) → 再レビューの規律 (R8〜R10) → 自制ルール (R11〜R17) → 出力形式」。**各項目に Codex の原文引用を根拠として添える** — 抽象カテゴリから演繹した項目を 1 つも作らない
- **D3 実測 0 件だった観点を落とす** — 旧・軸 C (PR 本文の評価) と軸 B の「簡素化 / 過剰抽象 / 再実装」。落とした事実と理由は rubric 本文に 1 行残す (黙って消すと、次に誰かが同じ観点を「抜けている」と足し戻す)
- **D4 代役 judge `.claude/agents/code-reviewer.md` を新設する** — [ADR 0019](independent-judge-agents-security-qa-release.md) の judge 群と同じ形 (新品コンテキストの subagent / rubric-as-truth / コードを変更しない / 投稿は呼び出し元)。セキュリティの深掘りは従来どおり security-reviewer へ委譲する
- **D5 CLAUDE.md の resolve 規律を書き換える** — 「指摘者 (Codex) の再レビューが OK を出してから」を「代役 judge (code-reviewer) の再レビューが OK を出してから」に。**2026-08-11 の PO 決定を上書きする変更**であることと、Codex 復帰時の戻し方を明記する
- **D6 分析の全文をリポジトリに残す** — [`docs/reviews/codex-review-analysis-2026-08-12.md`](../../../reviews/codex-review-analysis-2026-08-12.md)。rubric の各項目が「なぜ存在するか」の唯一の説明が原文引用なので、rubric だけ残して根拠を捨てない

### 2026-08-12 追加裁定 (PO / 選択肢形式) — 門は開けず、起動経路を作る

上の D1〜D6 を書いた時点で 2 つ open だった: **門をどう扱うか** (D1 は「開ける」と書いていた) と、Negative Consequences に挙げた「**起動を引く自動経路が無い**」。PO が [ADR 0020](hitl-choice-format-and-needs-human-queue.md) の選択肢形式で両方を裁定した。

- **D7 門は開けず、独立レビューの担い手を差し替える** (D1 を置き換え)。`review-gate` の合否条件 3 を「Codex のレビューがある」から「**独立レビューが 1 本ある**」に読み替え、担い手を Codex **または** 代役 judge のどちらでもよいとする ([`check.py`](../../../../cicd/scripts/review-gate/check.py) の `decide` / `has_standin_review`)。
  - **代役のレビューは pm-accept と同じ強度で push に失効する** — 権限保持者が投稿した `<!-- standin-review -->` + **現 head SHA** を含むコメントだけを数える。**Codex は SHA を縛っていないので非対称だが意図的**: Codex は別アカウント (`chatgpt-codex-connector[bot]`) なので実装者は自分でレビューを貼れないが、**代役の投稿はレビュー対象を書いた本人と同じアカウントから出る**。SHA を縛らないと「1 回レビューを貼れば以後は何を push しても門が開いたまま」になり、[#331](https://github.com/yomote/mind-inbox/issues/331) と同種の穴が代役の導入で新たに空く
  - **却下した案**: (a) `REVIEW_GATE_REQUIRE_CODEX=false` (旧 D1) — 「**上限に当たる → 門を開ける**」を前例にすると門が有限資源の都合で開く運用になる (Option C の Bad と同じ理由が、変数 1 つでも成立してしまう)。(b) dependabot PR だけ Codex 要求から外す — 6 本は解放されるが **#347 が止まったままで、ruleset が読めず [#327](https://github.com/yomote/mind-inbox/issues/327) の auto-merge 405 の原因特定に進めない** (下の「なぜ急ぐか」)
  - 環境変数名 `REVIEW_GATE_REQUIRE_CODEX` は**改名しない** — repository variable なので改名すると PO が web UI で作り直す作業を負う。機構の都合で人に作業を回さない
- **D8 起動経路として専用の PR レビュー Routine を置く** (Negative Consequences の「自動起動が無く、呼ばれなければ沈黙する」への対処)。
  - **当番 PM Routine に相乗りさせない** — [ADR 0035](role-split-across-agents-and-actions.md) D3 が「**やってほしいことがそこにあるか** (意図との一致 = PM)」と「**コードとして正しいか** (意図を知らない別モデル)」を意図的に分けている。同一セッションが両方やると、分けた意味が消える
  - **[ADR 0040](project-continuity-three-layers.md) の条件付き Routine の枠に載せる** — ADR 0035 D1 が Routine を 0 本にした理由は「生死が見えない」。よって (1) **発火ごとに必ず痕跡を Issue コメントに残す** (レビュー対象が 0 本でも「対象なし」と書く — 沈黙と正常を区別するため)、(2) **`watchers.json` に登録して欠落自体を状況ページが検出する**、の 2 条件を満たす形でのみ置く
  - **⚠️ 自動起動はまだ無い (2026-08-12 時点)。** `create_trigger` (MCP) は `cron_expression` / `run_once_at` しか持たず、**GitHub イベント (`pull_request`) を trigger にできない**。cron の暫定 Routine を一度置いたが、**PO 判断で削除した** — 「巡回 (ポーリング) は要らない、イベントで起動すべき」であり、暫定を残すとそれが設計だと誤読されるため。なおリポジトリを持たせること自体は可能で (repo 付きセッションに `persistent_session_id` で束ね、`run_once_at` で送れば配送される / 実測)、**`fire_trigger` は配送されない** ([`child-sessions.md`](../../../runbooks/child-sessions.md) §2 の既知事項)。イベント駆動にするには web UI での登録が要り、[#90](https://github.com/yomote/mind-inbox/issues/90) / [#156](https://github.com/yomote/mind-inbox/issues/156) と同型の needs-human になる ([#352](https://github.com/yomote/mind-inbox/issues/352))。**それまで judge は PM が手で呼ぶ** — 呼び忘れれば沈黙するという Negative はまだ生きている
  - **手順の正典は Runbook に置き、Routine のプロンプトは薄いポインタに留める** ([`review-agents.md`](../../../runbooks/review-agents.md) の「巡回手順」節)。理由は [ADR 0008](pr-review-via-cloud-routine.md) の Negative「Routine 設定はリポジトリ管理外」がプロンプト本文について**解消していない**こと — claude.ai 側の本文は git に無いので、**手順をそこに書くと壊れても diff に出ない**。同じ理屈で subagent 定義も薄いラッパにして観点を rubric へ寄せている (`review-agents.md:41` の既存規約)。**手順変更 = Runbook の PR** であり、Routine は触らない

### なぜ急ぐか (2026-08-12 時点の詰まり)

Codex 停止は 1 本の PR の問題ではなく、**依存関係で 8 本が連鎖して止まっていた**:

```
Codex 停止 (#345)
  └→ review-gate が「Codex レビューが無い」でコード PR を赤 (6 本) /
     再レビューできず未解決スレッドが畳めない (2 本: #330 #222)
       └→ #347 (GitHub 設定を宣言から点検・適用する) がマージできない
            └→ ruleset / ブランチ保護を読めない
               (エージェントの管理系 API は 403 — 2026-08-12 に本セッションでも再実測)
                 └→ #327 auto-merge が GITHUB_TOKEN で 405 になる原因が特定できない
                      └→ マージ執行機構 (ADR 0040 D1) が死んだまま
```

**代役 judge は、この連鎖の最上流を外す最小の一手**である。

### 動作検証条件 (ADR 0018 — 実測で確かめる)

**この ADR は「実装した」では実装されたと言えない。** 次で測る:

1. **次の 5 本のコード PR でこの judge を回し、Codex が過去に拾った類の欠陥 (C1〜C9) を拾えたかを 1 件ずつ突き合わせる** — とくに最大勢力の C1 (宣言と参照面の乖離 / 28%) を、diff の外のファイルを引く形で拾えるか
2. 出力が rubric の形式を守っているか (命令形の見出し / 根拠が 3 種のどれか / remedy 2 択 / 3〜6 文) を、投稿された finding で確認する
3. **偽陽性の率**を数える — 「開いていないファイルを根拠にした」「宣言を実環境と同一視した」(R11 / R12 違反) が出たら rubric に条件を足す
4. **収束するか** — Codex は #288 で 25 件・#258 で 20 件まで再提起を続け全て PM が打ち切った。代役が R15 (収束宣言) に従い 3 ラウンド以内に終えられるかを見る
5. Codex 復帰後、同じ PR に両方を当てて**代役が落とした指摘**を数える (独立性の劣化を数字で持つ)

6. **D7 の門が実際に開くか** — 代役レビューを貼った PR で `review-gate` の commit status が 🟢 になり、`REVIEW_GATE_REQUIRE_CODEX` を触らずにコード PR がマージできること。**そして SHA 失効が効くこと** — レビュー後に 1 コミット push したら赤に戻ること (機構が「押し流し」を本当にやるかは実 PR でしか測れない)
7. **D8 の Routine が痕跡を残すか** — 発火ごとに追跡 Issue にコメントが増え、状況ページの Routine 行が 🟢 になること。**レビュー対象 0 本の回でもコメントが増えること** (沈黙と正常の区別が本当に付いているか)

1 と 5 が満たせないなら、この ADR は Rejected に倒す。

### 初回実測 (2026-08-12 / PR #347) — judge が GitHub を読めていなかった

**この ADR の初回レビューで、judge は PR 本文もレビューコメントも一切読めなかった。** 実測で分かった事実:

- **R8 / R9 (再レビューの規律) が一度も使われなかった。** 前回分の解消状況を名指しで宣言することも、再提起に新しい根拠を添えることも、そもそも「前回の指摘」を取得できないため実行不能だった。動作検証条件 4 (収束するか) は、この状態では測れない
- **原因は frontmatter の `tools:` に GitHub MCP ツールが無かったこと。** この実行環境では GitHub を触る経路が MCP ツール (`mcp__github__*`) に限定されており、シェルからの `gh` / 直接 API は環境側で塞がれている (`403 GitHub access is not enabled for this session.`)。subagent は frontmatter に列挙されたツールしか持たないため、judge は GitHub API に到達する手段を 1 つも持っていなかった
- **同じ穴が judge 5 体すべてにあった** — `code-reviewer` / `security-reviewer` / `qa-reviewer` / `biz-owner-reviewer` / `release-judge` (`ux-reviewer` を含めると 6 体)。[ADR 0019](independent-judge-agents-security-qa-release.md) の release-gate judge 群も同様で、release-judge は release-rubric のチェック項目「未解決の PR レビュースレッドが残っていないか」を GitHub の実状態で判定できない状態だった

対処 (2026-08-12):

- **読み取り専用の GitHub MCP ツールを付与した** — `pull_request_read` / `issue_read` / `get_file_contents` / `list_issues` / `search_issues` / `list_pull_requests` / `get_commit` / `list_commits`。**書き込み・状態変更を伴うツール (コメント投稿 / レビュー作成 / スレッド resolve / merge / issue 更新) は意図的に渡さない** — `_common.md` の共通 9 「judge はコードを変更しない / 投稿は呼び出し元の責務」を、文面ではなく**機構**で守るため。`ToolSearch` も渡さない (後から書き込みツールを読み込めてしまうため)
- **「diff 先読み → PR 本文は後」の順序規律を `code-reviewer` 本文に足した** — PR 本文を先に読むと書いた人の言い分に引きずられ、judge の設計思想 (実装セッションのコンテキストを引き継がない) が壊れる。まず diff だけで findings を出し切り、そのあとに本文・既存コメント・関連 Issue を読み、用途を (a) 本文の主張と実装の食い違い検出 / (b) 既出指摘の再提起でないかの確認 (R8 / R9) / (c) 別 Issue へ切り出し済みかの確認 (R9 / R10) の 3 つに限る。**本文の正当化を根拠に finding を取り下げない** — 取り下げてよいのは実装・仕様・一次情報で反証されたときだけ
- **付与先は 3 体に限った** — `code-reviewer` (R8〜R10 が GitHub 依存) / `qa-reviewer` (qa-rubric の真実ソースに「リリース対象の Issue / PR に書かれたやること」がある) / `release-judge` (release-rubric が「GitHub の実状態」での裏取りと未解決スレッドの確認を要求している)。`security-reviewer` (rubric は diff + スキャナ完結・再レビュー規律を持たない) / `biz-owner-reviewer` (初見ユーザーの立場が PR 本文で汚染される) / `ux-reviewer` (採点対象はデータブランチの JSONL / [ADR 0041](ux-observations-on-git-data-branch.md)、git だけで完結する) には付与していない。**権限は後から足せるが、広げた権限で起きた事故は戻せない**

**未検証**: agent frontmatter の `tools:` が MCP ツール名を受け付けるかは、この時点で実測できていない (subagent を起動して確かめる手段が作業セッションに無かった)。リポジトリ内に先例も無い。次に judge を回したときに、GitHub の読み取りが実際に通ったかを確認すること — 通らなければ ToolSearch の遅延ロードとの相互作用を疑う。

### Positive Consequences

- Codex 不在でもコード PR のレビューと resolve が回り、マージ経路が閉じない
- レビュー基準が**実測された指摘の型**に基づく (「洗練」が言葉ではなく形式になった)
- ADR 0035 `:132` の未決が閉じる。以後「Codex の指摘の質」は 215 件の分類として引用できる
- 実測 0 件の観点が落ちた分、judge が読む量と出すノイズが減る
- Codex 復帰後も rubric は残り、**Codex 自身の質のばらつきを測る物差し**になる (原文引用が基準として残っているため)

### Negative Consequences

- **⚠️ Claude が Claude をレビューしても独立性は回復しない。** [ADR 0035](role-split-across-agents-and-actions.md) `:56` (D4) の根拠は「**同じモデルは同じ盲点を持つ**」であり、実装も代役レビューも Claude である以上この前提は満たされない。**これは Codex の代役ではなく、Codex が戻るまで目隠しを薄くする措置**である。rubric がどれだけ精緻でも、実装時に見えなかったものは同じモデルのレビューでも見えない可能性が高い。Codex が復帰したら D1 を戻し、代役は「Codex を待つ間の埋め合わせ」と「Codex 対象外の PR」に退く
- ~~**起動を引く自動経路が無い。**~~ → **D8 で対処**。ただし残る弱点が 2 つある: (1) Routine は 6 時間おきなので、**Codex の「PR ごとに即座」に比べてレビューが遅い** (最悪 6 時間の遅延)、(2) **Routine のプロンプト本文は git に無い** (claude.ai 側)。プロンプトが壊れても diff に出ないので、`watchers.json` の痕跡監視が唯一の検出手段になる
- **代役レビューは同一アカウントから投稿される。** 機構では「実装者 ≠ レビュアー」を保証できない (D7 で SHA 失効までは縛ったが、**同じセッションが自分の PR にレビューを貼ることは技術的に可能**)。担保は「judge が新品コンテキストの subagent / Routine セッションであること」と「痕跡が public なコメントとして残り PO が後から読めること」の 2 つだけで、これは [ADR 0035](role-split-across-agents-and-actions.md) D4 の「別モデル系統」より明確に弱い
- **rubric が長い。** judge が毎回読むコストが増える (原文引用を根拠として残す代償)。引用を削れば「なぜこの項目があるか」が失われるので、削るなら項目ごと落とす
- 45 時間・46 PR という**短い窓**から導出している。大規模リファクタリング PR / 新機能の初回設計 PR / 依存更新 PR (dependabot は全件レビュー対象外だった) に対する振る舞いは観測できておらず、rubric に書けていない
- Codex の弱点 (R11〜R14 の自制ルール) は写せるが、Codex の強み (`gpg` / `curl` / `git` を実際に叩いて数値で示す) は**代役が実際にコマンドを叩かないと再現しない**。rubric に書いてあることと実行することは別

## Pros and Cons of the Options

### Option A: rubric を実データ由来に全面改訂し、代役 judge を新設する

215 件から指摘の型を導出して rubric を書き直し、それを読む subagent を置く。

- Good, because 「洗練されていない」という評価に対して、測った差分 (軸 C = 0 件 / 軸 B 簡素化 = 0 件 / 未カバーの C1 = 28%) で直接答えている
- Good, because 基準がファイルとして残るので、Codex 復帰後も両者を同じ物差しで比べられる
- Good, because judge が subagent なので、実装セッションのコンテキストを引き継がない ([ADR 0019](independent-judge-agents-security-qa-release.md) と同じ形)
- Bad, because 独立性 (別モデル系統) は回復しない — 埋め合わせであることを毎回明示する必要がある
- Bad, because 自動起動が無く、呼ばれなければ沈黙する

### Option B: 既存 rubric のまま `/code-review` skill で回す

観点は変えず、実行者だけ差し替える。

- Good, because 変更が最小 (ファイルを 1 つも足さない)
- Bad, because PO の「洗練されていない」がそのまま残る。実測でも軸 C / 簡素化が 0 件、最大勢力の C1 が未カバーと分かっている基準を、根拠を持ったまま使い続けることになる
- Bad, because ADR 0035 `:132` の未決が閉じない

### Option C: Codex の復旧を待つ

レビュー必須を一時的に外し、マージだけ通す。

- Good, because 独立性を偽装しない (レビューが無いことが明白)
- Bad, because 復旧時刻が人間の課金操作依存で、その間に書かれるコードは**誰にも読まれずに main へ入る**
- Bad, because 「上限に当たる → 門を開ける」を前例にすると、門が有限資源の都合で開く運用になる

## Links

- Issue: [#345 Codex のコードレビュー利用上限に到達](https://github.com/yomote/mind-inbox/issues/345)
- 実測データ (この判断の一次資料): [`docs/reviews/codex-review-analysis-2026-08-12.md`](../../../reviews/codex-review-analysis-2026-08-12.md)
- 成果物: [`.github/claude/review-rubric.md`](../../../../.github/claude/review-rubric.md) / [`.claude/agents/code-reviewer.md`](../../../../.claude/agents/code-reviewer.md)
- 関連 ADR: [0035](role-split-across-agents-and-actions.md) (役割分担 — D4 「同じモデルは同じ盲点を持つ」/ `:132` の未決を実行) / [0008](pr-review-via-cloud-routine.md) (旧・PR レビュー Routine と軸 A/B/C — **Superseded by 0035**) / [0019](independent-judge-agents-security-qa-release.md) (独立 judge / rubric-as-truth) / [0036](merge-gate-as-required-check-and-pm-cadence.md) (マージの門 / `review-gate`) / [0042](pm-accept-carryover-and-merge-queue.md) (pm-accept の引き継ぎ) / [0018](runtime-verification-in-the-loop.md) (動作検証をループに組み込む) / [0020](hitl-choice-format-and-needs-human-queue.md) (選択肢形式の裁定)
