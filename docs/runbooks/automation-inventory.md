# 自動化インベントリ — 今どれが生きているか

> **なぜこの doc があるか**: 判断は ADR に、手順は Runbook に、進捗は Issue にある。
> しかし「**この自動化は今も動いているのか**」を書いた場所がどこにも無く、毎回ゼロから
> 調べ直していた。しかも Routine はクラウド側にいてリポジトリからは存在すら見えない。
> 増やす側だけ速くて畳む側が動かないので、増えるほど把握できなくなる — 2026-08-10 に
> PO が「仕掛かり中が多すぎて把握できない」と言ったのはこの構造が原因。
>
> **この表が唯一の真実**。自動化を足したらここに 1 行足す。足せないなら作らない。

## 状態の記号

| 記号 | 意味 |
| --- | --- |
| 🟢 | 動作を実測で確認済み (日付つき) |
| 🟡 | 動くが繋がっていない / 出力が放置されている |
| 🔴 | 止まっている、または赤いまま |
| ⬜ | 未着手 (設計だけある) |
| ❓ | 確認手段が無い / 未確認 |

## 一覧 (実測日 2026-08-10)

| # | 自動化 | 何をする | 住んでいる場所 | 状態 | 生死の見かた |
| --- | --- | --- | --- | --- | --- |
| 1 | `test` | PR ごとに L0〜L3 を回す | Actions | 🟢 08-10 | PR に sticky コメントが 2 本付く (lint/build, test) |
| 2 | `deploy` | main → dev 自動デプロイ + 実測 | Actions | 🔴 08-10 | 直近 3 run すべて failure。落ちるのは**常に最終ステップ**「Golden path scenario (UI 込み E2E・実環境)」 |
| 3 | `golden-path-monitor` | 毎朝 07:00 JST に実環境を通す + UX プローブ | Actions (cron) | 🔴 08-09 / 08-08 | 同上のステップで 2 晩連続 failure。**前段の記録投稿までは成功している** |
| 4 | `build-images` / `iac-validate` / `adr-number-guard` / `auto-improve-guard` | image 事前ビルド / bicep 検証 / ADR 採番衝突 / 自動改変の範囲 | Actions | 🟢 08-10 | PR・push のチェック欄 |
| 5 | `refresh-infra-diagram` | 週次で構成図を実環境から再生成 | Actions (cron) | 🟡 08-09 | run は success。ただし**生成された PR #193 が開きっぱなし** |
| 6 | `ops-inspect` | サンドボックス外の事実を read-only で取る | Actions (手動) | 🟢 08-09 | 3 run すべて success。エージェントが `actions_run_trigger` で叩く |
| 7 | SessionStart フック | 着手前に origin/main の差分と次の ADR 番号を出す | リポジトリ | 🟢 08-10 | セッション冒頭に自動表示。今日 0034→0035 を正しく提示 |
| 8 | ux-probe 記録の運搬 | 毎朝のプローブ記録を Issue へ自動投稿 | Actions | 🟢 08-09 | [#162](https://github.com/yomote/mind-inbox/issues/162) に毎朝コメントが増える |
| 9 | maint-check Routine | 週次で docs 陳腐化・デッドコードを検出 | **claude.ai (Routine)** | 🟢 08-10 | `[maint-check]` の Issue が立つ ([#195](https://github.com/yomote/mind-inbox/issues/195) が初回) |
| 10 | cd-watchdog Routine | 毎時 CD の赤を診断して fix PR | **claude.ai (Routine)** | 🔴 | `cd-watchdog` ラベルの Issue。**最後の痕跡は 08-08 の [#151](https://github.com/yomote/mind-inbox/issues/151)**。以後 #2/#3 の赤 5 回に無反応 |
| 11 | ux-judge Routine | 毎朝 UX プローブを採点してスコア投稿 | **claude.ai (Routine)** | 🔴 | [#127](https://github.com/yomote/mind-inbox/issues/127) にコメントが増える。**0 件のまま** ([#194](https://github.com/yomote/mind-inbox/issues/194)) |
| 12 | PR レビュー Routine ([ADR 0008](../adr/0008-pr-review-via-cloud-routine.md)) | 開いた PR をレビューする | **claude.ai (Routine)** | ❓ | PR にレビューコメントが付く。直近の PR #196 には**付いていない** (bot の CI サマリのみ。ただし開いて約 10 分でマージされたため周期の問題かもしれない) |
| 13 | release-gate (judge 4 役) | リリース PR で Go/No-Go を判定 | skill + subagent | 🟡 | `main → release` の PR。**過去 0 件**。ブランチ保護も未設定なので止める力が無い |
| 14 | UX 自律改善ループ 段3 | 採点が下がったら A/B して勝った案を PR | — | ⬜ | 未着手 ([#166](https://github.com/yomote/mind-inbox/issues/166))。設計は [ADR 0027](../adr/0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) で承認済み |

## いちばんの構造的な穴 — Routine の生死が見えない

**#9〜#12 の 4 本はリポジトリの外 (claude.ai の Routine) にいる。** リポジトリからは
存在も設定も発火履歴も見えず、しかも実行環境によっては `list_triggers` すら承認ゲートで
弾かれる (2026-08-09 / 08-10 に実測)。つまり **「沈黙している」と「異常なしで静かに
通った」を区別する手段が無い**。

- #9 と #8 は「動いたら Issue にコメントが増える」ので生死が分かる → だから 🟢 と判定できた
- #10 と #11 は「異常が無ければ何も残さない」設計なので、沈黙と正常が同じ見え方になる → 🔴 とも 🟢 とも言えない

**この差が判定可能性の分かれ目**。次に自動化を足すときの必須条件は
「**動いたら痕跡がリポジトリに残ること**」。異常時だけ喋る設計にしない。

## 畳む / 直すの推奨 (PO 判断待ち)

| 対象 | 推奨 | 理由 |
| --- | --- | --- |
| #2 / #3 の赤 | **直す** | 唯一「実環境が壊れている」を示している信号。ここが赤いままだと他の緑が意味を失う |
| #10 cd-watchdog | **直す (ただし Actions 側へ移す)** | 見張りの本体。ただし Routine のままでは生死が見えないので、痕跡が残る形にしないと同じことが起きる |
| #11 ux-judge | **畳む** | 一度も動いていない。採点は必要になったときに手で回せる (runbook あり)。#127 は空のまま残す |
| #13 release-gate | **畳む (凍結)** | 部品は残すが、リリース PR 運用を始めるまで「有る」ことにしない。ブランチ保護を入れる日に解凍する |
| #14 改善ループ 段3 | **畳む (Issue は残す)** | 未着手。#11 が動いていない以上、その下流である段3 は前提が無い |
| #5 の PR #193 | **掃除** | マージするか閉じる。開きっぱなしが「動いていない」ように見える原因になっている |
| #12 PR レビュー | **確認** | 生きているかを次の PR で 1 回見る。付かなければ畳む |

> 上は 2026-08-10 時点のエージェント推奨であり、**決定ではない**。PO が裁定したら
> この節を「決定」に書き換え、畳んだものは表から消さずに状態を更新する
> (消すと「そんな仕組みがあった」ことごと失われるため)。

## 関連

- [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) (PR レビュー Routine) / [ADR 0026](../adr/0026-cd-watchdog-routine.md) (cd-watchdog) / [ADR 0027](../adr/0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) (改善ループ) / [ADR 0029](../adr/0029-probe-record-transport-via-issue-comment.md) (記録の運搬) / [ADR 0031](../adr/0031-agent-reaches-outside-via-github-actions.md) (ops-inspect)
- 個別の運用手順: [cd-watchdog](cd-watchdog.md) / [ux-probe-judge](ux-probe-judge.md) / [ops-inspect](ops-inspect.md) / [review-agents](review-agents.md) / [refresh-infra-diagram](refresh-infra-diagram.md)
