# 独立 judge エージェント (security / QA / release) の運用

実装セッションとは**別コンテキスト・別役割**の審査役 3 体を、ループのどこで・どう走らせるかの手順。
判断記録: [ADR 0015](../adr/0015-independent-judge-agents-security-qa-release.md)。
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
      │ 節目のリリース (フェーズ完了版 / stg・prod 昇格 / 不可逆変更) の前
      │ ※ dev への日常 auto-deploy には差し込まない
      ▼
/release-gate skill
      ├─ 開発リリースレポート作成 (事実の列挙のみ・自己判定なし)
      ├─→ security-reviewer (スキャナ総動員 + 動的チェック)          ┐ 並列・
      ├─→ qa-reviewer (受け入れマトリクス + ゴールデンパス/UI 挙動 L3) ┘ 新品コンテキスト
      ▼
release-judge ← 3 レポート + CI レイヤ別結果を突合
      │   (機能揃ってる? コンセプトとズレてない? テスト/QA やった?
      │    → 🟢 GO / 🟡 / 🔴 NO-GO + 宛先つき作業指示リスト)
      ▼
人間が deploy ボタン (deploy-*.sh)
```

分離の原則: **judge は必ず subagent (新品コンテキスト) として起動する**。実装セッション自身に審査させない。役割ごとの持ち物:

- **security-reviewer**: 環境で使える脆弱性スキャナを総動員 (npm audit / pip-audit / osv-scanner / gitleaks / semgrep / bandit / trivy 等) し、結果を rubric に照らして判定する。アプリを起動できる場合は動的チェック (外部通信の観察・未認証アクセス実測・応答ヘッダ) も実施。使えなかった分は UNKNOWN 明記
- **qa-reviewer**: 「欲しかった機能が揃っているか / 変な動きをしないか」を受け入れマトリクスで検証し、**ゴールデンパス・UI 挙動・ユーザビリティ観点のシナリオテスト (L3 E2E) を作成・実行**する。L3 レイヤの所有者。ビジュアルの美的評価はしない (MDX 仕様との乖離のみ)。プロダクトコードは触らない (テストコードのみ)
- **release-judge**: 3 レポート + CI を突き合わせ、「この品質で出してよいか」「コンセプト ([`docs/concept_deck.md`](../concept_deck.md)) とズレていないか」を判定する。FAIL/UNKNOWN は**宛先つき作業指示リスト** (実装 / qa-reviewer / security-reviewer / 人間) に変換して返す。レポートが欠けた領域は UNKNOWN = GO は出ない (デフォルト NO-GO)

## 構成ファイル (rubric-as-truth)

| 役割 | 審査基準 (直すのはここ) | subagent 定義 |
| --- | --- | --- |
| security-reviewer | [`.github/claude/security-rubric.md`](../../.github/claude/security-rubric.md) | `.claude/agents/security-reviewer.md` |
| qa-reviewer | [`.github/claude/qa-rubric.md`](../../.github/claude/qa-rubric.md) | `.claude/agents/qa-reviewer.md` |
| release-judge | [`.github/claude/release-rubric.md`](../../.github/claude/release-rubric.md) | `.claude/agents/release-judge.md` |

subagent 定義は薄いラッパで、観点はすべて rubric 側に置く。**観点変更 = rubric の PR** (Routine や agent 定義をいじらない)。

## Steps

### リリース判定を回す

```
/release-gate
```

だけで良い (skill が範囲確定 → 開発レポート → judge 起動 → 集約まで面倒を見る)。deploy の実行は 🟢 でも人間。

**回すのは節目だけ**: フェーズ/マイルストーンの完了版・stg/prod への昇格・不可逆変更を含む deploy。dev への日常 auto-deploy や docs のみの変更には差し込まない (CI + PR レビュー judge の守備範囲)。🔴 が出たら作業指示リストを user 合意の上でディスパッチし、対応後に release-judge を**再起動**して再判定する。

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

- [ ] `/release-gate` で開発レポート + security / QA レポート + release-judge の verdict (🟢/🟡/🔴) が出る
- [ ] セキュリティレポートに「スキャン実行状況」表があり、回せなかったツールが UNKNOWN と明記されている
- [ ] QA レポートに受け入れマトリクスとテスト実行結果 (pass/fail、未実行は明記) がある
- [ ] qa-reviewer の作ったテストにプロダクトコードの差分が混ざっていない
- [ ] release-judge のチェックリストに UNKNOWN が出たとき、GO になっていない (デフォルト NO-GO が効いている)
- [ ] 実装セッションが自分で審査を代行していない (レポートが subagent 発か、セッションログで確認できる)

## Rollback / コスト制御

- judge を減らす: release-gate 時に qa-reviewer をスキップしたい等は skill を直すのではなく、その回のプロンプトで明示指定 (恒久変更なら ADR 0015 の見直し)
- 観点を軽くする: 各 rubric の観点を削る PR を出す
- 廃止: `.claude/agents/` と `.claude/skills/release-gate/` を消し、ADR 0015 を Deprecated にする

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

- それが [ADR 0015](../adr/0015-independent-judge-agents-security-qa-release.md) で禁止した状況そのもの。`/release-gate` を通し直す。

## Related

- 判断記録: [ADR 0015](../adr/0015-independent-judge-agents-security-qa-release.md)
- PR レビュー judge: [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) / [Runbook](claude-pr-review.md) / [`review-rubric.md`](../../.github/claude/review-rubric.md)
- テスト戦略 (L0〜L4 と QA の分担): [`docs/testing/strategy.md`](../testing/strategy.md)
- Subagents: <https://code.claude.com/docs/en/sub-agents>
