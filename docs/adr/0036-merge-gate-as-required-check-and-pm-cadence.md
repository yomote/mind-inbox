# 0036. マージの門を required check で機構化し、PM の運転リズムを定める

- Status: Accepted (design-gate 2026-08-10 で PO 承認)
- 運用改訂: [ADR 0042](0042-pm-accept-carryover-and-merge-queue.md) (2026-08-11 PO 裁定) — D1 の pm-accept 失効規則を「実装差分が不変の main 追随には引き継ぐ」に狭め、Considered Options D (merge queue) を実測 (追いつき競争 — PR #243 が 1 日 4 周) で採用に転じた
- Date: 2026-08-10
- Deciders: yomote (PO) / PM セッション
- Related: [ADR 0035](0035-role-split-across-agents-and-actions.md) (役割分担 — 本 ADR はその未決事項「レビューを待つ仕組み」の解) / [ADR 0021](0021-parent-session-as-pm-orchestrator.md) (hub-and-spoke — 条項追加であり supersede しない) / [ADR 0019](0019-independent-judge-agents-security-qa-release.md) / [ADR 0020](0020-hitl-choice-format-and-needs-human-queue.md)

Technical Story: 2026-08-10 のループエンジニアリング体制相談。ADR 0035 が「レビューを待つ仕組みが無い — ここを決めないと Codex を入れても結果が変わらない」を未決として残した。

## Context and Problem Statement

ADR 0035 で役割は分けた (実装 Claude / 技術レビュー Codex / 受け入れレビュー PM / 監視 Actions)。しかし現状のマージは **CI 緑で即マージ** (CLAUDE.md の常設承認) であり、レビュアーを何人足しても**誰もレビューを待っていない**。

さらに実測で分かっている前提が 2 つ:

- **main のブランチ保護は未設定のまま** (po-feedback 初回 2026-08-07 からの持ち越し)。PR を経ない直 push も、未解決スレッドを残したマージも、今は機械的に止まらない
- このリポジトリの歴史は一貫して「**規律は破られ、機構は守られる**」を示している — SessionStart フックが重複作業を捕まえ (ADR 0028)、A/B の境界は起票文でなく CI の diff 検査で縛った (ADR 0027)。「PM は気をつけてレビューを待つ」という運用ルールは同じ轍を踏む

あわせて、ループを常時運転するための **PM の運転リズム (ケイデンス / 並行度)** が未定義で、並行度を上げた日に重複作業 (#159) と ADR 採番衝突が実際に起きている。

## Decision Drivers

- **待たせるなら機構で待たせる** — 運用ルールは実績上守られない
- **生死が見える場所に置く** (ADR 0035 の原則) — 門自体の稼働が状況ページから判定できること
- **アカウント構成で成立すること** — PR 作成者・マージ実行者・PM が実質同一 GitHub アカウント
- **追加課金ゼロ・長期クレデンシャル増加ゼロ** (ADR 0008 / 0009 / 0031 の driver を継承)
- **docs だけの PR まで重くしない** — Codex の枠は全 PR 分は無い (ADR 0035 Negative Consequences)

## Decision Outcome

### D1 — `review-gate` workflow を required status check として作る

PR (base = main) ごとに走る Actions workflow が、以下の**揃うまで赤**を返す:

| 条件 | 対象 | 検出方法 |
| --- | --- | --- |
| **PM の受け入れコメントがある** (「やってほしいことがそこにあるか」— ADR 0035 D3) | 全 PR | PR コメントにマーカー `[pm-accept]` + **いまの head SHA (先頭 7 桁)** + 一言の判定理由 |
| **レビュースレッドが全部解決している** | 全 PR | GraphQL `reviewThreads.isResolved` |
| **Codex の技術レビューが付いている** | **コード PR のみ** (`apps/**` / `cicd/**` に触れる PR。docs / workflow のみの PR は対象外) | Codex connector アカウントのレビュー or コメントの存在 |

- 再評価トリガー: `pull_request` (opened / synchronize) + `pull_request_review` + `issue_comment`。コメント系イベントでは PR の head SHA に対して check を貼り直す
- **push で条件がリセットされる**: 受け入れコメントに head SHA を含める規約により、新コミットが積まれた時点で古い受け入れは自動的に無効になる (レビュー済みでないコードがマージされるのを防ぐ)
- watchers.json に 1 行追加 (痕跡 = PR ごとの run 履歴)

### D2 — main にブランチ保護を設定し、required check で強制する (needs-human 1 クリック)

- required status checks: `review-gate` + `test.yml` の主要ジョブ (+ testing strategy §373 の必須チェック群)
- Require conversation resolution before merging
- 直 push / force push の禁止 (PR 経由のみ)
- **Bypass list は空にする** — 当初「admin (PO) のバイパスを脱出口として残す」としたが、**実測で覆った** (下の実測記録)。このリポジトリはエージェントも PO と同一アカウント (= admin) の資格情報で操作するため、admin バイパス = 全操作素通しになり門が門でなくなる。緊急時の脱出口は **ruleset を一時 Disabled にする** (admin にしかできない操作なので強度は同じ)
- 設定作業は web UI のみ = needs-human Issue に積む。**設定されるまでの review-gate は「見えるが強制されない」advisory 状態**であり、それでも先に入れる (門の生死と判定精度を先に観測できる)

### D3 — CLAUDE.md の常設承認を書き換える

「CI が緑でレビュー指摘が解決していればマージしてよい」→「**CI と review-gate がともに緑ならマージしてよい**」。マージ可否の判断を明文 (エージェントの解釈) から機械 (check の色) へ移す。例外 (リリース PR / needs-human / PO 保留 / Proposed ADR 依存の不可逆実装) は不変。

### D4 — PM の運転リズム: 1 日 1 tick + 週次アンカー

- **日次 tick** (PM セッションの CronCreate): `/status` → 着手する Issue の選択 → 分配 (subagent / 1 クリック子セッション) → open PR のマージ判定 (review-gate の色を見るだけ)。この tick が「レビューを待つ」の待ち時間上限 (最大 1 営業日) を実質定義する
- **週次アンカー** (debrief / 報告会): Proposed ADR と needs-human キューをここで必ず消化する。溜まっていたらエージェントから提案する既定 (ADR 0014) を、定時アンカーに格上げ

### D5 — 並行実装の WIP 上限は 2 本、ファイル境界必須

同時に走らせる実装ストリームは **2 本まで**。分配時のファイル境界宣言 (ADR 0028 起票パケットの必須項目) を伴わない並行は認めない。根拠: 並行度を上げた 2026-08-08 に重複作業 (#159) と ADR 採番衝突が同日に発生した実測。上限の変更は PO 判断 (数字の根拠が「事故が起きた」なので、無事故が続けば上げてよい)。

### 到達経路 (main に入る道を全部数える — ADR 0018 の手順)

守るべき資源 = **main の履歴** (= dev への自動デプロイの入力)。

| 経路 | 現状 | この設計後 |
| --- | --- | --- |
| PR マージ (エージェント / user) | CI 緑なら素通し | review-gate + branch protection が止める |
| **直 push (git push / MCP の push_files)** | **通る (無防備)** | branch protection が拒否 |
| force push | 通る | branch protection が拒否 |
| 自動改善 PR (ADR 0027) | diff ガードのみ | 同じ門を通る (ガードと併存) |
| admin (PO) による ruleset の一時 Disable | — | **意図して残す** (脱出口。bypass 方式は同一アカウント構成で成立せず却下 — 実測記録参照) |

門を 1 枚作っても直 push が開いたままでは意味がない — D1 と D2 は**セットで初めて門になる**。

## Positive Consequences

- 「レビューを待つ」が判断ではなく機械の色になり、Codex 導入 (ADR 0035 D4) が実際に効き始める
- 門自体が Actions にあるので、生死が状況ページで見える (ADR 0035 の原則と一貫)
- PM の受け入れレビューが**マージの前提条件として痕跡に残る** (今は暗黙)
- 追加課金ゼロ・新しいクレデンシャルゼロ

## Negative Consequences

- **マージのリードタイムが延びる** (Codex 待ち + PM tick 待ち。上限は実質 1 営業日)。docs のみ PR は Codex 対象外にして緩和
- **受け入れコメントの形骸化は機構では防げない** — `[pm-accept]` のコピペは検出できない。質は debrief / po-feedback で監査する
- Codex が数日沈黙するとコード PR が詰まる。脱出口は PO による ruleset の一時 Disable のみ (エージェントに waive 権を持たせると門が門でなくなる)
- コメントイベント駆動の required check は SHA への貼り直しが要り、実装がやや繊細 (最初の 2〜3 PR で誤赤・誤緑を観測して直す)

## Considered Options

- **A: ブランチ保護の required approval (標準機能)** — PR 作成者と承認者が同一アカウントになる構成では自己承認できず成立しない。Codex connector の review が approval として数えられるかも未確認。不採用 (Codex 側の approval が実測で使えると分かれば将来簡素化の余地)
- **B: 運用ルールのみ (PM のマージ前チェックリスト)** — 導入は最軽量だが、「規律は破られる」の実績に反する。不採用
- **C: 採用案 — required status check を Actions で実装** — アカウント構成に依存せず、生死が見え、条件を自由に組める
- **D: GitHub merge queue** — 並行 2 本・同一アカウント運用にはオーバーキル。不採用

## 動作検証 (この ADR が実装されたと言える条件)

1. 受け入れコメントの無い PR で `review-gate` が**赤**、`[pm-accept]` コメントを付けると**緑**に変わる (実測)
2. ブランチ保護設定後、review-gate が赤のままマージを試みて**拒否される** (実測)
3. main への直 push が**拒否される** (実測)
4. docs のみの PR は Codex レビュー無しで緑になる (実測)
5. 受け入れ後に push を積むと check が赤に戻る (実測)
6. 状況ページに review-gate の行が出て 🟢/🔴 で判定できる

## 実測記録

- **2026-08-10 (PR #212 / この ADR を入れた PR 自身)**: 検証 1 を通過 — 受け入れコメント前に 🔴「PM 受け入れ ([pm-accept] + 4c411fc) が無い」、コメント投稿 + 再評価で 🟢「受け入れ・スレッド・レビューが揃った」
- **2026-08-10**: PO が D2 を設定完了 — ruleset (Active / required checks: review-gate + test + lint-and-build / conversation resolution / force push 禁止) + `REVIEW_GATE_REQUIRE_CODEX=true` (#211)。Codex GitHub 連携も有効化 (#205)
- **2026-08-10 (PR #213) — 検証 2 の初回は不合格**: review-gate 🔴 のまま squash マージが**通ってしまった**。原因は当初設計どおり Bypass list に Repository admin を入れていたこと — エージェントも PO と同一アカウントで操作するため、admin バイパス = 全操作素通しだった。**設計側を修正** (Bypass list 空 / 脱出口は ruleset の一時 Disable) し、ruleset からバイパスを除去して再実測 → この PR 自身で記録

## 未決

- Codex connector アカウントの正確な login 名 (#205 の有効化後に実測して確定)
- `test.yml` のどのジョブまでを required にするか (testing strategy §373 の具体化)
- WIP 上限 2 の見直し時期 (無事故が続いた場合の緩和基準)
