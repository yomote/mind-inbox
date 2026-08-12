# 0042. pm-accept は「実装差分が不変の main 追随」に引き継ぎ、直列化は Merge Queue に任せる

- Status: Proposed
- Date: 2026-08-11
- Deciders: yomote (PO) / PM セッション (PO 裁定 2026-08-11 を受けた実装側 ADR)
- Related: [ADR 0036](0036-merge-gate-as-required-check-and-pm-cadence.md) (マージの門 — 本 ADR はその運用改訂。D1 の pm-accept 失効規則を狭め、Considered Options D の merge queue を採用に転じる) / [ADR 0040](0040-project-continuity-three-layers.md) D1 (マージ**執行**とストール検知 — 本 ADR は**受け入れ判定**側で、分担は D4 参照) / [ADR 0035](0035-role-split-across-agents-and-actions.md) (役割分担)

Technical Story: 2026-08-11 の実害 — PR #243 が「追いつき競争」で 4 周 (base 追随 → pm-accept 失効 → 再受け入れ → 別 PR がマージされてまた out-of-date)。#260 / #261 / #263 が同時周回して互いの pm-accept を無効化し合った。queue 有効化の設定作業は needs-human Issue #269。

## Context and Problem Statement

ADR 0036 D1 は「pm-accept は head SHA を含む。push で自動失効する」と定めた。これは**未レビューコードのマージを防ぐ**ためのリセット規則だが、strict なブランチ保護 (up-to-date 必須) の下では **base (main) に追随するだけの push** も失効を引き起こす。

main が活発な日はこうなる: PR を受け入れる → 別 PR がマージされ main が進む → out-of-date で main をマージ → pm-accept 失効 → 再受け入れ → その間にまた別 PR がマージ → 振り出し。**実装は 1 文字も変わっていないのに、PM の受け入れクリックだけが周回する**。open PR が 3〜4 本あると全員が互いを失効させ合い、収束しない。

決めるべきは 2 つ: (1) 「実装が変わっていない push」で pm-accept を失効させない安全な判定は何か。(2) 「追いついてからマージする」直列化を誰がやるか。

## Decision Drivers

- **門を緩めない** — 実装差分が 1 文字でも変わる push は従来どおり再受け入れ必須。「main 追随のフリをした実装変更」(evil merge) も落とせること
- **判定は機械で** (ADR 0036 の原則) — 「これは追随だけだから OK」を人間やエージェントの解釈にしない
- **追いつき競争の根本は直列化の不在** — 引き継ぎだけでは「マージの順番待ち」は解けない
- 追加課金ゼロ・長期クレデンシャル増加ゼロ (ADR 0031 の driver を継承)

## Considered Options

- Option A: up-to-date 必須 (strict) をやめる
- Option B: base 追随 push を bot が自動で再受け入れする
- Option C: **pm-accept の引き継ぎ判定 + GitHub Merge Queue** (採用)
- Option D: WIP 上限を 1 に下げて PR を直列に流す

## Decision Outcome

Chosen option: **Option C** — 2 本立てで解く。

### D1 — pm-accept は「実装差分が不変の main 追随」に引き継ぐ

review-gate (`cicd/scripts/review-gate/check.py`) の受け入れ判定を拡張する。現 head への直接の `[pm-accept] <head 7 桁>` が無い場合、**最新の** 信頼できる (author_association が OWNER/MEMBER/COLLABORATOR の) `[pm-accept] <sha>` について、次が**全部**成立するときに限り受け入れを現 head に引き継ぐ:

