# ADR archive — 「ADR ではなかったもの」の置き場

> ここにあるのは **ADR ではありません**。過去に ADR として書かれたが、
> アーキテクチャ判断ではなく**開発の運用・プロセスの決め事**だったものを退避した場所です。
>
> **現行のルールはここにはありません。** 今どう動かすかは [`CLAUDE.md`](../../../CLAUDE.md) を見てください。

## なぜ分けたか

`docs/adr/` は「なぜそういう構成 / 技術選択をしたか」を**不変の記録**として残す場所です。
この前提は、**判断が滅多に覆らない領域でしか成立しません**。

退避したものは、AI エージェントが開発を回すための運用規約でした。この領域は毎日変わります。
実際、同じテーマが数日で 3〜4 代替わりしていました:

| テーマ | 系譜 |
| --- | --- |
| PR レビューを誰がやるか | 0008 → 0026 → 0035 → 0052 (4 代) |
| セッション分配の方針 | 0021 → 0033 → 0048 (3 代) |
| PM 機構 | 0035 → 0040 → 0043 (3 代) |
| UX 改善ループ | 0022 → 0027 → 0037 → 0041 (4 代) |
| 実行状態の地図 | 0011 → 0044 (2 代) |

「PR レビューは今どうなっているか」を知るのに 4 本読んで最新を特定する必要がある状態は、
**不変記録の棚として機能していません**。ADR という形式がこの領域に合っていなかった、というのが結論です。

さらに、退避対象の内容は `CLAUDE.md` にも要約されて二重管理になっていました。片方を直しても
もう片方に古い記述が残るため、`CLAUDE.md` の本文に「〜は誤り」「〜は撤回」という取り消し線が
溜まっていく原因になっていました。

## ファイル名から番号を落としてある

`0035-role-split-across-agents-and-actions.md` → `operations/role-split-across-agents-and-actions.md`

**番号があると ADR に見えるため**、退避時に落としています。ここにあるファイルを
「ADR 0035」と呼ばないでください。

## 番号は振り直していない (欠番はそのまま)

`docs/adr/` の番号は **0008 / 0011 / 0014 / 0018〜0022 …** と飛んでいます。これは意図的です。

- **番号は ID であって順序ではない**ので、飛んでいても実害はない
- 振り直すと**リポジトリ外の記録が壊れる** — Issue / PR の本文とコメントに「ADR 0030 を見て」と
  書かれた過去の議論が全部リンク切れになる。これは `sed` が届かない場所であり、
  整理のために過去の記録を壊すのは割に合わない
- 参照は 617 箇所 (うち 196 箇所はプロダクトのソースコード内のコメント) あった

**今後 ADR を新規に書くときも、欠番は埋めずに最大番号 +1 で続けてください。**
採番のしかたは [`../README.md`](../README.md) を参照。

### この退避とは無関係の欠番

