# 0042. pm-accept は「実装差分が不変の main 追随」に引き継ぎ、追いつき自体を strict OFF で無くす

- Status: Proposed (D2 を 2026-08-12 に実測で書き直し — 下記「D2 の改訂」。裁定は次の debrief)
- Date: 2026-08-11
- Deciders: yomote (PO) / PM セッション (PO 裁定 2026-08-11 を受けた実装側 ADR)
- Related: [ADR 0036](merge-gate-as-required-check-and-pm-cadence.md) (マージの門 — 本 ADR はその運用改訂。D1 の pm-accept 失効規則を狭め、Considered Options D の merge queue を採用に転じる) / [ADR 0040](project-continuity-three-layers.md) D1 (マージ**執行**とストール検知 — 本 ADR は**受け入れ判定**側で、分担は D4 参照) / [ADR 0035](role-split-across-agents-and-actions.md) (役割分担)

Technical Story: 2026-08-11 の実害 — PR #243 が「追いつき競争」で 4 周 (base 追随 → pm-accept 失効 → 再受け入れ → 別 PR がマージされてまた out-of-date)。#260 / #261 / #263 が同時周回して互いの pm-accept を無効化し合った。queue 有効化の設定作業は needs-human Issue #269 → **その #269 で「Merge Queue は org 所有リポジトリ専用」と判明し、strict OFF に切り替えた** (D2 の改訂を参照)。

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
- Option C: pm-accept の引き継ぎ判定 + GitHub Merge Queue (**初版で採用 → 2026-08-11 に実行不能と判明**)
- Option D: WIP 上限を 1 に下げて PR を直列に流す
- **Option E: pm-accept の引き継ぎ判定 + strict OFF** (現行案 — D2 の改訂で Option C から乗り換えた)

## Decision Outcome

Chosen option: **Option E** — 「実装が変わっていない追随では受け入れを失効させない」(D1) と「そもそも追随を要らなくする」(改訂 D2) の 2 本立てで解く。**Option C との差は 2 本目だけ**で、D1 は初版から変わっていない。

### D1 — pm-accept は「実装差分が不変の main 追随」に引き継ぐ

review-gate (`cicd/scripts/review-gate/check.py`) の受け入れ判定を拡張する。現 head への直接の `[pm-accept] <head 7 桁>` が無い場合、**最新の** 信頼できる (author_association が OWNER/MEMBER/COLLABORATOR の) `[pm-accept] <sha>` について、次が**全部**成立するときに限り受け入れを現 head に引き継ぐ:

| #   | 条件                                                                                                  | 検出方法 (GitHub API)                                                                                     |
| --- | ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 1   | 受け入れ SHA が PR のコミット列に一意に解決できる                                                     | `pulls/N/commits` (250 件超は判定不能 = 不成立)                                                           |
| 2   | 現 head から受け入れコミットへ**第一親で辿れる** (rebase / force-push でコミットが書き換わっていない) | 同上の parents                                                                                            |
| 3   | その間の追加コミットが**すべて base からのマージコミット** (ちょうど 2 親、第二親が base に到達済み)  | `compare/{base}...{第二親}` の `ahead_by == 0`                                                            |
| 4   | **実装差分 (base...head = PR の Files changed) の指紋が受け入れ時点と完全一致**                       | `compare/{base}...{sha}` の files (filename / status / rename 元 / blob SHA / patch 本文を sha256 に畳む) |

- 条件 3 と 4 は補完関係: 実装コミットの混入は 3 が落とし、**マージコミットに紛れた実装変更 (evil merge) は 4 が落とす**。条件 4 単独にしないのは「push A→push 打ち消し B で差分同一」のような紛れを構造側でも塞ぐため
- **判定不能は不成立** (compare の files 300 件打ち切り / commits 250 件打ち切り / SHA 解決の曖昧) — 「見えなかったものを同一」と書かない (status-page と同じ規律)
- 判定理由は status description に可視化する — 成立時「OK: pm-accept を \<sha\> から引き継ぎ (差分不変)」、不成立時は「引き継ぎ不成立: \<理由\>」を従来の赤メッセージに併記
- 既知の保守的な副作用: main 側が PR と**同じファイル**に触れた場合、コンフリクトせずマージできても patch の文脈や blob が変わり不成立になる (= 再受け入れが要る)。緩める方向の誤判定よりよい

### D2 の改訂 (2026-08-12) — Merge Queue は使えない。追いつき自体を strict OFF で消す