| # | 条件 | 検出方法 (GitHub API) |
| --- | --- | --- |
| 1 | 受け入れ SHA が PR のコミット列に一意に解決できる | `pulls/N/commits` (250 件超は判定不能 = 不成立) |
| 2 | 現 head から受け入れコミットへ**第一親で辿れる** (rebase / force-push でコミットが書き換わっていない) | 同上の parents |
| 3 | その間の追加コミットが**すべて base からのマージコミット** (ちょうど 2 親、第二親が base に到達済み) | `compare/{base}...{第二親}` の `ahead_by == 0` |
| 4 | **実装差分 (base...head = PR の Files changed) の指紋が受け入れ時点と完全一致** | `compare/{base}...{sha}` の files (filename / status / rename 元 / blob SHA / patch 本文を sha256 に畳む) |

- 条件 3 と 4 は補完関係: 実装コミットの混入は 3 が落とし、**マージコミットに紛れた実装変更 (evil merge) は 4 が落とす**。条件 4 単独にしないのは「push A→push 打ち消し B で差分同一」のような紛れを構造側でも塞ぐため
- **判定不能は不成立** (compare の files 300 件打ち切り / commits 250 件打ち切り / SHA 解決の曖昧) — 「見えなかったものを同一」と書かない (status-page と同じ規律)
- 判定理由は status description に可視化する — 成立時「OK: pm-accept を \<sha\> から引き継ぎ (差分不変)」、不成立時は「引き継ぎ不成立: \<理由\>」を従来の赤メッセージに併記
- 既知の保守的な副作用: main 側が PR と**同じファイル**に触れた場合、コンフリクトせずマージできても patch の文脈や blob が変わり不成立になる (= 再受け入れが要る)。緩める方向の誤判定よりよい

### D2 — 直列化は GitHub Merge Queue に任せる (ADR 0036 Considered Options D の再裁定)

- required check を出す workflow (`test.yml` の test / lint-and-build、`review-gate.yml`、`auto-improve-guard.yml`、`adr-number-guard.yml`) に `merge_group:` トリガーを追加し、**queue の一時 branch (merge group) でも check が報告される**ようにする。これが無い required check は queue で永遠に pending になり、PR がタイムアウト脱落する
- review-gate は merge_group では `head_ref` (`gh-readonly-queue/<base>/pr-<N>-<sha>`) から対象 PR を解決し、**同じ判定** (pm-accept 引き継ぎ含む) を行って **merge group の head SHA** に status を貼る。**PR を解決できないときは failure を貼る** (安全側 — 静かに緑を貼ると未受け入れ PR が queue を素通りする。failure は queue から外れて PR に戻るだけで不可逆でない)
- auto-improve-guard は merge_group では対象 PR の files API で検査する (merge group の diff を直接取ると queue 内で先行する他 PR の変更が混ざるため)
- **queue の有効化はリポ設定 (web UI) = needs-human Issue #269 で PO が行う**。有効化されるまで `merge_group:` トリガーは発火しないだけで、既存の PR フローに影響しない (先に入れて安全)

ADR 0036 は merge queue を「並行 2 本・同一アカウント運用にはオーバーキル」として不採用にした。**実測で覆す**: 並行 3〜4 本 + 活発な main で「追いつき競争」が 1 日 4 周の実害になった。queue は「追いついて check を回してからマージ」をサーバー側で直列化し、セッションの生死に依存しない (auto-merge と同じ性質)。

### D3 — 信頼境界: 門のスクリプトは常に main の信頼版を実行する (PR #271 Codex P1)

merge_group イベントの checkout は queue の一時 branch = **PR が改変したコード**を含む。`statuses: write` を持つ job がそこから `check.py` を実行すると、`check.py` を改変した (外部 fork の) PR が偽の `review-gate=success` を merge group SHA に貼り、門を丸ごと迂回できる — `pull_request` イベントでは fork の GITHUB_TOKEN を GitHub が read-only に落とすが、**merge_group は base リポジトリのイベントなので落ちない**。よって:

