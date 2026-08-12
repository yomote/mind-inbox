# 独立 judge エージェント (code / security / QA / release) の運用

実装セッションとは**別コンテキスト・別役割**の審査役を、ループのどこで・どう走らせるかの手順。
判断記録: [ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md) (security / QA / biz-owner / release) / [ADR 0052](../adr/0052-codex-derived-review-rubric-and-stand-in-judge.md) (code-reviewer)。
旧・PR レビュー Routine ([ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) / [Runbook](claude-pr-review.md)) は**退役済み**で、技術レビューの担い手は Codex、その停止時 ([#345](https://github.com/yomote/mind-inbox/issues/345)) は `code-reviewer` subagent。

## Trigger

セキュリティレビュー / QA レビュー / リリース判定を回したい・観点を変えたい・動かない時。

## 全体像 (どこで何が走るか)

```text
PR 作成 → 技術レビュー: Codex (@codex review) / 停止時は code-reviewer [+ security-reviewer] → merge
                                                                    │ 節目
リリース PR (main → release) → /release-gate                        ▼
   ├─ 開発リリースレポート (事実の列挙のみ・自己判定なし)
   ├─→ security-reviewer / qa-reviewer / biz-owner-reviewer  (並列・新品コンテキスト)
   └─→ release-judge ← 4 レポート + CI を突合 → 🟢 / 🟡 / 🔴 + 宛先つき作業指示
                        blocker はリリース PR のスレッドへ → 未解決ならマージ不可
人間がリリース PR をマージ → stg/prod へ deploy (deploy-*.sh)
```

main への機能 PR / dev の日常 auto-deploy には差し込まない。

- **judge は必ず subagent (新品コンテキスト) で起動する** — 実装セッション自身に審査させない ([ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md))
- **各 judge の観点は rubric が正典** — 下の表から辿る。スキャナ一覧も [`security-rubric.md`](../../.github/claude/security-rubric.md) が正典

## 構成ファイル (rubric-as-truth)

共通規約 (Severity / 出力ルール) は [`.github/claude/_common.md`](../../.github/claude/_common.md)。

| 役割               | 一言 (**詳細は rubric が正典**)                                                                                                                           | 審査基準 (直すのはここ)                                           | subagent 定義                          |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------- |
| code-reviewer      | 技術レビュー (Codex の代役)。宣言と参照面の乖離・並行/再送・失敗が成功に化ける経路を、反証可能な形で 1 件ずつ。**収束を宣言する**                         | [`review-rubric.md`](../../.github/claude/review-rubric.md)       | `.claude/agents/code-reviewer.md`      |
| security-reviewer  | スキャナ総動員 + 動的チェック + 攻撃面追跡。使えなかった分は UNKNOWN 明記                                                                                 | [`security-rubric.md`](../../.github/claude/security-rubric.md)   | `.claude/agents/security-reviewer.md`  |
| qa-reviewer        | 受け入れマトリクス + 受け入れの機械検証 (**受け入れ観点の創出の所有者** — 異常系スモークの作成・実行 + 実環境 E2E の結果確認)。プロダクトコードは触らない | [`qa-rubric.md`](../../.github/claude/qa-rubric.md)               | `.claude/agents/qa-reviewer.md`        |
| biz-owner-reviewer | 実操作ウォークスルー (stub + Playwright、スクショつき) + 違和感                                                                                           | [`biz-owner-rubric.md`](../../.github/claude/biz-owner-rubric.md) | `.claude/agents/biz-owner-reviewer.md` |
| release-judge      | 4 レポート + CI を突合 → GO/NO-GO + 宛先つき作業指示 (**既定 NO-GO**)                                                                                     | [`release-rubric.md`](../../.github/claude/release-rubric.md)     | `.claude/agents/release-judge.md`      |

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

### PR の技術レビューを回す (Codex 停止時の代役)

Codex が応答できない間 ([#345](https://github.com/yomote/mind-inbox/issues/345))、`code-reviewer` subagent が技術レビューを埋める ([ADR 0052](../adr/0052-codex-derived-review-rubric-and-stand-in-judge.md))。**起動は 2 経路**: 巡回 Routine (自動 / 下記) と、PM がその場で呼ぶ手動。

共通の規律:

- 出力 (verdict + findings 表 + inline 本文) を PR に**投稿するのは呼び出し元** — judge は書かない
- diff に認証・入力検証・秘密情報・インフラ (Bicep / workflow)・依存追加が含まれる場合は、あわせて security-reviewer も起動する (code-reviewer 側はセキュリティの深掘りを委譲する規約)
- 修正 push 後は**同じ subagent を再起動して再レビュー**し、同じ指摘が再提起されないことを確認してから PM がスレッドを resolve する (CLAUDE.md の PO 決定 / 2026-08-12 改訂)
- **resolve する前に「段」を見る** ([#351](https://github.com/yomote/mind-inbox/issues/351) Phase 0 — 受け取り方の修正)。judge は finding ごとに `T1` / `T2` / `T3` を宣言する ([rubric R4-b](../../.github/claude/review-rubric.md))。**`T1` / `T2` の指摘を点修正で resolve しない** — その場合は機構 (型・ラッパ・lint・契約テスト) に落とすか、落とす Issue を分ける。実測 62 件のうち **35.5% は機構に落とせた**のに点修正で畳まれ、`createdAt` のソート汚染 ([#274](https://github.com/yomote/mind-inbox/issues/274)) は同じ型のバグが**今も 2 箇所生きている**。**T4 (一回性の点修正) は使わない**
- **Codex が復帰したら指摘者を Codex に戻す** (`REVIEW_GATE_REQUIRE_CODEX` を true のまま、[巡回 Routine を退役](#巡回-routine-を退役させる-codex-復帰時))

#### 巡回手順 (このリポジトリの正典 — Routine もこれを読んで従う)

**ここが手順の唯一の真実。** 巡回 Routine のプロンプトは「この節を読んで従え」しか書いていない ([ADR 0052](../adr/0052-codex-derived-review-rubric-and-stand-in-judge.md) D8)。手順を変えたいときは**この節を直す** — Routine 側は触らない (`subagent 定義は薄いラッパ` と同じ理由: claude.ai 側の本文は git に無く、diff に出ないので、そこに手順を持たせると壊れても気づけない)。

1. **対象を選ぶ** — open PR のうち、(a) base が `main`、(b) draft でない、(c) コード PR (`apps/` か `cicd/` に触れる)、(d) **現 head SHA に対する独立レビューがまだ無い**もの。(d) は「Codex (`chatgpt-codex-connector[bot]`) のレビューがある」か「`<!-- standin-review -->` + 現 head SHA 先頭 7 桁を含む権限保持者のコメントがある」のどちらでもない、で判定する。**古い SHA の代役レビューは無効** (push で失効する)
   - 複数あるときは**更新が古い順に最大 3 本**。残りは痕跡に「未着手 n 本」と書く (全部やろうとして途中で力尽きるより、3 本を rubric どおり書き切る)
2. **1 本ずつ `code-reviewer` subagent を起動する** (Agent tool / `subagent_type: code-reviewer`)。**自分で diff を読んでレビューを書かない** — judge を新品コンテキストの subagent に分けているのが独立性の担保そのもの。対象 PR 番号と head SHA を渡す
3. **返ってきたレポートを PR に投稿する** — サマリコメント 1 本 + 行が特定できる `blocker` / `major` の inline コメント。サマリ先頭 2 行は [`review-rubric.md`](../../.github/claude/review-rubric.md) Part 6 の**必須ヘッダ**を厳守 (これが無いと `review-gate` が独立レビューとして数えず、PR は赤のまま動かない)。SHA は**投稿直前に取り直した現 head**。レビュー中に push があったらそのレビューは古い — 投稿せず次回に回す
4. **痕跡を必ず残す** — [Issue #360](https://github.com/yomote/mind-inbox/issues/360) に先頭マーカー `<!-- pr-review-routine-tick -->` 付きコメントを 1 本。内容はレビューした PR と verdict / findings 件数 (severity 別) / 未着手の残り本数 / 異常。**対象 0 本の回も必ず書く** (「対象なし」)。異常ゼロで黙ると沈黙と正常が区別できず、`watchers.json` の監視が意味を失う。取れなかったものは `未検証: 理由` の形で書く

**やらないこと**: 実装・修正の push・マージ・auto-merge の武装・`[pm-accept]` の投稿・スレッドの **resolve** (判定は judge、操作は PM / rubric R10)・リリース PR (`main → release`) のレビュー (release-gate の担当)・設計判断。dependabot PR は rubric の C1 / C6 だけ見て短く済ませる (rubric は人が書いたコードから導出しており、依存更新 PR での振る舞いは未観測)。

**詰まったら**: subagent が起動できない / GitHub に到達できない等で回らないときは、**推測で「異常なし」と書かず** #360 に何がどこで止まったかを書いて終了する (マーカー付きコメントは必ず残す)。ツール権限で止まったなら必要なツール名も残す。

#### 巡回 Routine の登録 (登録済み / 再作成が必要なときだけ)

**登録済み** — `trig_01C4DFF8wkLnxnLeoM8QmE2b` / 6 時間毎 / 毎回新セッション。`create_trigger` MCP でエージェントが作成できたため、[#90](https://github.com/yomote/mind-inbox/issues/90) や [#156](https://github.com/yomote/mind-inbox/issues/156) と違い **web UI の手作業は不要**。生死は状況ページの Routine 行 (`watchers.json` の痕跡監視) で見る。

再作成するときの**プロンプト (貼り付け用 — これで全文)**:

```text
yomote/mind-inbox の PR レビュー巡回 (代役 judge) を実行して。
あなたは使い捨ての新品セッションで、user との対話窓口ではない。

手順は docs/runbooks/review-agents.md の
「巡回手順 (このリポジトリの正典 — Routine もこれを読んで従う)」節が正典。
まず CLAUDE.md とその節を読み、書かれているとおりに実行する。
このプロンプトに手順を再掲しない (二重管理で片方が古くなるため)。

あなたの役割は技術レビュー (judge) だけ。実装・マージ・受け入れ・resolve はしない。
```

**プロンプトを太らせないこと。** 手順を Routine 側に書くと、git に無い本文が正典になり、壊れても diff に出ない。

#### 巡回 Routine を退役させる (Codex 復帰時)

1. `delete_trigger` で `trig_01C4DFF8wkLnxnLeoM8QmE2b` を消す (または `update_trigger` で `enabled: false`)
2. `cicd/scripts/status-page/watchers.json` の `routines` から該当エントリを消す — **消さないと痕跡が止まって永久に 🔴 になる**
3. `#360` に退役した旨をコメントして close する
4. `REVIEW_GATE_REQUIRE_CODEX` は `true` のまま (門の条件は「独立レビューが 1 本」で、担い手が Codex に戻るだけ)

### 単発でレビューだけ欲しい

開発セッションで「code-reviewer で今の diff を見て」「security-reviewer で今の diff を見て」「QA 観点でレビューして」と言えば、Agent tool 経由で該当 subagent が起動する。**結果の verdict を実装セッションが値切らない**こと (blocker は直すか、直さない理由を user が明示的に引き受ける)。

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
- qa-reviewer が新規に作るのは**異常系スモーク** (実配線ハーネス `apps/frontend/e2e-uc/` / qa-rubric Q3)。
  取り込んだシナリオは以後 CI (`pnpm --dir apps/frontend test:e2e:uc`) の資産になる。
  **廃止方針の旧 L3 mock (`apps/frontend/e2e/` / `npm run test:e2e`) には取り込まない**
  ([strategy.md §6.3](../testing/strategy.md))。
- 正常系の受け入れ不足に対する QA の「実環境 E2E への hop 追加提案」は、
  恒常追加の判断 (本数を増やさない制約 / strategy.md §2.4) を人間裁定 (PO / needs-human) で通してから取り込む。

### 実装セッションが「自分でチェックしたから GO」と言う

- それが [ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md) で禁止した状況そのもの。`/release-gate` を通し直す。

## Related

- 判断記録: [ADR 0019](../adr/0019-independent-judge-agents-security-qa-release.md)
- PR レビュー judge: [ADR 0052](../adr/0052-codex-derived-review-rubric-and-stand-in-judge.md) / [`review-rubric.md`](../../.github/claude/review-rubric.md) / 導出元の実測 [`docs/reviews/`](../reviews/README.md) — 旧経路 (**退役**): [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) / [Runbook](claude-pr-review.md)
- テスト戦略 (4 層 [契約 / 単体 / スモーク / E2E] と QA の分担): [`docs/testing/strategy.md`](../testing/strategy.md)
- Subagents: <https://code.claude.com/docs/en/sub-agents>
