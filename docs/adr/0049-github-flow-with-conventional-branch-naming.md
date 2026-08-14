# 0049. ブランチ戦略は GitHub Flow と明文化し、命名は Conventional Branch 準拠 + Issue 番号必須にする

- Status: Proposed
- Date: 2026-08-12
- Deciders: yomote (PO — 「ベストプラクティスがあればそれで決めて ADR に残して」/ 裁定は次回 debrief) / PM セッション
- Related: [ADR 0011](archive/operations/github-projects-as-execution-dashboard.md) (実行状態の真実は GitHub) / [ADR 0021](archive/operations/parent-session-as-pm-orchestrator.md) (hub-and-spoke) / [ADR 0033](archive/operations/parent-implements-via-subagent-when-child-sessions-are-gated.md) (分配基準) / [ADR 0036](archive/operations/merge-gate-as-required-check-and-pm-cadence.md) (マージの門 / WIP 上限 2) / [ADR 0041](archive/operations/ux-observations-on-git-data-branch.md) (`data/ux-observations` — 機械が読むブランチ) / ADR 0043 (**`main` に未収録** — PR [#284](https://github.com/yomote/mind-inbox/pull/284)) (D5 の `claim/<Issue 番号>` 着工ロック) / [ADR 0044](archive/operations/stream-lanes-as-the-project-map.md) (地図)

Technical Story: 2026-08-12 の PO 依頼。ブランチ戦略・命名規約がリポジトリのどこにも文書化されていないことが判明した (`grep` で確認 — ヒットしたのは ADR 0033 と CLAUDE.md の「セッション名」の話で、ブランチではない)。関連: [#175](https://github.com/yomote/mind-inbox/issues/175) (並行する作業を機械で数えられない、と同型の問題)。

## Context and Problem Statement

**戦略そのものは既に GitHub Flow と一致している** — main 1 本 / 短命ブランチ / PR / squash merge / ブランチ保護 + required check (ADR 0036) / リリースは `main → release` の PR (ADR 0019)。決めるべきは戦略の変更ではなく、**書かれていないせいで揃っていない命名と寿命**である。

### 実測 (2026-08-12, `git ls-remote --heads origin`)

| 項目                                                    | 実測                                                                                                                                               |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| origin のブランチ総数                                   | **131 本**                                                                                                                                         |
| prefix 別                                               | `claude/` 107 / `test/` 6 / `dependabot/` 5 / `docs/` 3 / `chore/` 2 / `tooling/` `ci/` `ops/` `data/` `claim/` 各 1 / `main` `release` `gh-pages` |
| `claude/` 107 本の形                                    | `claude/<Issue 番号>-<slug>` **16** / `claude/issue-<Issue 番号>-<slug>` **5** / **Issue 番号を持たない 86**                                       |
| open PR                                                 | 11 本                                                                                                                                              |
| 長命ブランチ (main / release / gh-pages / data / claim) | 5 本                                                                                                                                               |
| **PR も持たず機械も読まない置き去り**                   | **115 本**                                                                                                                                         |
| 最古のブランチ tip                                      | 2026-05-06 (`tooling/pr-readiness` / `docs/claude-md-update-rules` / `ci/lint-typecheck-build`) — 3 か月超                                         |
| 7 日以上動いていない / 30 日以上                        | 26 本 / 22 本                                                                                                                                      |
| `git branch -r --merged origin/main` が返す本数         | **3 本** (squash merge のため ancestry ではマージ済みを判定できない)                                                                               |

同じ Issue に複数ブランチが立っている実例: **#262 に 3 本** (`claude/262-deploy-robust` / `claude/262-role-assignment-fix` / `claude/262-role-assignment-idempotent`) + 番号を持たない `claude/fix-deploy-role-assignment-lookup`、**#187 に 2 本**、**#86 に 2 本**。

### 命名が揃っていないと何が静かに壊れるか

1. **「逐次修正」と「二重着工」がブランチ名から区別できない** — #262 は実際には 5 本の逐次修正 PR (#273 / #278 / #279 / #290 / #292) を要した案件で、複数ブランチが立つこと自体は正常運転である。問題は、名前を見ても「1 つの Issue を 3 手で直している」のか「3 セッションが同じ仕事を重複着工している」のか判別できないこと。ADR 0036 D5 / ADR 0043 D3 の **WIP 上限 2 は「実装ストリームの本数」を数える規律だが、数える対象を名前から機械的に取り出せない**。#175 (open PR 間の ADR 採番衝突を数えられない) と同じ構造で、規律の側だけが用意されていて計数手段が無い
2. **予約名前空間との衝突が防がれていない** — `data/ux-observations` (ADR 0041 / `cicd/scripts/ux-data/append.py` などが append する)、`gh-pages` (status ページ)、`release` (リリース先)、`claim/<Issue 番号>` (ADR 0043 D5 の着工ロック) は**機械が読み書きする ref** である。ADR 0043 の CAS ロックは `refs/heads` の下にしか置けない (下記の実測) ため、**ロック用 ref と人間の作業ブランチが同じ名前空間を共有している**。分離できるのは名前だけなのに、その名前の規約が無い
3. **掃除の基準が作れない** — squash merge のため ancestry 判定が効かず (`--merged` が 3 本しか返さない)、「消してよいブランチ」を機械で選ぶには対応する PR / Issue を引く必要がある。今は 115 本が判定不能のまま残り、`git ls-remote` の出力が実質ノイズになっている
4. **Issue 番号を持たない 86 本は Issue から辿れない** — 対応関係が PR 経由でしか復元できず、PR が閉じたあとは追跡不能。「実行状態の真実は GitHub Issues」(ADR 0011) の可視面が 1 つ欠けている

### この環境で ref に対して何ができるか (親セッション実測 / 2026-08-12)

ADR 0043 D5 の前提と、本 ADR の移行方針の前提になるため記録する。

- 同一 ref への後発 push は non-fast-forward で拒否される (CAS が成立する)
- `--force-with-lease` は expect した SHA が一致したときだけ成功する (奪取も CAS を保つ)
- **`refs/heads` の外 (`refs/claim/*` 等) には push できない** — ロックを専用名前空間に隔離できず、`refs/heads/claim/*` に置くしかない
- **この実行環境からは ref の削除ができなかった** (3 回試行して全失敗) — エージェントが「消す」を完遂できる保証が無い

## Decision Drivers

- **既存のベストプラクティスに乗る** — 方言を自作しない (保守対象を増やさない)
- **機構で守れる形にする** — 「規律は破られ、機構は守られる」(ADR 0036)。ただし今回は**機構を先に作るのではなく、後から機構化できる形に規約を切る** (下の Negative でトレードオフを明記)
- **ADR 0043 D5 の claim ロックと名前空間を衝突させない** — ロックの成立条件そのもの
- **移行コストをゼロで始められること** — 既存 131 本を人力で触らせない (削除できない実測がある)
- **エージェントの自動生成名と戦わない** — Claude Code on the web が付ける `claude/<slug>-<random>` を人が毎回直す運用は規律で守れない (ADR 0044 D1「名前の重力に勝とうとしない」)

## Considered Options

- Option A: 現状維持 (暗黙のまま)
- Option B: **GitHub Flow を明文化 + Conventional Branch 1.1.0 準拠の命名規約 + Issue 番号必須** (採用)
- Option C: git-flow 型の重い分岐モデル (`develop` / `release/*` / `hotfix/*` を恒久分岐として持つ)
- Option D: 命名規約を自前で新規定義する (既存標準に乗らない)

## Decision Outcome

Chosen option: **"Option B"**。戦略は既に GitHub Flow なので変えず、欠けている**命名・寿命・名前空間**だけを既存標準に合わせて足す。Conventional Branch 1.1.0 は `claude/` `codex/` を正式な type として定義しており、このリポジトリの実態 (実装 Claude / レビュー Codex — ADR 0035) にそのまま当たるため、方言を作る理由が無い。

### D1 — 戦略は GitHub Flow である、と明文化する

- `main` は常にデプロイ可能な唯一の恒久ブランチ。変更は必ず短命ブランチ + PR 経由 (直 push はブランチ保護が拒否 — ADR 0036 D2)
- `release` は GitHub Flow の外側にある**リリース PR の宛先** (ADR 0019) であり、git-flow の `develop` のような統合ブランチではない。ここに機能を直接積まない
- 本項は新規の判断ではなく、**既に行われている運用の明文化**である (ADR は不変記録であり、暗黙の前提を後から書き起こすのは正当な用途)

### D2 — 作業ブランチ名は `claude/<Issue 番号>-<kebab-slug>` (Issue 番号は必須)

Conventional Branch 1.1.0 の `<type>/<description>` に従い、`<description>` の先頭に Issue 番号を置く。

- **type は下表で閉じる。「など」で開かない** — 開いた集合にすると検証条件が書けず、`ci/` `tooling/` `ops/` のような一回きりの type が増え続ける (実測: 既存にこの 3 つが各 1 本ある)

  | type | 使う場面 | Issue 番号 |
  | --- | --- | --- |
  | `claude/` | Claude Code が実装 (既定) | **必須** |
  | `codex/` | Codex が実装 | **必須** |
  | `feature/` `feat/` `fix/` `bugfix/` `hotfix/` | 人間 (PO) が手で切る | **必須** |
  | `chore/` `docs/` `test/` | D3 の例外 (Issue を立てない作業) | 不要 |
  | `dependabot/` | Dependabot が生成 (名前を変えられない) | 対象外 |

  `claude` `codex` `feature` `feat` `fix` `bugfix` `hotfix` `chore` は Conventional Branch 1.1.0 の正式 type。**`docs` `test` は仕様に無い本リポジトリの追加**で、既存の `docs/` 3 本 `test/` 6 本に合わせた (仕様の `chore/` に寄せる案もあるが、既に定着している方を採る)。**既存の `ci/` `tooling/` `ops/` は今後使わず `chore/` に寄せる** (既存ブランチは D6 で適用外)
- **description**: kebab-case、英小文字 (`a-z`) / 数字 / ハイフンのみ。連続ハイフン・先頭末尾ハイフン禁止 (仕様の Naming Rules に準拠)。目安 2〜5 語
- **Issue 番号は必須** — `claude/262-role-assignment-fix`。既存で最多の形 (16 本) をそのまま採る。仕様の例は `feature/issue-123-add-oauth` と `issue-` を付けるが、**本リポジトリは `issue-` を省く** (既存 16 本 : 5 本の多数派に合わせる。仕様の Rules に反する逸脱ではなく、Description Style の例からの逸脱)
- **同じ Issue に複数の作業ブランチが立つのは正常** (逐次修正 / 複数 PR)。slug で区別する。排他は D4 の claim ref が担う
- **判定点は「PR を出す時点」** — 作業中の一時的な名前 (Claude Code on the web の自動生成名など) は問わない。PR の head branch が規約名であればよい。切り直しは `git push origin HEAD:claude/<N>-<slug>` の 1 行で済む。門を PR 境界に置くのは、review-gate が既にそこにいるため (ADR 0036)

### D3 — Issue の無い作業は「先に Issue を立てる」。例外は用途 type + 番号なし

- **原則**: 作業の前に Issue を立てる。ADR 0011 (実行状態の真実は Issues) と ADR 0044 (`stream:*` ラベルで地図に載る) の前提であり、起票 30 秒で真実が揃う
- **例外**: Issue を立てるほどでない作業 (typo 修正 / 依存更新 / 実験ブランチ) は `chore/<slug>` `docs/<slug>` `test/<slug>` の**3 つに限り、番号なし**を許す (D2 の表と同じ集合。**「など」で開かない**)。`dependabot/*` は Dependabot が生成する名前をそのまま使う (このリポジトリ側で変えられない)
- **したがって `claude/` `codex/` の prefix は「Issue 番号を持つ」ことのしるしになる** — `^(claude|codex)/[0-9]+-` に合致しないブランチが PR の head に来たら規約違反、という 1 行の機械判定が後から書ける (D7)
- 例外を選ぶかどうかの判断: 「後からこの変更の理由を Issue で追う必要があるか」。あるなら Issue を立てる

### D4 — `claim/*` は着工ロック専用の予約名前空間。作業ブランチ名は「索引」であって「ロック」ではない

ADR 0043 D5 との接続を明文化する。

- **予約名前空間** (作業ブランチを切ってはいけない): `claim/*` (着工ロック / ADR 0043 D5) / `data/*` (機械が append するデータブランチ / ADR 0041) / `gh-pages` (status ページ) / `release` / `main`
- **役割の分離**: **排他 = `refs/heads/claim/<Issue 番号>` の CAS** (ADR 0043 D5)、**索引 = 作業ブランチ名の Issue 番号** (本 ADR D2)。ADR 0043 D5 は「作業ブランチ名は排他の役割を持たない」と明記しており、本 ADR はこれを追認したうえで、空いていた役割 (索引) を作業ブランチ名に与える
- ロックが `refs/heads` の下に同居せざるを得ないのは、この環境から `refs/heads` の外へ push できないという実測による (上記)。**したがって `claim/` の予約は ADR 0043 の成立条件であり、本 ADR の飾りではない**
- claim ref の解放・失効・奪取の手順は ADR 0043 D5 が正典。本 ADR は上書きしない
- `claim/999` (実測で origin に存在) は 0043 の検証で作られたもの。掃除は D6 の対象

### D5 — 寿命は「開いてから 3 営業日以内に merge / close」、PR の決着と同時に削除する

- **目標寿命 3 営業日** — Trunk Based Development は短命ブランチを「hours 〜 a couple of days」とし、2 日を超えると long-lived branch 化のリスクとする。このリポジトリは日次 tick (ADR 0043 D4) で運転しているため tick 3 回分を上限に置く。**超えたら「分割するか close する」を検討する合図**であり、赤にはしない
- **削除 (merged)**: PR が merged になった時点で head ブランチを削除する。GitHub の "Automatically delete head branches" (`delete_branch_on_merge`) を有効化して機構化する — **web UI 操作なので `needs-human` Issue に積む** (ADR 0020)。有効化されるまでは手動
- **削除 (未マージ close)**: **この設定は merge 時にしか働かない。** 未マージのまま close した PR の head は残り続ける。かつ**エージェントセッションからは ref の削除ができない実測がある** (2026-08-12 / `git push --delete` が 3 回とも失敗) ため、**「close した本人が消す」は close 主体がエージェントのとき成立しない**。したがって当面の規則を次の 1 つに固定する:
  - **エージェントが未マージ PR を close するときは、head ブランチ削除の `needs-human` Issue を立ててから close する** (ADR 0020 の宿題キュー)。**引き渡しを作らずに close しない**
  - 人間が close する場合は、同じ操作の中で head を削除する
  - **恒久策**(「closed かつ head が残っている PR」を検知して削除する Action を必須経路にする) は [#343](https://github.com/yomote/mind-inbox/issues/343) に切り出す。それが入るまで、この規則は**機構ではなく規律**である (D7 と同じトレードオフ)
- **判定基準は ancestry ではなく PR の状態** — squash merge で `--merged` が使えない実測 (3 本しか返らない) があるため、掃除の条件は「対応する PR が merged / closed」とする
- **対応する PR を持たないブランチには別条件を置く** — 置き去り 115 本の大半がこれで、上の条件では**永久に選べない**。次をすべて満たすものを掃除候補とする: (a) D4 の予約名前空間でない (b) **対応する open PR が無い** (c) **tip が 30 日以上動いていない** (実測: 30 日超 22 本 / 7 日超 26 本。再開されうる直近の作業を巻き込まない側に倒す) (d) ブランチ名が Issue 番号を持つ場合、**その Issue が closed である** (open なら再開余地があるので候補から外す)。**候補の一覧化までは機械が行い、削除そのものは [#343](https://github.com/yomote/mind-inbox/issues/343) の経路に載せる**
- 長命を許すブランチは D4 の予約名前空間のみ

### D6 — 既存 131 本は一括改名しない。規約は本 ADR マージ後に**作成された PR** から適用する

- **適用境界は「ブランチをいつ切ったか」ではなく「PR をいつ作ったか」** — D2 が拘束するのは PR の head 名であり (D2 の判定点)、ブランチの出自ではない。**マージ前から存在するブランチでも、マージ後に PR を出すなら規約名で出す** (`git push origin HEAD:claude/<N>-<slug>` の 1 行で済む)。ブランチの新旧は `git ls-remote` から判定できない (作成日時を返さない) ため、境界をブランチ側に置くとそもそも検証できない
- **一括改名しない** — 上記は「PR を出すとき」の話であり、**PR を持たない既存ブランチを遡って改名することは求めない**。改名は「新 ref 作成 + 旧 ref 削除」であり、**この環境から ref を削除できなかった実測** (3 回全失敗) がある。削除できないまま新 ref だけ増やすと本数が倍になる
- 既存の open PR 11 本の head ブランチ名も変えない
- **置き去り 115 本の掃除は本 ADR の決定に含めない** — 別 Issue に切り出す。実施主体は人間 (web UI) または GitHub Actions (ADR 0031「サンドボックスの外にある事実は Actions 経由で取る」と同じ経路)。掃除の条件は D5 のとおり (「対応する PR が merged / closed」**または**「対応する open PR が無く 30 日以上動いておらず、Issue 番号を持つならその Issue が closed」。いずれも予約名前空間でないこと)
- 移行はしないが**計数は今日からできる** — 規約が根付いているかは「動作検証」の条件 1 (マージ後に作成された PR の head が許可パターンに合致する割合) で観測する。**部分一致の `^(claude|codex)/[0-9]+-` を指標に使わない** — prefix と番号の有無しか見ておらず、`claude/123-FOO` のような規約違反を合格に数えてしまう

### D7 — 当面は CI で強制しない。機構化の条件を先に決めておく

- **現在ブランチ名を解釈する自動化は 1 つも無い** (実測: `.github/workflows/` の 16 workflow に branch 名を検査するものはなく、`branches: [main]` のトリガ指定と、ADR 0042 の merge queue 一時 branch から PR を解決する処理のみ)。壊れるものが無いところに required check を足すと、ADR 0036 で作った「門」の意味が薄まる
- **機構化する条件**: 「同じ Issue に対する並行本数を機械で数える」必要が出たとき — すなわち WIP 上限 2 (ADR 0036 D5 / ADR 0043 D3) の自動判定を作るとき、または #175 型の並行衝突検知をブランチ側にも広げるとき。そのときに review-gate へ 1 チェック (`head_ref` の形式判定) を足す
- それまでは本 ADR + PR テンプレの 1 行で運用する。**これはこのリポジトリの経験則に反する選択であり、意図的なトレードオフである** (Negative に明記)

## Consequences

### Positive

- **Issue → ブランチ → PR の索引が名前だけで辿れる** — PR が閉じたあとでも Issue 番号から作業の痕跡に戻れる (ADR 0011 の可視面が 1 つ増える)
- **WIP 上限の自動判定に道ができる** — 「今 in-flight の実装ストリーム本数」がブランチ名の正規表現で数えられる形になる。ADR 0036 D5 / 0043 D3 の規律が、初めて計数可能な対象を持つ
- **ADR 0043 の claim ロックが名前衝突で壊れる経路が塞がる** — `claim/*` が予約名だと明文化される
- **既存標準に乗るので方言の保守が要らない** — Conventional Branch 1.1.0 が `claude/` `codex/` を正式 type として持っており、外部ツール (lint / skill) をそのまま使える余地が残る
- **移行コストゼロで始められる** — 既存 131 本に触らない

### Negative

- **規約であって機構ではない (D7)** — 「規律は破られ、機構は守られる」というこのリポジトリの実績 (ADR 0036 / 0028 / 0027) に真っ向から反する選択。破られても赤くならず、静かに揃わなくなる。受け入れる理由は**破られたときの損害が小さいこと** (索引が欠けるだけで、main の履歴もロックも壊れない) と、**強制する先の自動化がまだ存在しないこと**。ここは PO 裁定で覆されてよい論点
- **PR 直前に名前を切り直す 1 手順が全セッションに増える** — Claude Code on the web の自動生成名 (`claude/<slug>-<random>`) と規約名は一致しない。ADR 0043 D5 の台帳確認と同性質のコスト
- **既存 115 本は残り続ける** — `git ls-remote` のノイズは当面消えず、「決めたのに現場が揃っていない」状態が可視で残る。しかも**この環境からは掃除を実行できない実測**があるため、いつ消えるかは人間の 1 クリック待ちになる
- **Issue を先に立てる原則が、些細な修正のリードタイムを伸ばす** — D3 の例外 (用途 type + 番号なし) で緩和するが、例外にするかの判断が毎回発生する
- **3 営業日という数字にこのリポジトリでの実測の裏付けが無い** — Trunk Based Development の「couple of days」+ 日次 tick 3 回という組み立てで置いた初期値。運用してから見直す
- **`claude/` prefix は「誰が書いたか」しか伝えず、種類 (feat / fix / chore) は名前から分からないまま** — Conventional Branch の用途 prefix の利点を捨てている。ツールの自動生成名と戦わないための取引 (D2)
- **ADR 0043 が未マージの状態で依存している** — 0043 は Accepted (2026-08-12 debrief) だが PR [#284](https://github.com/yomote/mind-inbox/pull/284) がまだ open で、`docs/adr/0043-*.md` は `main` に存在しない。本 ADR の D4 のリンクは 0043 のマージ後に有効になる

## Pros and Cons of the Options

### Option A: 現状維持 (暗黙のまま)

- Good, because 決めるコストも移行コストもゼロ
- Bad, because Context の 4 つの静かな破損 (計数不能 / 予約名前空間の無防備 / 掃除基準なし / Issue から辿れない 86 本) がそのまま続く
- Bad, because ADR 0043 D5 の claim ロックが「名前空間を予約している」という前提を、どこも保証していない状態が残る

### Option B: GitHub Flow の明文化 + Conventional Branch 準拠 + Issue 番号必須 (採用)

- Good, because 戦略の変更がゼロ — 既にやっていることを書き起こすだけなので、実装も移行も要らない
- Good, because 既存標準 (Conventional Branch 1.1.0) に `claude/` `codex/` が定義されており、このリポジトリの役割分担 (ADR 0035) にそのまま当たる
- Good, because Issue 番号を必須にすることで、規律 (WIP 上限) が初めて機械で数えられる対象を持つ
- Bad, because 当面は CI 強制が無く、規律に依存する (D7 / Negative)
- Bad, because 自動生成名との切り直しという手順が 1 つ増える

### Option C: git-flow 型の重い分岐モデル

`develop` を恒久の統合ブランチとして持ち、`release/*` `hotfix/*` を分岐させる古典的モデル。

- Good, because 複数バージョンを並行保守する製品では release 分岐が要る
- Bad, because このリポジトリは main → dev 自動デプロイ (ADR 0013) + リリース PR (`main → release` / ADR 0019) で既にデプロイが回っており、`develop` を足すと **required check の門 (ADR 0036) を通る回数が二重になる**だけ
- Bad, because Trunk Based Development / GitHub Flow が一貫して指摘する「長命ブランチは統合コストを生む」に真っ向から反する。実測の WIP 上限は 2 本であり、恒久分岐は過剰
- Bad, because 並行保守すべき出荷済みバージョンが現時点で存在しない (dev 環境 1 面のみ)

### Option D: 命名規約を自前で新規定義する

- Good, because このリポジトリ固有の事情 (Issue 番号 / claim 名前空間) を最初から織り込める
- Bad, because 方言の保守コストが乗る。外部の lint / skill / 他プロジェクトの知識がそのまま使えなくなる
- Bad, because **自前で作る必要が無い** — Conventional Branch 1.1.0 は `claude/` `codex/` を正式 type として持ち、ticket 番号を description に入れる形 (`feature/issue-123-add-oauth`) も定義済み。固有事情は「Issue 番号を必須にする」「予約名前空間を列挙する」という**上乗せ**で表現でき、規約そのものを作り直す理由にならない

## 動作検証 (この ADR が実装されたと言える条件)

1. **本 ADR マージ後に作成された PR** の head ブランチ名が、下記の**完全パターン**のいずれかに合致する。**どれにも合致しないものが 0 本であること**が条件

   ```text
   ^(claude|codex|feature|feat|fix|bugfix|hotfix)/[0-9]+(-[a-z0-9]+)+$   # D2: Issue 番号必須
   ^(chore|docs|test)/[a-z0-9]+(-[a-z0-9]+)*$                            # D3: 番号なし例外
   ^dependabot/                                                          # 生成名を変えられない (形式は問わない)
   ```

   `[a-z0-9]+` をハイフンで連結する形にしているのは、Conventional Branch 1.1.0 の Naming Rules (小文字のみ / 連続ハイフン禁止 / 先頭末尾ハイフン禁止) を**パターン自体で表現する**ため。prefix だけを見る条件では `feature/foo` (番号なし) / `claude/123-FOO` (大文字) / `chore/a--b` (連続ハイフン) がすべて通ってしまい、規約違反を検出できない

   **観測対象を「ブランチ」ではなく「PR の head」にしたのは、新規かどうかを再現可能に判定するため。** `git ls-remote --heads origin` の出力は ``<oid>` + タブ + `<ref>`` だけで**作成日時を返さない**。tip の commit 日時で代用しても、古い commit を指す新規 ref と、既存 ref への追記を区別できない。一方 **PR の `created_at` は API が返す**ので、「本 ADR マージ後に作成されたか」が一意に決まる。D2 の判定点 (「PR を出す時点」) とも一致する
2. `claim/*` `data/*` に作業ブランチが切られていない
3. **merged** な PR の head ブランチが残っていない (Automatically delete head branches 有効化後)。**未マージ close** については、head が残っている closed PR の**それぞれに削除の `needs-human` 引き渡しが存在する** (D5)。件数の増減ではなく**引き渡しの有無**で判定する — 件数条件では個々の head が消えなくても満たせてしまい、D5 の「PR の決着と同時に削除する」を検証できないため
4. `git ls-remote --heads origin` の総数が減少に転じる (置き去り 115 本の掃除 Issue の完了後)

## Links

### 一次ソース (すべて 2026-08-12 取得)

| ソース                                                                                                                  | 採った内容                                                                                                                                                                                                                                                                                                                                                                                                                                          | 取得状況                                                                                                                            |
| ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| GitHub Flow (GitHub 公式) <https://docs.github.com/en/get-started/using-github/github-flow>                             | 「短く説明的なブランチ名 (a short, descriptive branch name) が、進行中の作業を一目で見せる」/ 「PR をマージしたらブランチを削除する」— D1 / D5 の根拠                                                                                                                                                                                                                                                                                               | **WebSearch 経由の引用のみ**。`docs.github.com` への直接 fetch は本実行環境の egress 制限でブロックされ、**原文ページ全体は未取得** |
| Trunk Based Development, Short-Lived Feature Branches <https://trunkbaseddevelopment.com/short-lived-feature-branches/> | 短命ブランチの寿命は「a couple of days」、2 日を超えると long-lived feature branch 化のリスク — D5 の 3 営業日の根拠                                                                                                                                                                                                                                                                                                                                | **WebSearch 経由の引用のみ**。直接 fetch は egress 制限でブロック、**原文ページ全体は未取得**                                       |
| Conventional Branch 1.1.0 <https://github.com/conventional-branch/conventional-branch>                                  | `<type>/<description>` 形式 / type = `feature`(`feat`) `bugfix`(`fix`) `hotfix` `release` `chore` + AI エージェント prefix `ai` `copilot` `cursor` **`claude`** `codex` / Naming Rules (小文字のみ / 英数字・ハイフン・ドット / ドットは `release/` のバージョンのみ / 連続ハイフン・先頭末尾ハイフン禁止) / Description Style (kebab-case, 2〜5 語, 約 50 字, ticket 番号に言及があれば含める — 例 `feature/issue-123-add-oauth`) — D2 / D3 の根拠 | **直接取得済み** (`raw.githubusercontent.com` の `README.md` と `skills/conventional-branch/SKILL.md`)                              |
| Git `gitglossary` <https://git-scm.com/docs/gitglossary>                                                                | 「ref namespace は階層で、`refs/heads/` 階層がローカルブランチを表す」/ 「branch head は `refs/heads` 階層に格納される」— D4 の名前空間論の根拠                                                                                                                                                                                                                                                                                                     | **WebSearch 経由の引用のみ**。`git-scm.com` への直接 fetch は egress 制限でブロック、**原文ページ全体は未取得**                     |
| Git `git-check-ref-format` <https://git-scm.com/docs/git-check-ref-format>                                              | ref 名は `/` で階層化でき、少なくとも 1 つの `/` を含む (= `heads/` 等のカテゴリを強制する) — D2 の形式が Git の制約と矛盾しないことの確認                                                                                                                                                                                                                                                                                                          | **WebSearch 経由の引用のみ**。直接 fetch は egress 制限でブロック、**原文ページ全体は未取得**                                       |

**未取得**: リポジトリ設定「Automatically delete head branches」の現在値 — 本セッションから `gh api` が 403 (GitHub App 未接続) で叩けず確認できなかった。置き去り 115 本の存在からは「有効になっていない、または有効化以前のブランチが残っている」までしか言えない。

**取れなかった一次ソースについて**: 上表の「WebSearch 経由」の 4 件は、検索エンジンが返した公式ページの引用文に依拠している。原文ページを直接開いての一字一句の確認は行えていない。数値 (「a couple of days」) と方針 (「マージしたら削除」) の粒度では確かだが、**逐語引用として扱わないこと**。

### 関連 ADR / Issue

- ADR 0043 (**`main` に未収録** — PR [#284](https://github.com/yomote/mind-inbox/pull/284)) D5 — `refs/heads/claim/<Issue 番号>` の CAS 着工ロック (本 ADR D4 の接続先 / PR [#284](https://github.com/yomote/mind-inbox/pull/284) がマージされるまで `main` に存在しない)
- [ADR 0041](archive/operations/ux-observations-on-git-data-branch.md) — `data/ux-observations` (機械が読み書きするブランチ / D4 の予約名前空間)
- [ADR 0036](archive/operations/merge-gate-as-required-check-and-pm-cadence.md) D2 / D5 — ブランチ保護と WIP 上限 2 (D7 の機構化条件)
- [ADR 0033](archive/operations/parent-implements-via-subagent-when-child-sessions-are-gated.md) / [ADR 0021](archive/operations/parent-session-as-pm-orchestrator.md) — セッション分配 (ブランチが増える源)
- [ADR 0011](archive/operations/github-projects-as-execution-dashboard.md) / [ADR 0044](archive/operations/stream-lanes-as-the-project-map.md) — Issue が真実 / 地図 (D3 の「先に Issue を立てる」の根拠)
- [#175](https://github.com/yomote/mind-inbox/issues/175) — open PR 間の並行を機械で数えられない (本 ADR の Context 1 と同型の問題)
