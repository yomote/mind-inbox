# 独立 judge エージェント (security / QA / release) の運用

実装セッションとは**別コンテキスト・別役割**の審査役 3 体を、ループのどこで・どう走らせるかの手順。
判断記録: [ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md)。
既存の PR レビュー judge ([ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) / [Runbook](claude-pr-review.md)) と役割分担して動く。

## Trigger

セキュリティレビュー / QA レビュー / リリース判定を回したい・観点を変えたい・動かない時。

## 全体像 (ループのどこに入るか)

```
実装セッション ── pr-readiness (自己チェック)
      │ PR 作成
      ▼
PR レビュー Routine (ADR 0008: doc 整合・バグ・テンプレ)
      ├─→ security-reviewer subagent (PR にセキュリティ関連差分がある時)
      ▼
人間 merge
      │ 節目が来たら: リリース PR (main → release) を開く
      │ ※ main への機能 PR / dev への日常 auto-deploy には差し込まない
      ▼
/release-gate skill (リリース PR で起動 — Routine or 手動)
      ├─ 開発リリースレポート作成 (事実の列挙のみ・自己判定なし)
      ├─→ security-reviewer (スキャナ総動員 + 動的チェック)          ┐
      ├─→ qa-reviewer (受け入れマトリクス + ゴールデンパス/UI 挙動 L3) ┤ 並列・新品コンテキスト
      ├─→ biz-owner-reviewer (実操作ウォークスルー + 違和感)          ┘
      ▼
release-judge ← 4 レポート + CI レイヤ別結果を突合
      │   (機能揃ってる? コンセプトとズレてない? テスト/QA やった?
      │    → 🟢 GO / 🟡 / 🔴 NO-GO + 宛先つき作業指示リスト)
      │   blocker はリリース PR のスレッドに → 未解決ならマージ不可 (ブランチ保護)
      ▼
人間がリリース PR をマージ → stg/prod へ deploy (deploy-*.sh)
```

分離の原則: **judge は必ず subagent (新品コンテキスト) として起動する**。実装セッション自身に審査させない。役割ごとの持ち物:

- **security-reviewer**: 環境で使える脆弱性スキャナを総動員 (npm audit / pip-audit / osv-scanner / gitleaks / semgrep / bandit / trivy 等) し、結果を rubric に照らして判定する。アプリを起動できる場合は動的チェック (外部通信の観察・未認証アクセス実測・応答ヘッダ) も実施。使えなかった分は UNKNOWN 明記
- **qa-reviewer**: 「欲しかった機能が揃っているか / 変な動きをしないか」を受け入れマトリクスで検証し、**ゴールデンパス・UI 挙動・ユーザビリティ観点のシナリオテスト (L3 E2E) を作成・実行**する。L3 レイヤの所有者。ビジュアルの美的評価はしない (MDX 仕様との乖離のみ)。プロダクトコードは触らない (テストコードのみ)
- **biz-owner-reviewer**: ビジネスオーナーとして**アプリを実際に起動・操作**し (stub モード + Playwright、スクショつき)、文言・導線・期待とのズレ・コンセプト体現・「普通に考えておかしいよね」の違和感を報告する。アサーション的な仕様突合はしない (QA の担当)
- **release-judge**: 4 レポート + CI を突き合わせ、「この品質で出してよいか」「コンセプト ([`docs/concept_deck.md`](../concept_deck.md)) とズレていないか」を判定する。FAIL/UNKNOWN は**宛先つき作業指示リスト** (実装 / qa-reviewer / security-reviewer / biz-owner-reviewer / 人間) に変換して返す。レポートが欠けた領域は UNKNOWN = GO は出ない (デフォルト NO-GO)

## 構成ファイル (rubric-as-truth)