**初版の D2 は実行不能だった。** Merge Queue は **organization 所有のリポジトリ向け**の機能で、`yomote/mind-inbox` は個人アカウント所有のため設定項目が UI に出ない (2026-08-11、PO が Settings → Rules → Rulesets を実際に開いて確認 — [#269 コメント](https://github.com/yomote/mind-inbox/issues/269#issuecomment-5262150635))。**前提を確認せずに手順まで書いたのは PM の落ち度**。

改訂後の決定案 (**運用としては 2026-08-11 に PO が strict OFF を実施済み。ADR としての裁定は次回 debrief** — Status が Proposed のままなのはこのため):

- **直列化はしない。「Require branches to be up to date before merging」(strict) を OFF にする** — 渋滞の実体は「直列化の不在」ではなく **strict そのもの**だった。個人リポでもこれは外せる。2026-08-11 に PO が OFF にし、追いつきレースは即消滅した
- **代償を引き受ける**: 少し古い main に対してテストされた PR が入りうる。組み合わせの破綻は**後段で捕まえる** — main の CI / dev への auto-deploy / golden-path 監視 / [#258](https://github.com/yomote/mind-inbox/issues/258) のマージ執行時のフル再評価が受け皿になる。「マージ前に完全に直列化する」を諦め、「壊れたら早く気づく」に賭ける判断
- **`merge_group` 対応 (PR #271) は捨てない** — 将来 org へ移す判断をすればそのまま効く。有効化されていない間は**トリガーが発火しないだけ**で、PR フローに影響しない
- **D1 (pm-accept の引き継ぎ) は strict の有無に関係なく効き続ける** — base 追随はコンフリクト解消などで今後も起きるため

渋滞対策は結果として **3 層**で成立した: **#258 マージ執行 (緑になったら workflow が叩く) / D1 の引き継ぎ (追随のたびの受け入れ儀式が要らない) / strict OFF (そもそも追随が要らない)**。

**2026-08-12 の実測で分かった注意点**: strict が OFF になった以上、**base が進んだからといって反射的に `update-branch` してはいけない**。同日 PR #286 で PM が不要な追随を行い、それが引き金で D1 の引き継ぎが不成立になって受け入れをやり直した (原因は [#323](https://github.com/yomote/mind-inbox/issues/323) — D1 の判定が base 側の変化を実装差分の変化として拾う実装バグ)。追随が要るのは **コンフリクトが実際に出たとき**だけ。

<details>
<summary>初版の D2 (実行不能と判明。経緯の記録として残す)</summary>

### D2 — 直列化は GitHub Merge Queue に任せる (ADR 0036 Considered Options D の再裁定)

- required check を出す workflow (`test.yml` の test / lint-and-build、`review-gate.yml`、`auto-improve-guard.yml`、`adr-number-guard.yml`) に `merge_group:` トリガーを追加し、**queue の一時 branch (merge group) でも check が報告される**ようにする。これが無い required check は queue で永遠に pending になり、PR がタイムアウト脱落する
- review-gate は merge_group では `head_ref` (`gh-readonly-queue/<base>/pr-<N>-<sha>`) から対象 PR を解決し、**同じ判定** (pm-accept 引き継ぎ含む) を行って **merge group の head SHA** に status を貼る。**PR を解決できないときは failure を貼る** (安全側 — 静かに緑を貼ると未受け入れ PR が queue を素通りする。failure は queue から外れて PR に戻るだけで不可逆でない)
- auto-improve-guard は merge_group では対象 PR の files API で検査する (merge group の diff を直接取ると queue 内で先行する他 PR の変更が混ざるため)
- **queue の有効化はリポ設定 (web UI) = needs-human Issue #269 で PO が行う**。有効化されるまで `merge_group:` トリガーは発火しないだけで、既存の PR フローに影響しない (先に入れて安全)

ADR 0036 は merge queue を「並行 2 本・同一アカウント運用にはオーバーキル」として不採用にした。**実測で覆す**: 並行 3〜4 本 + 活発な main で「追いつき競争」が 1 日 4 周の実害になった。queue は「追いついて check を回してからマージ」をサーバー側で直列化し、セッションの生死に依存しない (auto-merge と同じ性質)。

</details>

### D3 — 信頼境界: 門のスクリプトは常に main の信頼版を実行する (PR #271 Codex P1)

merge_group イベントの checkout は queue の一時 branch = **PR が改変したコード**を含む。`statuses: write` を持つ job がそこから `check.py` を実行すると、`check.py` を改変した (外部 fork の) PR が偽の `review-gate=success` を merge group SHA に貼り、門を丸ごと迂回できる — `pull_request` イベントでは fork の GITHUB_TOKEN を GitHub が read-only に落とすが、**merge_group は base リポジトリのイベントなので落ちない**。よって:

- **review-gate の merge_group 判定は専用 job に分離し、`ref: main` で checkout した信頼版スクリプトだけを実行する** (判定材料は全て API 経由 — queue ref の作業ツリー自体が不要)。permissions は `contents: read` + `statuses: write` のみ (`pull-requests: write` は持たない — advisory は merge_group で投稿しないため)
- **未信頼コード (PR 由来の npm / pnpm スクリプト) を実行する job には write を持たせない** — `test.yml` の workflow permissions を `contents: read` に落とし、PR への sticky コメント投稿は未信頼コードを実行しない別 job (`pr-comment`、`pull-requests: write` のみ) へ artifact 経由で分離した
- `pull_request` イベント側の review-gate は従来どおり PR 版 `check.py` を実行する (PR #258 / ADR 0040 D1 が導入した `gate-pr` job — `contents: write` を持たず `REVIEW_GATE_EXECUTE_MERGE=false`)。fork PR は token が read-only で偽 status を貼れず、same-repo PR の作者は信頼境界の内側 (単一アカウント運用)。挙動を変えないことを選ぶ
- auto-improve-guard の merge_group 経路はリポジトリのスクリプトを実行しない (インライン bash + git / gh API のみ) かつ read 権限のみで、この穴の対象外。adr-number-guard は #381 (PR #469) 以降 queue ref の checkout から `cicd/scripts/adr-number-guard/adr_guard.py` を実行するが、job の permissions は `contents: read` のみで `statuses: write` を持たない — 改変されたスクリプトが実行されても偽の status を貼る経路が無く、引き続きこの穴の対象外
- 帰結: **required check の workflow に merge_group を足すときは「queue ref のコードを write 権限で実行していないか」を必ず確認する** (Runbook の Common Issues に追記)

本項は PR #258 (ADR 0040 D1) が同じ理由で入れた job 分離 (`gate-pr` / `gate-trusted` / `sweep` — イベントの由来で権限を分ける) と**同じ原則の merge_group への拡張**であり、`gate-merge-group` はその 4 本目にあたる。

### D4 — マージ**執行** (ADR 0040 D1) との分担: 受け入れ判定は 0042、実行は 0040

PR #258 (ADR 0040 D1) は「review-gate 自身が受け入れ済み・auto-merge 武装済みの PR をマージまで実行する + ストール検知」を入れた。本 ADR とは**排他ではなく補完**で、層が違う:

| 層                                      | 担当          | 内容                                                                                                                                                                                            |
| --------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **受け入れ判定** (何をマージしてよいか) | 本 ADR (0042) | pm-accept の引き継ぎ。判定は `evaluate_gate` → `decide` の 1 経路に置くので、**マージ執行側のマージ直前フル再評価にも同じ引き継ぎが効く** (「追随しただけの PR」が執行の直前で門を閉じられない) |
| **実行** (いつ誰がマージを叩くか)       | ADR 0040 D1   | イベント経路 (`gate-trusted`) と定期 sweep がマージ API を叩く。**直列化する主体はいない** (改訂 D2)                                                                                            |

重複の整理:

- **PR 側経路のマージ執行が唯一の自動マージ経路** — Merge Queue が使えない以上、代替は無い。**ただし 2026-08-12 時点でこの経路は 405 で弾かれ続けており実際には動いていない** ([#327](https://github.com/yomote/mind-inbox/issues/327))
- **ストール検知 (0040 D1) は残す** — 「auto-merge 未武装で全緑のまま放置」「needs-human / Proposed ADR の 48h 停滞」を拾う層で、マージ執行とは別の失敗モードを見ている
- **merge_group の run はマージ執行を行わない** — queue が有効化されていない現状では発火しない (将来 org へ移したときの取り決めとして残す)

### Positive Consequences

- base 追随だけの push で PM の再受け入れが不要になる — 受け入れは「実装差分への承認」という本来の意味に戻る
- マージの順番待ちを GitHub が直列化する — PM セッションが「追いつき → 再受け入れ」を周回する時間が消える
- 判定理由が status description に残る — 引き継ぎの成立・不成立が PR 上で観測できる (誤判定の実測 → 修正のループが回る)
- 追加課金ゼロ・新しいクレデンシャルゼロ

### Negative Consequences

- review-gate の判定が複雑になる (compare API 依存が増える)。誤判定は「保守的に赤」に倒しているが、最初の数 PR で実測が要る
- pm-accept 引き継ぎは**コメント列の最新受け入れだけ**を見る — PM が受け入れをやり直すと古い受け入れへは遡らない (意図した仕様だが、運用上は「受け入れは最後に 1 回」が前提)
- **strict OFF (改訂後の D2) の代償**: 少し古い main に対してテストされた PR が入りうる。semantic conflict はマージ後 (main の CI / dev への auto-deploy / golden-path 監視) で露見する。**「壊れたら早く気づく」に賭けた**判断であり、壊れないことは保証しない
- ~~merge queue の一時 branch でも CI が回るので Actions 分数が増える~~ / ~~statuses API が queue の required check として数えられるかは実測が要る~~ — **どちらも Merge Queue 前提。改訂後の D2 では該当しない**

## Pros and Cons of the Options

### Option A: up-to-date 必須をやめる

- Good, because 追いつき競争が即消える
- Bad, because 「古い main でテストされたものが main に入る」— semantic conflict がマージ後に露見する
- **初版ではこれを「merge queue の方がより良く解く」として退けたが、queue が使えないと判明したため、改訂 D2 で D1 と組み合わせて採用に転じた (= Option E)**

### Option B: base 追随 push を bot が自動で再受け入れ

- Good, because review-gate の判定は単純なまま
- Bad, because 受け入れコメントの意味 (PM の判断の痕跡) が壊れる — bot が `[pm-accept]` を書くなら門は実質無い。「追随かどうか」の判定は結局 D1 と同じものが要る

### Option C: 引き継ぎ判定 + Merge Queue (初版で採用 → **実行不能**)

- Good, because 失効規則の**意味** (実装差分への承認) を保ったまま偽陽性だけを消す
- Good, because 直列化がサーバー側 (セッションの生死・エージェントの規律に依存しない — 「規律は破られ、機構は守られる」)
- Bad, because 実装が繊細 (上の Negative Consequences)
- **Fatal**: Merge Queue は organization 所有リポジトリ専用。個人アカウント所有の本リポでは設定項目自体が存在しない (2026-08-11 実測 / [#269](https://github.com/yomote/mind-inbox/issues/269))

### Option E: 引き継ぎ判定 + strict OFF (現行案)

- Good, because 追いつきが構造的に起きなくなる (直列化の実装も設定も要らない)
- Good, because Option C の良い半分 (D1 の引き継ぎ判定) をそのまま引き継げる
- Bad, because 古い main に対してテストされた PR が入りうる — **マージ前ではなくマージ後に壊れが見つかる**設計に賭けている (受け皿は main の CI / dev への auto-deploy / golden-path 監視)

### Option D: WIP 上限 1

- Good, because 機構の追加なし
- Bad, because スループットが 1/2〜1/4 になる。並行を捨てるのは ADR 0036 D5 (上限 2、無事故なら緩和) の方向とも逆

## 動作検証 (この ADR が実装されたと言える条件)

1. **strict OFF の状態で、main が進んでも受け入れ済み PR がそのままマージできる** (実測) — 初版の「queue に入った PR が merge group SHA で全 check 緑になる」は Merge Queue が使えないため**検証不能・廃止**
2. 受け入れ済み PR に main をマージで追随させても review-gate が緑のまま、description に「pm-accept を \<sha\> から引き継ぎ (差分不変)」が出る (実測) — **2026-08-12 に PR #286 で実施したところ不成立になり、[#323](https://github.com/yomote/mind-inbox/issues/323) を起票した。この条件は未達**
3. 受け入れ後に実装コミットを積むと従来どおり赤に戻り、「引き継ぎ不成立」の理由が description に出る (実測)
4. [L1] テスト: 引き継ぎの成立 / 実装コミット混入 / 非 main マージ / evil merge (差分変化) / rebase / 判定不能の各ケース (`cicd/scripts/review-gate/test_check.py`)
5. 引き継ぎで緑になった PR が**マージ執行 (ADR 0040 D1) の直前フル再評価でも緑のまま**マージされる (実測 — 判定が `evaluate_gate` の 1 経路にあることの動作確認)

## 未決

- **D1 の引き継ぎ判定が base の前進を実装差分の変化として拾う** ([#323](https://github.com/yomote/mind-inbox/issues/323) / P1) — 直すまで、追随のたびに再受け入れが要る
- **strict OFF で「古い main に対する PR」がどこまで許容できるか** — 実際に semantic conflict が main で露見したら、そのときの検知経路 (どの層が捕まえたか) を記録して判断材料にする
- ~~merge queue の設定値~~ / ~~strict を queue 有効化後に外すか~~ / ~~paths フィルタ付き workflow の merge_group 対応~~ / ~~queue 有効化後に PR 側マージ執行を畳むか~~ — **すべて Merge Queue 前提。個人アカウント所有リポでは使えないため消滅** (org へ移す判断をしたら復活する)

## Links

- 関連 ADR: [0036](merge-gate-as-required-check-and-pm-cadence.md) / [0040](project-continuity-three-layers.md) / [0035](role-split-across-agents-and-actions.md) / [0031](agent-reaches-outside-via-github-actions.md)
- Runbook: [merge-queue.md](../../../runbooks/merge-queue.md) — **Merge Queue が使えないと判明した後は「将来 org へ移したら読む手順書」**であり、現行運用の手順ではない
- 経緯: Issue [#269](https://github.com/yomote/mind-inbox/issues/269) (queue 有効化 → **使えないと判明 → strict OFF に切り替えてクローズ**。訂正コメントに一次情報がある)
- 追随の実装バグ: [#323](https://github.com/yomote/mind-inbox/issues/323)