- **0049** — open PR [#342](https://github.com/yomote/mind-inbox/pull/342) にあり、main には未着地
- **0050 / 0051** — main に存在しない。`docs/runbooks/claude-pr-review.md` が 0051 を
  参照しているが**リンク切れ** (`debt-check` が検出済み)

## 退避した 29 本

| 旧番号 | ファイル | 内容 |
| --- | --- | --- |
| 0008 | [pr-review-via-cloud-routine.md](operations/pr-review-via-cloud-routine.md) | PR レビューは Claude Code on the web の Routine で行う (API キー Actions / 管理版 Code Review を採らない) |
| 0011 | [github-projects-as-execution-dashboard.md](operations/github-projects-as-execution-dashboard.md) | GitHub Projects は実行状態のダッシュボードに徹し、設計の真実は docs に置く |
| 0014 | [design-comprehension-gate-and-debrief.md](operations/design-comprehension-gate-and-debrief.md) | 設計理解ゲートとゼミ型デブリーフで、user の意思決定・学習をループに組み込む |
| 0018 | [runtime-verification-in-the-loop.md](operations/runtime-verification-in-the-loop.md) | 動作検証をループに組み込む — 実態の読み取り・PR への証跡・ローカルブラウザ検証 |
| 0019 | [independent-judge-agents-security-qa-release.md](operations/independent-judge-agents-security-qa-release.md) | セキュリティ / QA / リリース判定を実装コンテキストから分離した独立 judge エージェントにする |
| 0020 | [hitl-choice-format-and-needs-human-queue.md](operations/hitl-choice-format-and-needs-human-queue.md) | 人間の確認は選択肢形式で出し、人間宿題は needs-human キューに一元化する |
| 0021 | [parent-session-as-pm-orchestrator.md](operations/parent-session-as-pm-orchestrator.md) | 親セッションを PM ハブにして、並行作業は子セッションへ分配する (hub-and-spoke) |
| 0022 | [autonomous-ux-improvement-loop.md](operations/autonomous-ux-improvement-loop.md) | UX 品質は自律改善ループで維持する — 観測・評価・改善 (PR まで) を自動化し、人間は基準定義と例外裁定に徹する |
| 0026 | [cd-watchdog-routine.md](operations/cd-watchdog-routine.md) | CD の赤は毎時の watchdog Routine が検知し、診断と fix PR まで無人で進める |
| 0027 | [ux-improvement-loop-ab-protocol-and-mutation-boundary.md](operations/ux-improvement-loop-ab-protocol-and-mutation-boundary.md) | UX 自律改善ループ M2 — 採点の無人化を先行させ、A/B は実環境の外で回し、改変対象はパスで縛る |
| 0028 | [dispatch-packet-in-issue-and-session-start-preflight.md](operations/dispatch-packet-in-issue-and-session-start-preflight.md) | 分配は「起票パケットを Issue 本文に残す」形にし、並行の衝突は SessionStart の事前提示と CI で防ぐ |
| 0029 | [probe-record-transport-via-issue-comment.md](operations/probe-record-transport-via-issue-comment.md) | UX プローブ記録は artifact ではなく Issue コメントで採点セッションへ運ぶ |
| 0031 | [agent-reaches-outside-via-github-actions.md](operations/agent-reaches-outside-via-github-actions.md) | サンドボックスの外にある事実は GitHub Actions 経由で取る (その場しのぎの回避策を作らない) |
| 0032 | [use-case-acceptance-tests-against-real-wiring.md](operations/use-case-acceptance-tests-against-real-wiring.md) | ユースケース受け入れテストを「mock を通らない実配線」で持つ (L3-real) |
| 0033 | [parent-implements-via-subagent-when-child-sessions-are-gated.md](operations/parent-implements-via-subagent-when-child-sessions-are-gated.md) | 子セッションを起動できない環境では、親が subagent で実装を回す (旧 0021 の改訂) |
| 0035 | [role-split-across-agents-and-actions.md](operations/role-split-across-agents-and-actions.md) | 開発ループの役割を分け、それぞれを「生死が見える場所」に置く |
| 0036 | [merge-gate-as-required-check-and-pm-cadence.md](operations/merge-gate-as-required-check-and-pm-cadence.md) | マージの門を required check で機構化し、PM の運転リズムを定める |
| 0037 | [scheduled-evals-split-mechanical-actions-llm-pm-tick.md](operations/scheduled-evals-split-mechanical-actions-llm-pm-tick.md) | 定期評価を「機械計測 = Actions」と「LLM 採点 = PM tick」に分ける |
| 0038 | [security-checks-as-mechanized-triggers.md](operations/security-checks-as-mechanized-triggers.md) | セキュリティ検査のトリガーを人の判断から機構へ移す (週次 sweep / PR 自動指名 / リリース judge) |
| 0040 | [project-continuity-three-layers.md](operations/project-continuity-three-layers.md) | プロジェクト継続性を 3 層 (機構化された完遂 / 当番 PM / 窓口 PM) で保証する |
| 0041 | [ux-observations-on-git-data-branch.md](operations/ux-observations-on-git-data-branch.md) | UX 観測データの蓄積先を Issue コメントから git データブランチへ移す |
| 0042 | [pm-accept-carryover-and-merge-queue.md](operations/pm-accept-carryover-and-merge-queue.md) | pm-accept は「実装差分が不変の main 追随」に引き継ぎ、直列化は Merge Queue に任せる |
| 0043 | [pm-self-driving-mode.md](operations/pm-self-driving-mode.md) | PM を自走モードにする — 実物指標・週次目標・引く当番・日次ダイジェスト・窓口台帳 |
| 0044 | [stream-lanes-as-the-project-map.md](operations/stream-lanes-as-the-project-map.md) | プロジェクトの地図を固定レーン (stream ラベル) で持ち、Projects board を正式に畳む |
| 0045 | [e2e-artifacts-are-secret-by-default.md](operations/e2e-artifacts-are-secret-by-default.md) | 実環境 E2E の成果物は既定で秘密扱いにし、trace は公開鍵で暗号化して残す |
| 0047 | [security-posture-in-layers-free-tier-first.md](operations/security-posture-in-layers-free-tier-first.md) | セキュリティ対策を「無料枠優先 + 責任分担が重ならない層」で段階導入する |
| 0048 | [child-sessions-are-usable-again-with-a-one-way-poke-channel.md](operations/child-sessions-are-usable-again-with-a-one-way-poke-channel.md) | 子セッションは再び起動できる — 会話は Routine 経由で片道 1 分なので、分配先は往復の少ない作業に限る |
| 0052 | [codex-derived-review-rubric-and-stand-in-judge.md](operations/codex-derived-review-rubric-and-stand-in-judge.md) | PR レビューの基準を Codex の実レビュー 215 件から導出し、Codex 不在の間は代役 judge が読む |
| 0053 | [synthetic-user-exploration-poc.md](operations/synthetic-user-exploration-poc.md) | 合成ユーザーによる探索テストは「採点が繋がってから」「週 1 手動 1 回」から始める |

## ここに置いたものの扱い

- **消していません。** 過去の判断がなぜそうなったかを追う必要が出たら読んでください
- **`Status:` 行は更新しません。** 退避時点の状態のまま凍結しています
  (`Proposed` のまま入ったものは `Proposed` のまま。裁定は現行ルール側で行う)
- **ここを直して運用を変えようとしないでください。** 現行ルールの置き場は `CLAUDE.md` です