| 役割 | 審査基準 (直すのはここ) | subagent 定義 |
| --- | --- | --- |
| security-reviewer | [`.github/claude/security-rubric.md`](../../.github/claude/security-rubric.md) | `.claude/agents/security-reviewer.md` |
| qa-reviewer | [`.github/claude/qa-rubric.md`](../../.github/claude/qa-rubric.md) | `.claude/agents/qa-reviewer.md` |
| biz-owner-reviewer | [`.github/claude/biz-owner-rubric.md`](../../.github/claude/biz-owner-rubric.md) | `.claude/agents/biz-owner-reviewer.md` |
| release-judge | [`.github/claude/release-rubric.md`](../../.github/claude/release-rubric.md) | `.claude/agents/release-judge.md` |

subagent 定義は薄いラッパで、観点はすべて rubric 側に置く。**観点変更 = rubric の PR** (Routine や agent 定義をいじらない)。

## Steps

### 初回セットアップ: `release` ブランチとブランチ保護

リリースイベント = **リリース PR (`main → release`)**。一度だけ準備する:

1. ~~`release` ブランチを main から作る~~ — **実施済み** (2026-08-06、`main` の `0bba631` から作成)
2. **[要 user・ワンクリック]** ブランチ保護: <https://github.com/yomote/mind-inbox/settings/branches> → **Add branch ruleset** (または Add rule):
   - Target branch: `release`
   - **"Require conversation resolution before merging"** を有効化 — judge の blocker スレッドが未解決のままだとマージ (= リリース) できない。これがゲートの強制力
   - (任意) Require status checks で test.yml を必須に
   - ※ API からは設定できない (管理権限の認証が要る) ため、ここだけ手動
3. **[要 user・任意]** ゲート自動起動の Routine: <https://claude.ai/code/routines> → New routine。main への機能 PR 用レビュー Routine (ADR 0008) とは**別物として共存**する
   - **名前**: `Release gate (mind-inbox)` / **リポジトリ**: `yomote/mind-inbox`
   - **トリガー**: GitHub event / `pull_request` → `opened` と `synchronize` / `Is draft` = `false`
   - **Permissions**: `Allow unrestricted branch pushes` は OFF (qa-reviewer のテスト commit は `claude/` ブランチで可)
   - **プロンプト** (貼り付け用):

     ```text
     まず対象 PR の base ブランチを確認してください。
     base が release でない場合: この PR はリリース PR ではないので、何もせず終了する。
     base が release の場合: これはリリース PR。リポジトリの release-gate skill
     (.claude/skills/release-gate/SKILL.md) の手順に従ってフルゲートを実行する。
     - 開発リリースレポートを作成し、security-reviewer / qa-reviewer /
       biz-owner-reviewer subagent を並列起動し、release-judge に 4 レポートを集約させる
     - release-judge の FAIL / blocker 項目は、このリリース PR の該当箇所への
       レビュースレッド (指摘 + 解除条件) として投稿する
     - サマリ (verdict + チェックリスト + 作業指示リスト) を PR にコメントする
     - 再実行時 (push 後) は解消済みスレッドを resolve する
     merge / deploy はしない。判定とスレッド管理までが責務で、マージは人間が行う。
     ```

Routine を作らない場合も、リリース PR を開いた後に手元セッションで `/release-gate` と言えば同じゲートが走る (自動化しないだけで運用は成立する)。

### リリースを出す (節目が来たら)

1. 「きっちりした版」を出すと決めたら、リリース PR を開く: `main → release` (タイトル例: `Release: Phase N 完了版`)
2. フルゲートが走る (Routine 自動 or セッションで `/release-gate`)。blocker はリリース PR のスレッドになる
3. 🔴 なら作業指示リストを user 合意の上でディスパッチ → 対応 push → release-judge を**再起動**して再判定 (スレッド resolve)
4. 全スレッド解決 + 🟢/受け入れ済み 🟡 → **人間がマージ** → tag を打って stg/prod へ deploy (`deploy-*.sh`)

**回すのは節目だけ**: main への機能 PR・dev への日常 auto-deploy・docs のみの変更には差し込まない (CI + PR レビュー judge の守備範囲)。

### PR 時にセキュリティレビューも走らせる (任意強化)

