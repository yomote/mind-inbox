# 0051. マージの門の判定を `workflow_run` に移し、門の定義を被判定者 (PR) から切り離す

- Status: Proposed
- Date: 2026-08-12
- Deciders: yomote (PO)
- Consulted: Codex (PR #324 のレビュー指摘)
- Informed: —

Technical Story: [Issue #331](https://github.com/yomote/mind-inbox/issues/331) / [PR #324](https://github.com/yomote/mind-inbox/pull/324)

## Context and Problem Statement

`review-gate` は main のブランチ保護が読む required check (commit status) で、合否を貼る job は `statuses: write` を持つ ([ADR 0036](0036-merge-gate-as-required-check-and-pm-cadence.md))。GitHub Actions は **workflow の YAML をどのブランチから読むかがトリガーごとに違う** — `pull_request` / `pull_request_review` は **PR 側 (merge ref)** の定義、`workflow_run` / `schedule` / `issue_comment` / `pull_request_target` は **default branch (main)** の定義。判定 job は `pull_request` / `pull_request_review` で動いていたため、**PR ブランチで `review-gate.yml` を「無条件 success」に書き換えれば、その PR の review-gate は書き換わった定義で動き、自分自身に合格を貼れた**。

[PR #324](https://github.com/yomote/mind-inbox/pull/324) (Issue #311 / [ADR 0047](0047-security-posture-in-layers-free-tier-first.md) Phase 0) で判定 job の checkout を `ref: default_branch` に固定し、**判定ロジック `cicd/scripts/review-gate/check.py` を PR 側から読む経路**は塞いだ。しかし **どの YAML を実行するかは checkout より前に決まる**ため、yml 自体を書き換える経路は残った (Codex がレビューで指摘 / PM が妥当と判定)。例えると「警備員が読むルールブックは公式版に差し替えたが、警備員の職務規定は訪問者が書いている」状態。

脅威モデル: fork からの PR は GitHub が書き込み権限のあるトークンを渡さないため元から安全。問題は**同一リポジトリのブランチから出す PR** — このプロジェクトのエージェント作業は全部これに当たり、`review-gate` はそのエージェントを拘束するための門である。「門を通る当人が門の定義を書ける」構造が残っていた。

## Decision Drivers

- **門の定義を被判定者の手の届かない場所に置く** — required check の意味はそこにしかない
- **既存の判定内容を 1 ミリも変えない** — pm-accept の引き継ぎ ([ADR 0042](0042-pm-accept-carryover-and-merge-queue.md)) / Codex レビュー / 未解決スレッド / advisory / マージ執行 ([ADR 0040](0040-project-continuity-three-layers.md) D1) は現状維持。変えるのは「どのトリガーで、どの定義で動くか」だけ
- **required check は壊すと全 PR がマージ不能になる** — 変更は最小・可逆・fail-closed であること
- **将来の自分が静かに穴を開けにくい形** — 危険な設定を「知っている人だけが安全に使える」形で残さない
- 監査で評価された「このリポジトリに `pull_request_target` はゼロ」という単純な禁止ルールを保てるか

## Considered Options

- Option A: `workflow_run` で分離する (PR イベントの受け口 no-op + 判定は `workflow_run`)
- Option B: 判定を `pull_request_target` に移す
- Option C: 受容して文書化する (ブランチ保護 + 定義変更のレビューで担う)

## Decision Outcome

Chosen option: **"Option A: `workflow_run` で分離"**。

**PO が 2026-08-12 に選択肢形式 (AskUserQuestion) で A を選択した** ([ADR 0020](0020-hitl-choice-format-and-needs-human-queue.md) の「人間の確認は選択肢形式」に沿い、Issue #331 の 3 案表をそのまま提示して裁定を取った)。決め手は「`pull_request_target` をリポジトリに一切入れない、という**単純で見張りやすい禁止ルール**を保てること」— B は今回の使い方 (PR のコードを checkout しない) なら教科書的に安全だが、**将来 checkout を足した人が静かに重大な穴を開ける**種類の設定であり、安全性が「触る人の知識」に依存する。A のコストは workflow が 1 本増えることだけ。

実装:

1. **`.github/workflows/review-gate-trigger.yml` (新規)** — `pull_request` (types は旧 gate-pr と同一) と `pull_request_review` を受ける **`permissions: {}` の no-op**。やることは「完了して `workflow_run` イベントを起こす」だけ
2. **`review-gate.yml` の `gate-pr`** — トリガーを `workflow_run` (`workflows: [review-gate-trigger]` / `types: [completed]`) に移す。`pull_request` / `pull_request_review` は `on:` から**外す** (残すと穴が残る)。checkout は従来どおり `ref: default_branch`、権限も従来どおり最小 (`contents: read` / `statuses: write` / `pull-requests: write`)、`REVIEW_GATE_EXECUTE_MERGE: "false"` も維持
3. **PR 番号の解決** — `github.event.workflow_run.pull_requests[0].number` を第一経路とし、**空になりうる**ので `repos/{repo}/commits/{head_sha}/pulls` から open PR を引く第二経路を持つ。どちらでも解決できなければ **`::error` を出して exit 1** (静かな成功にしない)。status の貼り先は従来どおり `check.py` が API で引いた **PR の現 head SHA** — `workflow_run.head_sha` は使わない (判定材料と貼り先をずらさない)
4. `issue_comment` (`gate-trusted`) / `merge_group` (`gate-merge-group`) / `schedule` (`sweep`) の 3 job は**既に main から YAML が読まれる**ので手を触れない

### なぜこれで塞がるか (fail-closed の構造)

| 攻撃者 (PR) にできること                                         | 起きること                                                                                                                                                                 |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `review-gate.yml` を「無条件 success」に書き換える               | **使われない**。判定は `workflow_run` = 常に main の定義                                                                                                                   |
| `review-gate-trigger.yml` を書き換える / 消す / `name:` を変える | trigger が起動しない or `workflows:` フィルタに掛からない → `workflow_run` が飛ばない → **commit status が付かない** → required check 未達 → **マージ不能**                |
| trigger に権限を足す                                             | 足せるのは PR 側の定義なので**その run の**権限だが、trigger は checkout も API 呼び出しもしない no-op。判定・status 貼りは別 run (main の定義) が行うため、結果に触れない |
| trigger を別ブランチから `workflow_dispatch` 等で起こす          | 判定 job は head_sha から PR を解決して**正直に**判定するだけ。緑にはできない                                                                                              |

つまり **改竄でできるのは「自分の PR を止めること」だけ**で、偽の合格は作れない。

### 動作検証条件 (ADR 0018 — 実測で確かめる)

1. 通常の PR で `review-gate` の commit status が従来どおり貼られる (🔴 → pm-accept → 🟢 の遷移が起きる)
2. **PR ブランチで `review-gate.yml` を「無条件 success」に書き換えた PR を作り、偽の success が貼られないことを実測する** (Issue #331 完了条件 2)
3. `review-gate-trigger.yml` を PR で削除した場合に status が「付かない」= マージ不能になることを確認する (fail-closed)
4. pm-accept の引き継ぎ (ADR 0042) / Codex レビュー条件 / 未解決スレッド条件が従来どおり効く
5. required check 名 (`review-gate`) が変わっていないこと — ブランチ保護 ruleset の設定変更が**不要**であること

### Positive Consequences

- 門の判定が **被判定者の手の届かない定義**で動く。ADR 0036 の required check がようやく「機構」として成立する
- `pull_request_target` ゼロの禁止ルールを保てる (監査で見るのが `grep pull_request_target` 1 回で済む)
- 権限の最小化 (ADR 0040 / PR #258 の分離) はそのまま維持 — 多層防御が 1 枚増えるだけ
- fork PR の扱いが**改善する**: 従来は fork PR の run が read-only トークンで status を貼れず失敗していた。`workflow_run` の run は base リポジトリ側で動くため、fork PR にも門の判定が貼られる (PR のコードは checkout しないので、`workflow_run` の教科書的な安全な使い方の範囲内)

### Negative Consequences

- **判定 run が PR の Checks タブに出なくなる** — `workflow_run` の run は PR ではなく default branch に紐づく。合否そのものは commit status として PR に出るので門の機能は落ちないが、「判定 job 自体が落ちた」ことに PR 上で気づけない。対処として `watchers.json` に `event: workflow_run` で絞った watcher を 1 行足し、状況ページで見張る
- workflow が 1 本増え、PR ごとに no-op の run が 1 つ増える (Actions 分数は数秒 / public リポジトリは無料枠)
- 判定までに **run 1 本分の遅延** (数十秒) が乗る
- `review-gate-trigger.yml` の `name:` と `review-gate.yml` の `workflows:` が**離れた 2 箇所で結ばれている** — 片方だけ変えると gate が起動しなくなる (両ファイルにコメントで明記)
- この PR がマージされた時点で**既に open な PR** は、次のイベント (push / レビュー / コメント) が来るまで再評価されない。既存の commit status は SHA ごとに残るので消えはしないが、再評価が要るときは PR にコメントを 1 本投稿する (従来どおりの手段)

## Pros and Cons of the Options

### Option A: `workflow_run` で分離する

PR イベントの受け口だけを権限ゼロの no-op workflow に残し、判定は `workflow_run` の別 workflow (常に main の定義) が行う。

- Good, because 判定の定義が確実に main から来る (狙いを直接達成する)
- Good, because `pull_request_target` をリポジトリに入れずに済み、「このリポジトリにそれは存在しない」という**単純な禁止ルール**を保てる
- Good, because 改竄の結果が「起動しない = マージ不能」に倒れる (fail-closed)
- Bad, because workflow が 1 本増える (2 ファイルが `name` で結ばれる保守点も増える)
- Bad, because 判定 run が PR の Checks タブから消える (watcher で補う)

### Option B: 判定を `pull_request_target` に移す

同じ狙いを 1 ファイルで達成する。PR のコードを checkout しない今回の使い方なら教科書的に安全。

- Good, because 最も単純・部品が増えない・遅延も増えない
- Good, because 判定 run が PR に紐づいたまま (Checks タブに出る)
- Bad, because **将来 checkout を足した人が静かに重大な穴を開ける**種類の設定 (「安全なのは今の使い方だから」という条件付きの安全)
- Bad, because 監査で評価された「`pull_request_target` ゼロ」が崩れ、以後は「ある前提でどう使っているか」を毎回読む必要が出る

### Option C: 受容して文書化する

ブランチ保護 + 定義変更のレビューで担う。

- Good, because 作業ゼロ
- Bad, because 「エージェントが自分で門を開けられる」構造が残る。ADR 0036 の門は主にエージェントを拘束するための機構であり、その前提が成立しない
- Bad, because レビュー (Codex / PM) は yml の 1 行改変を見落としうる — 機構で防げるものを人の注意に戻す

## Links

- Issue: [#331 review-gate は PR 側の workflow 定義で動くため、PR が自分に合格を貼れる](https://github.com/yomote/mind-inbox/issues/331)
- 半分の修正: [PR #324](https://github.com/yomote/mind-inbox/pull/324) (check.py を main から実行 / Issue #311)
- GitHub Docs: [Events that trigger workflows — `workflow_run`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)
- 関連 ADR: [0036](0036-merge-gate-as-required-check-and-pm-cadence.md) (マージの門 / required check) / [0040](0040-project-continuity-three-layers.md) (マージ執行・権限分離) / [0042](0042-pm-accept-carryover-and-merge-queue.md) (pm-accept 引き継ぎ / merge_group) / [0047](0047-security-posture-in-layers-free-tier-first.md) (セキュリティの層 — Phase 0 の続き) / [0018](0018-runtime-verification-in-the-loop.md) (動作検証をループに組み込む) / [0020](0020-hitl-choice-format-and-needs-human-queue.md) (選択肢形式の裁定)
