# 0038. セキュリティ検査のトリガーを人の判断から機構へ移す (週次 sweep / PR 自動指名 / リリース judge)

- Status: Accepted (briefing #8 2026-08-11 で PO 承認)
- Date: 2026-08-11
- Deciders: yomote (PO) / PM セッション (方針は 2026-08-11 の PM 対話で選択肢承認済み。ADR の Accept は debrief で)
- Related: [ADR 0019](0019-independent-judge-agents-security-qa-release.md) (独立 judge) / [ADR 0035](0035-role-split-across-agents-and-actions.md) (役割分担と「生死が見える場所」) / [ADR 0036](0036-merge-gate-as-required-check-and-pm-cadence.md) (review-gate) / [ADR 0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) (debt-check — 本 ADR が踏襲する型)

Technical Story: 2026-08-11 の PM セッション対話。セキュリティ検査の実績ゼロ問題と、Codex 自動レビューの沈黙 (PR #231) への対処。

## Context and Problem Statement

セキュリティ検査は設計上 3 層ある — (1) 常時のスキャン ([security-rubric.md](../../.github/claude/security-rubric.md) がスキャナ併用を規定)、(2) 節目の `@codex security review` (ADR 0035 D6)、(3) release-gate の security-reviewer (ADR 0019)。しかし**トリガーが全部「人の判断」のため、全層で実績ゼロ**だった。リリース PR は過去 0 件で (3) は一度も走らず、「節目で PM が指名する」(2) は一度も指名されず、(1) に至っては走らせる場所すら無かった。

これは ADR 0035 が Routine で踏んだ構造の再演である: **起動を人 (または生死の見えない仕組み) に頼った自動化は、動いていないことに誰も気づかない**。

加えて 2026-08-10、Codex の自動 PR レビューが PR #231 で **11 時間沈黙**した。手動の `@codex review` には 1 分で応答したため、原因は枠切れかイベント欠落と推定される — **自動トリガーは欠落してもリトライされない**。review-gate (ADR 0036) が Codex レビューを要求する構成では、この沈黙はコード PR のマージを無期限に止める。

## Decision Drivers

- **トリガーに人の判断を挟まない** — 「PM が判断して回す」は実績上回らない (ADR 0035 と同じ実測)
- **動いた痕跡がリポジトリに残る** — 沈黙と正常が区別できること (CLAUDE.md の自動化新設条件)
- **門を重くしない** — review-gate のリードタイム悪化 (ADR 0036 Negative Consequences) を積み増さない
- 追加課金ゼロ・長期クレデンシャル増加ゼロ (ADR 0008/0009/0031 の driver を継承)
- リポジトリ設定の変更 (人の 1 クリック) に依存せず、コードだけで導入できること

## Decision Outcome

**3 層それぞれのトリガーを機構に移す。** 層の構成自体 (常時 / PR / リリース) は変えない。

| 層           | 検査                                                                                               | トリガー (before → after)                                                           | 痕跡                                |
| ------------ | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------- |
| **常時**     | npm audit (root / bff) + pnpm audit (frontend) + pip-audit (ai-agent / voicevox) + gitleaks (HEAD) | 無し → **週次 cron (月曜 06:30 JST) + workflow_dispatch** (`security-sweep.yml`)    | run 履歴 + `[security-sweep]` Issue |
| **PR**       | `@codex security review`                                                                           | PM の指名 → **敏感パスに触れる PR で review-gate が自動指名** (1 回だけ / advisory) | PR コメント (マーカー付き)          |
| **リリース** | release-gate の security-reviewer                                                                  | 変更なし (リリース PR で起動 — ADR 0019 のまま)                                     | judge レポート                      |

付随して、同じ review-gate に **Codex 自動レビューの再トリガー**を足す: コード PR に Codex レビューが 10 分以上未着なら `@codex review` を 1 回だけ自動投稿する (PR #231 の沈黙への対処)。

### 設計の要点

- **判定はテスト済みの純関数、workflow はその周りの I/O だけ** — sweep の集計は `cicd/scripts/security-sweep/sweep.py`、再トリガー / 敏感パス判定は `cicd/scripts/review-gate/check.py` (review-gate / report-failure と同じ分担)
- **冪等性はマーカーで機械判定** — `<!-- codex-auto-retrigger -->` / `<!-- codex-security-retrigger -->` を本文に埋め、1 PR につき 1 回しか投稿しない。push でリセットしない (沈黙の原因が機構の外なら毎 push 吠えてもノイズにしかならない)
- **敏感パスの初期セット**: `cicd/iac/**` / `.github/workflows/**` / `.github/actions/**` / `**/local.settings*` / `apps/bff/src/**` のうちパスに auth・token・cors を含むもの。パス名による近似であり、取り逃しは release-gate の security-reviewer が持つ
- **自動指名は advisory — review-gate の合否条件には入れない**。門を重くするかは投稿実績を見て PO が判断する
- **sweep はカバー外領域を毎回明示する** (debt-check の型) — SAST 未導入 / 実環境の設定監査 / 依存の悪意ある更新 / git 履歴内の秘密 / 到達可能性の判定 / pip-audit の severity 無し等。「0 件 = 安全」と読ませない
- **ツールのバージョンは固定** (pip-audit 2.9.0 / gitleaks 8.24.3 + sha256 検証) — 「先週と違う結果」の原因がツール更新か依存の変化か区別できなくなるため (ADR 0025 と同じ理由)
- **起票は debt-check と同じ冪等パターン** — 検出 > 0 のときだけ `[security-sweep]` Issue、既存 open があれば追記、0 件は run 緑のみ。ツール出力が読めなかったら「0 件」ではなく「実行できず」として run を落とす (取れなかったものを合格と書かない)

### Positive Consequences

- 3 層すべてに「人が思い出さなくても動く」トリガーが付き、実績ゼロの構造が壊れる
- 依存の既知 CVE・コミットされた秘密が**週次で必ず**照合され、痕跡が Issue に残る
- Codex 沈黙時の詰まり (review-gate 赤のまま) が 10 分で自動回復を試みる
- 敏感な変更 (IaC / CI / 認証) ほどレビューが厚くなる — 現状はその逆だった

### Negative Consequences

- **検出はするが判定はしない** — audit 系の findings は到達可能性を見ていない (dev 依存も同じ桁)。severity 判定と対処優先度は人 (rubric S7) に残る。初回実測は 104 件で、トリアージの宿題が最初から積まれる
- **bot 投稿のメンションに Codex は応答しない (2026-08-11 PR #238 で実測済み)** — github-actions bot の `@codex security review` に「To use Codex here, create a Codex account…」の定型返信が返った。よって bot コメントは**検知と依頼** (「接続済みアカウントから `@codex review` を投稿せよ」/ メンションはバッククォートで殺す) に徹し、実投稿は PM セッション (MCP 経由 = 接続済みアカウント) が行う。PAT で bot を接続済みにする案は長期クレデンシャルの driver と衝突するため不採用
- 敏感パス判定はパス名の近似 — `apps/bff/src/` 配下の認証関連ファイルが auth/token/cors を含まない名前だと素通りする
- 週次 cron は月曜朝に debt-check と近接し、Issue が同時に 2 本動く

## Considered Options

- **A: 現状維持 (人の判断でトリガー)** — 全層で実績ゼロという実測に反証済み。不採用
- **B: Dependabot / CodeQL などリポジトリ設定側の機能を有効化** — 検出品質は高く、依存更新 PR まで自動化できる。ただし**リポジトリ設定の変更 (web UI / 人の作業) が絡み**、コードだけで導入が完結しない。needs-human を増やさず今日動かすことを優先し、今回は自前 sweep を選ぶ。**将来オプションとして残す** — sweep の運用でトリアージ負荷が高いと分かったら、Dependabot (修正 PR の自動作成) への移行を PO に提案するのが自然な次の一手
- **C: 採用案 — 自前の週次 sweep (Actions) + review-gate への自動指名** — 既存の型 (debt-check / review-gate / report-failure) の踏襲だけで作れ、生死が状況ページに乗る
- **D: sweep を毎 PR で回す** — 門が重くなり (ADR 0036 driver に反する)、findings の大半は PR と無関係な既存依存のもの。週次 + dispatch で足りる

## 動作検証 (この ADR が実装されたと言える条件)

1. `security-sweep.yml` の workflow_dispatch 実行で、実リポジトリの findings が severity 集計付きで Issue に起票される (0 件なら起票されず run 緑)
2. Codex レビュー未着のコード PR で 10 分経過後に `@codex review` コメントが 1 回だけ付き、再評価が走っても 2 本目が付かない
3. 敏感パスに触れる PR に `@codex security review` が自動で付き、**review-gate の合否はそれと無関係に判定される**
4. 状況ページに security-sweep の行が出て 🟢/🔴 で判定できる
5. ~~bot 投稿の `@codex ...` に Codex が実際に応答する~~ → **実測で否定** (2026-08-11 PR #238 / 上記のとおり設計を「検知 + 依頼」に変更)。代替の検証: 未着 10 分で依頼コメントが 1 回だけ立ち、PM がそれを受けて投稿した `@codex review` にレビューが付く

## Links

- 関連 ADR: [0019](0019-independent-judge-agents-security-qa-release.md) / [0035](0035-role-split-across-agents-and-actions.md) / [0036](0036-merge-gate-as-required-check-and-pm-cadence.md) / [0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md)
- 審査基準: [security-rubric.md](../../.github/claude/security-rubric.md) (スキャナ併用と severity 判定の正典)