- **review-gate の merge_group 判定は専用 job に分離し、`ref: main` で checkout した信頼版スクリプトだけを実行する** (判定材料は全て API 経由 — queue ref の作業ツリー自体が不要)。permissions は `contents: read` + `statuses: write` のみ (`pull-requests: write` は持たない — advisory は merge_group で投稿しないため)
- **未信頼コード (PR 由来の npm / pnpm スクリプト) を実行する job には write を持たせない** — `test.yml` の workflow permissions を `contents: read` に落とし、PR への sticky コメント投稿は未信頼コードを実行しない別 job (`pr-comment`、`pull-requests: write` のみ) へ artifact 経由で分離した
- `pull_request` イベント側の review-gate は従来どおり PR 版 `check.py` を実行する (PR #258 / ADR 0040 D1 が導入した `gate-pr` job — `contents: write` を持たず `REVIEW_GATE_EXECUTE_MERGE=false`)。fork PR は token が read-only で偽 status を貼れず、same-repo PR の作者は信頼境界の内側 (単一アカウント運用)。挙動を変えないことを選ぶ
- auto-improve-guard / adr-number-guard の merge_group 経路はリポジトリのスクリプトを実行しない (インライン bash + git / gh API のみ) かつ read 権限のみで、この穴の対象外
- 帰結: **required check の workflow に merge_group を足すときは「queue ref のコードを write 権限で実行していないか」を必ず確認する** (Runbook の Common Issues に追記)

本項は PR #258 (ADR 0040 D1) が同じ理由で入れた job 分離 (`gate-pr` / `gate-trusted` / `sweep` — イベントの由来で権限を分ける) と**同じ原則の merge_group への拡張**であり、`gate-merge-group` はその 4 本目にあたる。

### D4 — マージ**執行** (ADR 0040 D1) との分担: 受け入れ判定は 0042、実行は 0040 / queue

PR #258 (ADR 0040 D1) は「review-gate 自身が受け入れ済み・auto-merge 武装済みの PR をマージまで実行する + ストール検知」を入れた。本 ADR とは**排他ではなく補完**で、層が違う:

| 層 | 担当 | 内容 |
| --- | --- | --- |
| **受け入れ判定** (何をマージしてよいか) | 本 ADR (0042) | pm-accept の引き継ぎ。判定は `evaluate_gate` → `decide` の 1 経路に置くので、**マージ執行側のマージ直前フル再評価にも同じ引き継ぎが効く** (「追随しただけの PR」が執行の直前で門を閉じられない) |
| **実行** (いつ誰がマージを叩くか) | ADR 0040 D1 / Merge Queue | イベント経路 (`gate-trusted`) と 30 分毎 sweep がマージ API を叩く。queue 有効化後は queue が直列化してマージする |

重複の整理:

- **merge_group の run はマージ執行を行わない** (`REVIEW_GATE_EXECUTE_MERGE` 相当を無効化し、job も `contents: write` を持たない) — queue がマージするので二重機構にしない
- **PR 側経路のマージ執行は残す** — queue 有効化前 (#269 まで) はそれが唯一の自動マージ経路であり、有効化後も「auto-merge 武装 = queue 投入」の入口として整合する。queue 有効化後に不要と実測できたら 0040 D1 側の改訂として畳む (未決)
- **ストール検知 (0040 D1) は残す** — queue は「入った PR」の直列化しかせず、「auto-merge 未武装で全緑のまま放置」「needs-human / Proposed ADR の 48h 停滞」は queue の外側の失敗モード。重複しない

### Positive Consequences

- base 追随だけの push で PM の再受け入れが不要になる — 受け入れは「実装差分への承認」という本来の意味に戻る
- マージの順番待ちを GitHub が直列化する — PM セッションが「追いつき → 再受け入れ」を周回する時間が消える
- 判定理由が status description に残る — 引き継ぎの成立・不成立が PR 上で観測できる (誤判定の実測 → 修正のループが回る)
- 追加課金ゼロ・新しいクレデンシャルゼロ

### Negative Consequences

- review-gate の判定が複雑になる (compare API 依存が増える)。誤判定は「保守的に赤」に倒しているが、最初の数 PR で実測が要る
- merge queue の一時 branch でも CI (test / lint-and-build ≒ 15 分) が回る — Actions 分数の消費が増える (public リポジトリなので無料枠内)
- pm-accept 引き継ぎは**コメント列の最新受け入れだけ**を見る — PM が受け入れをやり直すと古い受け入れへは遡らない (意図した仕様だが、運用上は「受け入れは最後に 1 回」が前提)
- commit status (checks API でなく statuses API) が merge queue の required check として数えられることは**実測で確認が要る** (下の動作検証 1)

## Pros and Cons of the Options

### Option A: up-to-date 必須をやめる

- Good, because 追いつき競争が即消える
- Bad, because 「古い main でテストされたものが main に入る」— semantic conflict がマージ後に露見する。merge queue が同じ問題をより良く解く

### Option B: base 追随 push を bot が自動で再受け入れ

- Good, because review-gate の判定は単純なまま
- Bad, because 受け入れコメントの意味 (PM の判断の痕跡) が壊れる — bot が `[pm-accept]` を書くなら門は実質無い。「追随かどうか」の判定は結局 D1 と同じものが要る

### Option C: 引き継ぎ判定 + Merge Queue (採用)

- Good, because 失効規則の**意味** (実装差分への承認) を保ったまま偽陽性だけを消す
- Good, because 直列化がサーバー側 (セッションの生死・エージェントの規律に依存しない — 「規律は破られ、機構は守られる」)
- Bad, because 実装が繊細 (上の Negative Consequences)

### Option D: WIP 上限 1

- Good, because 機構の追加なし
- Bad, because スループットが 1/2〜1/4 になる。並行を捨てるのは ADR 0036 D5 (上限 2、無事故なら緩和) の方向とも逆

## 動作検証 (この ADR が実装されたと言える条件)

1. queue 有効化後、queue に入った PR で review-gate / test / lint-and-build / auto-improve-guard / adr-number-guard が merge group SHA に報告され、マージまで到達する (実測 — **statuses API が queue の required check として数えられることの確認を含む**)
2. 受け入れ済み PR に main をマージで追随させても review-gate が緑のまま、description に「pm-accept を \<sha\> から引き継ぎ (差分不変)」が出る (実測)
3. 受け入れ後に実装コミットを積むと従来どおり赤に戻り、「引き継ぎ不成立」の理由が description に出る (実測)
4. [L1] テスト: 引き継ぎの成立 / 実装コミット混入 / 非 main マージ / evil merge (差分変化) / rebase / 判定不能の各ケース (`cicd/scripts/review-gate/test_check.py`)
5. 引き継ぎで緑になった PR が**マージ執行 (ADR 0040 D1) の直前フル再評価でも緑のまま**マージされる (実測 — 判定が `evaluate_gate` の 1 経路にあることの動作確認)

## 未決

- merge queue の設定値 (merge method = squash / 並行ビルド数 / タイムアウト) — #269 で PO が設定するときに初期値を決め、実測で調整
- up-to-date 必須 (strict) を queue 有効化後に外すか — queue が同じ保証を持つため外せるはずだが、#269 の設定作業とセットで判断
- `iac-validate` など paths フィルタ付き workflow を required に入れる場合の merge_group 対応 (現状 required は上の 5 check のみという前提)
- **queue 有効化後に PR 側経路のマージ執行 (ADR 0040 D1) を畳むか** — queue がマージを直列実行するなら二重だが、queue 未投入の PR (auto-merge 未武装) には効いている。実測してから 0040 側の改訂で判断 (D4)

## Links

- 関連 ADR: [0036](0036-merge-gate-as-required-check-and-pm-cadence.md) / [0040](0040-project-continuity-three-layers.md) / [0035](0035-role-split-across-agents-and-actions.md) / [0031](0031-agent-reaches-outside-via-github-actions.md)
- Runbook: [merge-queue.md](../runbooks/merge-queue.md)
- needs-human: Issue #269 (queue 有効化)