PR レビュー Routine のプロンプト末尾 (web UI: <https://claude.ai/code/routines>) に 1 行足す:

```text
6. diff に認証・入力検証・秘密情報・インフラ (Bicep/workflow)・依存追加が含まれる場合は、
   security-reviewer subagent を起動して .github/claude/security-rubric.md での審査を受け、
   その findings (blocker/major) もサマリに含める。
```

subagent はレビューセッション内でも新品コンテキストで起動されるため、役割分離は保たれる。

### 単発でセキュリティ / QA レビューだけ欲しい

開発セッションで「security-reviewer で今の diff を見て」「QA 観点でレビューして」と言えば、Agent tool 経由で該当 subagent が起動する。**結果の verdict を実装セッションが値切らない**こと (blocker は直すか、直さない理由を user が明示的に引き受ける)。

## Verification

- [ ] `/release-gate` で開発レポート + security / QA / ビジネスオーナーレポート + release-judge の verdict (🟢/🟡/🔴) が出る
- [ ] ビジネスオーナーレポートにスクショつきウォークスルーログがある (起動できなかった場合は「未実施」が明記され、release-judge 側で UNKNOWN になる)
- [ ] リリース PR で blocker がスレッド化され、未解決のままマージできない (ブランチ保護が効いている)
- [ ] セキュリティレポートに「スキャン実行状況」表があり、回せなかったツールが UNKNOWN と明記されている
- [ ] QA レポートに受け入れマトリクスとテスト実行結果 (pass/fail、未実行は明記) がある
- [ ] qa-reviewer の作ったテストにプロダクトコードの差分が混ざっていない
- [ ] release-judge のチェックリストに UNKNOWN が出たとき、GO になっていない (デフォルト NO-GO が効いている)
- [ ] 実装セッションが自分で審査を代行していない (レポートが subagent 発か、セッションログで確認できる)

## Rollback / コスト制御

- judge を減らす: release-gate 時に qa-reviewer をスキップしたい等は skill を直すのではなく、その回のプロンプトで明示指定 (恒久変更なら ADR 0019 の見直し)
- 観点を軽くする: 各 rubric の観点を削る PR を出す
- 廃止: `.claude/agents/` と `.claude/skills/release-gate/` を消し、ADR 0019 を Deprecated にする

## Common Issues

### judge が blocker を出したが誤検知に見える

- rubric は「悪用経路 / 根拠を 1 文で書けない指摘は落とす」ルールを持つ。誤検知が続くなら rubric に除外条件を追記する PR を出す (セッション内で judge を説得して取り下げさせない — 記録が残らない)。

### release-judge が UNKNOWN だらけで判定不能

- 原因: CI 結果や前回リリース基点にアクセスできない環境で走っている。
- 対処: 範囲 (commit range) と CI 結果を呼び出しプロンプトで明示的に渡す。UNKNOWN のまま GO にしないのは仕様。

### スキャンツールが環境に無く UNKNOWN だらけになる

- 原因: クラウドセッション / Routine 環境に gitleaks / semgrep 等が未インストール。
- 対処: `npm audit` (lockfile 同梱) と git grep 代替は常に動くので最低線は出る。恒久対応するなら
  スキャナを CI (test.yml とは別 job) に足す — その追加は security-reviewer の「提案」を起点に PR で行う。

### qa-reviewer が作ったテストをどう取り込むか

- テストは QA レポートと一緒に提示される。取り込む場合はテストファイルのみの commit / PR にする
  (プロダクトコードの差分が混ざっていたら rubric 違反 — 取り込まず finding として扱う)。
- 取り込んだ L3 は以後 CI (`npm run test:e2e`) の資産になる。

### 実装セッションが「自分でチェックしたから GO」と言う

- それが [ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md) で禁止した状況そのもの。`/release-gate` を通し直す。

## Related

- 判断記録: [ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md)
- PR レビュー judge: [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) / [Runbook](claude-pr-review.md) / [`review-rubric.md`](../../.github/claude/review-rubric.md)
- テスト戦略 (L0〜L4 と QA の分担): [`docs/testing/strategy.md`](../testing/strategy.md)
- Subagents: <https://code.claude.com/docs/en/sub-agents>
