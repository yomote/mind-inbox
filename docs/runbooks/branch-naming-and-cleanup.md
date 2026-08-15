# ブランチ運用 — 命名 / 寿命 / 掃除

> **この文書が正典。** ブランチ戦略 (GitHub Flow) の明文化と、命名・寿命・予約名前空間・掃除条件を持つ。
> 全セッションが毎ターン読む 1 行は [`CLAUDE.md`](../../CLAUDE.md) にあり、詳細はここを引く。
> **ADR ではない** — 覆すのに移行 (データ・依存・公開面・課金) が要らない運用ルールだから ([経緯](#決定の経緯))。

## Trigger

- **PR を出す直前** — head ブランチ名がこの規約に合っているかを確認する (判定点はここ。作業中の名前は問わない)
- 子セッション / subagent の起票パケットにブランチ名を書くとき ([`/dispatch`](../../.claude/skills/dispatch/SKILL.md) Step 3-5)
- 未マージのまま PR を close するとき (head が残るので引き渡しが要る)
- 置き去りブランチを掃除するとき

## Prerequisites

- **書き込みは PR 経由のみ** — `main` はブランチ保護 + required check がかかっており直 push できない
- **この実行環境からは ref を削除できない** (2026-08-12 実測: `git push --delete` を 3 回試行し 3 回とも `send-pack: unexpected disconnect`)。**エージェントは「消す」を完遂できない**前提で手順を組む
- **squash merge のため ancestry でマージ済みを判定できない** (2026-08-12 実測: `git branch -r --merged origin/main` が 3 本しか返さない)

---

## 規約

### 1. 戦略は GitHub Flow

- `main` は常にデプロイ可能な唯一の恒久ブランチ。変更は必ず短命ブランチ + PR 経由
- `release` は GitHub Flow の外側にある**リリース PR の宛先**であり、git-flow の `develop` のような統合ブランチではない。**ここに機能を直接積まない**
- これは新しい決定ではなく、**既に行われている運用の書き起こし**

### 2. 作業ブランチ名は `<type>/<Issue 番号>-<kebab-slug>`

[Conventional Branch 1.1.0](https://github.com/conventional-branch/conventional-branch) の `<type>/<description>` に従い、`<description>` の先頭に Issue 番号を置く。

**type は下表で閉じる。「など」で開かない** — 開いた集合にすると検証条件 (下の完全パターン) が書けず、`ci/` `tooling/` `ops/` `pm/` のような一回きりの type が増え続ける (実測: 既存に各 1 本ある)。

| type | 使う場面 | Issue 番号 |
| --- | --- | --- |
| `claude/` | Claude Code が実装 (既定) | **必須** |
| `codex/` | Codex が実装 | **必須** |
| `feature/` `feat/` `fix/` `bugfix/` `hotfix/` | 人間 (PO) が手で切る | **必須** |
| `chore/` `docs/` `test/` | 3 の例外 (Issue を立てない作業) | 不要 |
| `dependabot/` | Dependabot が生成 (名前を変えられない) | 対象外 |

- `claude` `codex` `feature` `feat` `fix` `bugfix` `hotfix` `chore` は Conventional Branch 1.1.0 の正式 type。**`docs` `test` は仕様に無い本リポジトリの追加**で、既存の `docs/` `test/` に合わせた。**既存の `ci/` `tooling/` `ops/` `pm/` は今後使わず `chore/` に寄せる**
- **description**: kebab-case、英小文字 (`a-z`) / 数字 / ハイフンのみ。連続ハイフン・先頭末尾ハイフン禁止 (仕様の Naming Rules に準拠)。目安 2〜5 語
- **Issue 番号は必須** — `claude/262-role-assignment-fix`。仕様の例は `feature/issue-123-add-oauth` と `issue-` を付けるが、**本リポジトリは `issue-` を省く** (既存の多数派に合わせる。Naming Rules に反する逸脱ではなく Description Style の例からの逸脱)
- **同じ Issue に複数の作業ブランチが立つのは正常** (逐次修正 / 複数 PR)。slug で区別する。**ブランチ名は「索引」であって「排他」ではない** (slug が違えば別 ref になるので、名前では排他できない)
- **判定点は「PR を出す時点」** — 作業中の一時的な名前 (Claude Code on the web の自動生成名 `claude/<slug>-<random>` など) は問わない。PR の head branch が規約名であればよい。切り直しは 1 行で済む:

  ```bash
  git push origin HEAD:claude/<Issue 番号>-<slug>   # 新しい名前で push し直し、その ref から PR を出す
  ```

### 3. Issue の無い作業は「先に Issue を立てる」。例外は用途 type + 番号なし

- **原則**: 作業の前に Issue を立てる。「実行状態の真実は GitHub Issues」「open Issue にはちょうど 1 個の `stream:*` ラベル」(CLAUDE.md) の前提で、起票 30 秒で真実が揃う
- **例外**: Issue を立てるほどでない作業 (typo 修正 / 依存更新 / 実験ブランチ) は `chore/<slug>` `docs/<slug>` `test/<slug>` の**3 つに限り、番号なし**を許す (2 の表と同じ集合。**「など」で開かない**)。`dependabot/*` は生成名をそのまま使う
- **したがって `claude/` `codex/` の prefix は「Issue 番号を持つ」ことのしるしになる** — 「`^(claude|codex)/[0-9]+-` に合致しない head が来たら規約違反」という 1 行の機械判定が後から書ける (7)
- 例外を選ぶかの判断: **「後からこの変更の理由を Issue で追う必要があるか」**。あるなら Issue を立てる

### 4. 予約名前空間 — 作業ブランチを切ってはいけない

| ref | 何が読み書きするか | 状態 |
| --- | --- | --- |
| `main` / `release` / `gh-pages` | 保護 / リリース PR の宛先 / status ページ | 現役 |
| `data/*` | 機械が append するデータブランチ。`data/ux-observations` (UX 観測) / `data/github-settings` (設定スナップショット) を 4 つの workflow と `cicd/scripts/{ux-data,ux-eval,ux-probe,status-page,github-settings}/` が読み書きする | 現役 |
| `claim/*` | 着工ロック (`refs/heads/claim/<Issue 番号>` への CAS push) 用に**予約だけしてある** | **実装も運用も無い** — 2026-08-15 実測で origin に 0 件、`cicd/` `.github/` `.claude/` に触るコードも無い ([`docs/team.md`](../team.md))。設計は [archive の記録](../adr/archive/operations/pm-self-driving-mode.md) (現行ルールではない) |

`claim/*` を予約したままにしているのは、**ロックを専用名前空間に隔離できないため**。2026-08-12 実測で **`refs/heads` の外 (`refs/claim/*` 等) には push できなかった**ので、着工ロックを実装するなら `refs/heads/claim/*` に置くしかなく、人間の作業ブランチと同じ名前空間を共有する。分離できるのは名前だけなので、名前だけ先に空けてある。

### 5. 寿命は 3 営業日目安、PR の決着と同時に削除する

- **目標寿命 3 営業日** — Trunk Based Development は短命ブランチを「hours 〜 a couple of days」とし、2 日を超えると long-lived branch 化のリスクとする。日次 tick で回している運用に合わせて tick 3 回分を上限に置いた。**超えたら「分割するか close する」を検討する合図**であり、赤にはしない。**この数字にこのリポジトリでの実測の裏付けは無い** (初期値。運用してから見直す)
- **削除 (merged)**: PR が merged になった時点で head を削除する。GitHub の "Automatically delete head branches" (`delete_branch_on_merge`) で機構化できるが、**現在値は未取得** — 管理系 API が 403 で読めていない ([`cicd/github/terraform/unmanaged.tf`](../../cicd/github/terraform/unmanaged.tf) に「未取得」として記録済み)。**有効になっている前提で書かないこと**
- **削除 (未マージ close)**: **上の設定は merge 時にしか働かない。** 未マージ close した PR の head は残り、かつ**エージェントは ref を削除できない** (Prerequisites)。したがって:
  - **エージェントが未マージ PR を close するときは、head 削除の `needs-human` Issue を立ててから close する。引き渡しを作らずに close しない**
  - 人間が close する場合は、同じ操作の中で head を削除する
  - **恒久策** (「closed かつ head が残っている PR」を検知して削除する Action) は [#343](https://github.com/yomote/mind-inbox/issues/343)。それが入るまで、この規則は**機構ではなく規律**
- **判定基準は ancestry ではなく PR の状態** (Prerequisites の squash merge 実測)

### 6. 適用境界 — 既存ブランチは一括改名しない

- **境界は「ブランチをいつ切ったか」ではなく「PR をいつ作ったか」** — 拘束するのは PR の head 名であってブランチの出自ではない。**古いブランチでも、これから PR を出すなら規約名で出す** (2 の 1 行 push)。`git ls-remote` は**作成日時を返さない**ので、境界をブランチ側に置くとそもそも検証できない (PR の `created_at` は API が返す)
- **PR を持たない既存ブランチを遡って改名することは求めない** — 改名は「新 ref 作成 + 旧 ref 削除」であり、**削除できない実測**がある。削除できないまま新 ref だけ増やすと本数が倍になる
- 既に open な PR の head 名も変えない

### 7. 当面 CI で強制しない。機構化の条件を先に決めておく

- **現在ブランチ名を解釈する自動化は 1 つも無い** (2026-08-12 実測: `.github/workflows/` の 16 workflow に branch 名を検査するものはなく、`branches: [main]` のトリガ指定と merge queue の一時 branch から PR を解決する処理のみ)。壊れるものが無いところに required check を足すと「門」の意味が薄まる
- **機構化する条件**: 「同じ Issue に対する並行本数を機械で数える」必要が出たとき (WIP 上限の自動判定を作るとき、または [#175](https://github.com/yomote/mind-inbox/issues/175) 型の並行衝突検知をブランチ側に広げるとき)。そのとき review-gate に `head_ref` の形式判定を 1 つ足す
- **これはこのリポジトリの経験則 (「規律は破られ、機構は守られる」) に反する意図的なトレードオフ。** 受け入れる理由は、破られたときの損害が索引の欠落だけで `main` の履歴も自動化も壊れないこと。**予約名前空間 (4) だけは損害の質が違う** — `data/*` を踏むと 4 つの workflow が読むデータが壊れるので、ここは CLAUDE.md の 1 行で毎ターン当てている

---

## Steps

### A. PR を出す前に名前を確認する

```bash
git rev-parse --abbrev-ref HEAD
```

規約名でなければ切り直してから PR を出す:

```bash
git push origin HEAD:claude/<Issue 番号>-<slug>
```

旧 ref は削除できないので残る。**それでよい** (6 の適用境界)。

### B. 未マージのまま PR を close する

1. head ブランチ削除の `needs-human` Issue を立てる (対象 PR 番号と ref 名を書く)
2. その Issue 番号を close コメントに残す
3. close する

**1 を飛ばして close しない** — 誰も消せない ref が静かに増える。

### C. 置き去りブランチを掃除候補として一覧化する

次を**すべて**満たすものが候補:

- (a) 4 の予約名前空間でない
- (b) **対応する open PR が無い**
- (c) **tip が 30 日以上動いていない** (再開されうる直近の作業を巻き込まない側に倒す)
- (d) ブランチ名が Issue 番号を持つ場合、**その Issue が closed である** (open なら再開余地があるので外す)

**一覧化までは機械が行い、削除そのものは人間 (web UI) か GitHub Actions が行う** (エージェントは ref を削除できない)。恒久経路は [#343](https://github.com/yomote/mind-inbox/issues/343)。

## Verification

- [ ] **この文書の着地後に作成された PR** の head 名が、下の**完全パターン**のいずれかに合致する。**どれにも合致しないものが 0 本**であること

  ```text
  ^(claude|codex|feature|feat|fix|bugfix|hotfix)/[0-9]+(-[a-z0-9]+)+$   # 2: Issue 番号必須
  ^(chore|docs|test)/[a-z0-9]+(-[a-z0-9]+)*$                            # 3: 番号なし例外
  ^dependabot/                                                          # 生成名を変えられない (形式は問わない)
  ```

  `[a-z0-9]+` をハイフンで連結する形にしているのは、Naming Rules (小文字のみ / 連続ハイフン禁止 / 先頭末尾ハイフン禁止) を**パターン自体で表現する**ため。**部分一致の `^(claude|codex)/[0-9]+-` を指標に使わない** — prefix と番号の有無しか見ておらず、`claude/123-FOO` (大文字) / `chore/a--b` (連続ハイフン) / `feature/foo` (番号なし) を合格に数えてしまう
  - **観測対象を「ブランチ」ではなく「PR の head」にしたのは、新規かどうかを再現可能に判定するため** (6 の理由と同じ)
- [ ] `claim/*` `data/*` に作業ブランチが切られていない
- [ ] **merged** な PR の head が残っていない (`delete_branch_on_merge` の有効化後。現在値は未取得)
- [ ] **未マージ close** で head が残っている PR の**それぞれに削除の `needs-human` 引き渡しが存在する**。件数の増減ではなく**引き渡しの有無**で判定する — 件数条件では個々の head が消えなくても満たせてしまう
- [ ] `git ls-remote --heads origin` の総数が減少に転じる ([#343](https://github.com/yomote/mind-inbox/issues/343) の完了後)

### 実測

| 項目 | 2026-08-12 | 2026-08-15 |
| --- | --- | --- |
| origin のブランチ総数 | 131 | **160** |
| `claude/` の本数 | 107 | 135 |
| うち `claude/<番号>-` / `claude/issue-<番号>-` / 番号なし | 16 / 5 / 86 | 20 / 14 / 101 |
| open PR | 11 | 6 (うち 4 本が `dependabot/`。**残り 2 本の head は規約名ではない**) |
| 長命 (`main` `release` `gh-pages` `data/*`) | 5 (`claim/999` 含む) | 5 (`data/*` が 2 本 / `claim/*` は **0 件**) |
| open PR も持たず機械も読まない | 115 | **149** |
| `git branch -r --merged origin/main` | 3 | (未再測) |
| 最古の tip | 2026-05-06 (3 か月超) | (未再測) |

**2026-08-15 の再測は `git ls-remote --heads origin` と open PR 一覧から機械的に出した。** 「未再測」の 2 行はこの再配置 PR では取り直していない (2026-08-12 の値をそのまま持ち越さないために空欄にしてある)。

## Rollback

規約に合わない名前で PR を出してしまった場合、**PR を作り直す必要は無い** — 7 のとおり CI では強制しておらず、名前は索引でしかない。次の PR から規約名にすればよい。切り直す場合は A の 1 行 push で新しい ref から PR を出し直す (旧 ref は残る)。

## Common Issues

### Claude Code on the web が付けた `claude/<slug>-<random>` のまま PR を出してしまう

- 原因: 作業開始時点では Issue 番号が決まっていない / セッションが自動生成名で始まる
- 対処: PR を出す直前に A を踏む。**これが一番よく破られる箇所**なので、起票パケット ([`/dispatch`](../../.claude/skills/dispatch/SKILL.md) Step 3-5) でブランチ名を名指しする

### 同じ Issue にブランチが 3 本立っている — 二重着工か?

- 原因: **逐次修正でも複数ブランチが立つ** (実例: #262 は 5 本の修正 PR を要した)。名前からは区別できない
- 対処: **ブランチ名で排他を判定しない** (2)。open PR とその head を見て、同じファイルを触っているかで判断する。機械で数えたくなったら 7 の機構化条件

### `ci/` `tooling/` `ops/` `pm/` のブランチがある

- 原因: type 表が閉じる前に切られたもの (各 1 本)
- 対処: 今後は `chore/` に寄せる。既存は 6 のとおり改名しない

---

## 決定の経緯

2026-08-12、PO の「ベストプラクティスがあればそれで決めて残して」という依頼で調査したところ、**ブランチ戦略も命名規約もリポジトリのどこにも文書化されていない**ことが分かった ([#341](https://github.com/yomote/mind-inbox/issues/341))。戦略そのものは既に GitHub Flow と一致していたため、決めるべきは戦略の変更ではなく、**書かれていないせいで揃っていない命名・寿命・名前空間**だった。内容は当初 ADR 0049 として起案され、[PR #342](https://github.com/yomote/mind-inbox/pull/342) 上で Codex との 8 スレッドの往復 (2026-08-12〜14) で鍛えられた — type 集合を閉じたこと、検証条件を部分一致でなく完全パターンにしたこと、適用境界を「ブランチの新旧」から「PR の作成時点」に移したこと、未マージ close の自己矛盾を [#343](https://github.com/yomote/mind-inbox/issues/343) に切り出したことは、いずれもこの往復の産物である。

**2026-08-15、PO が「これは ADR ではない」と分類を裁定した。** [#385](https://github.com/yomote/mind-inbox/pull/385) の「運用・プロセスの決め事は ADR ではない」と、[#395](https://github.com/yomote/mind-inbox/pull/395) で入った判定基準「覆すのに移行 (データ・依存・公開面・課金) が要るか」に照らすと、ブランチ命名は覆しても移行が要らない = 運用ルールである。旧 0054 (開発設備の運用判断) の受容条件を Runbook へ移した 2026-08-14 の裁定と同じ形で、**中身は保全したまま棚だけ移した**。**ADR 番号 0049 は使用せず、[`docs/adr/archive/retired-numbers.txt`](../adr/archive/retired-numbers.txt) に載せて再利用を止めてある** (PR #342 とその 8 スレッドが「ADR 0049」の名で残っているため、別の判断に振り直すと過去の議論が読めなくなる)。

### 採らなかった案

- **現状維持 (暗黙のまま)** — 決めるコストはゼロだが、4 つの静かな破損 (並行本数を数えられない / 予約名前空間が無防備 / 掃除基準が作れない / Issue 番号を持たないブランチが Issue から辿れない) がそのまま続く
- **git-flow 型の重い分岐モデル** (`develop` + `release/*` `hotfix/*` を恒久分岐に) — `main` → dev 自動デプロイ + リリース PR で既にデプロイが回っており、`develop` を足すと required check の門を通る回数が二重になるだけ。並行保守すべき出荷済みバージョンも存在しない (dev 環境 1 面のみ)
- **命名規約を自前で新規定義する** — 方言の保守コストが乗る。Conventional Branch 1.1.0 が `claude/` `codex/` を正式 type として持ち、ticket 番号を description に入れる形も定義済みなので、固有事情 (Issue 番号必須 / 予約名前空間) は**上乗せ**で表現できる

### 一次ソース (すべて 2026-08-12 取得)

| ソース | 採った内容 | 取得状況 |
| --- | --- | --- |
| GitHub Flow (GitHub 公式) <https://docs.github.com/en/get-started/using-github/github-flow> | 「短く説明的なブランチ名が進行中の作業を一目で見せる」/「PR をマージしたらブランチを削除する」— 1 / 5 の根拠 | **WebSearch 経由の引用のみ。原文ページは未取得** (`docs.github.com` への直接 fetch が egress 制限でブロック) |
| Trunk Based Development, Short-Lived Feature Branches <https://trunkbaseddevelopment.com/short-lived-feature-branches/> | 短命ブランチの寿命は「a couple of days」、2 日超で long-lived 化のリスク — 5 の 3 営業日の根拠 | **WebSearch 経由の引用のみ。原文ページは未取得** |
| Conventional Branch 1.1.0 <https://github.com/conventional-branch/conventional-branch> | `<type>/<description>` 形式 / type 一覧 (AI エージェント prefix に **`claude`** `codex` を含む) / Naming Rules / Description Style (kebab-case, 2〜5 語, ticket 番号に言及があれば含める — 例 `feature/issue-123-add-oauth`) — 2 / 3 の根拠 | **直接取得済み** (`raw.githubusercontent.com` の `README.md` と `skills/conventional-branch/SKILL.md`) |
| Git `gitglossary` <https://git-scm.com/docs/gitglossary> | 「ref namespace は階層で、`refs/heads/` 階層がローカルブランチを表す」— 4 の名前空間論の根拠 | **WebSearch 経由の引用のみ。原文ページは未取得** |
| Git `git-check-ref-format` <https://git-scm.com/docs/git-check-ref-format> | ref 名は `/` で階層化できる — 2 の形式が Git の制約と矛盾しないことの確認 | **WebSearch 経由の引用のみ。原文ページは未取得** |

**上表の「WebSearch 経由」4 件は逐語引用として扱わないこと。** 検索エンジンが返した引用文に依拠しており、原文ページを開いての一字一句の確認はできていない。数値 (「a couple of days」) と方針 (「マージしたら削除」) の粒度では確かだが、それ以上の精度で引かない。

## Related

- [`CLAUDE.md`](../../CLAUDE.md) — 全セッションが毎ターン読む 1 行 (予約名前空間 + PR の head 名)
- [`/dispatch` skill](../../.claude/skills/dispatch/SKILL.md) — 起票パケットにブランチ名を書くとき
- [`github-settings.md`](github-settings.md) — ブランチ保護の宣言と点検 / [`merge-queue.md`](merge-queue.md) — マージの門 (review-gate)
- [`docs/team.md`](../team.md) — `claim/*` が「設計だけで実装も運用も無い」ことの実測
- [`status-page.md`](status-page.md) / [`ux-probe-judge.md`](ux-probe-judge.md) — `data/*` を読み書きする自動化
- Issue: [#341](https://github.com/yomote/mind-inbox/issues/341) (起点) / [#343](https://github.com/yomote/mind-inbox/issues/343) (未マージ close の head 削除経路) / [#175](https://github.com/yomote/mind-inbox/issues/175) (並行を機械で数えられない)
