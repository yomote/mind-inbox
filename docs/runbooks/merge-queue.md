# Merge Queue と pm-accept 引き継ぎ (review-gate) の運用

## Trigger

- main の merge queue を有効化 / 設定変更 / 一時停止する (needs-human Issue #269 の設定作業)
- review-gate の「pm-accept 引き継ぎ」(ADR 0042) の挙動を確認・切り分けする
- queue に入れた PR が check 待ちのままタイムアウト脱落する

## Prerequisites

- リポ admin (queue の有効化は web UI のみ = PO の宿題)
- required check を出す workflow に `merge_group:` トリガーが入っていること (PR で導入済み: `test.yml` の test / lint-and-build、`review-gate.yml`、`auto-improve-guard.yml`、`adr-number-guard.yml`)

## Steps

### Merge Queue の有効化 (PO / web UI)

1. Settings → Rules → Rulesets → main の ruleset を開き、**Require merge queue** を有効化する
2. 初期値 (ADR 0042 未決 — 実測で調整): merge method = **squash** / 並行ビルド数は既定 / check タイムアウトは CI 実測 (test ≒ 15 分) より十分長く
3. required status checks は現状維持 (`review-gate` / `test (L0 / L1+L2 / L3 / L3-real)` / `lint-and-build` 等) — 各 check は merge_group でも報告される
4. queue が「追いついてから check してマージ」を保証するため、**Require branches to be up to date (strict)** は外してよい (ADR 0042 未決 — この設定作業とセットで判断)

以後のマージ操作は「Merge when ready」(auto-merge 相当) — PR 側の要件が揃うと queue に入り、サーバー側で直列にマージされる。

### pm-accept 引き継ぎの読み方

受け入れ (`[pm-accept] <sha>`) 後に main 追随の push が積まれたときの review-gate status:

- 🟢 `OK: pm-accept を <sha> から引き継ぎ (差分不変)` — 追加コミットが main からのマージのみ + 実装差分 (Files changed) が受け入れ時点と同一
- 🔴 `... (引き継ぎ不成立: <理由>)` — 理由ごとの対応:
  - `実装差分が受け入れ時点から変化` → 実装が変わっている。**新しい head SHA で pm-accept を取り直す** (正常動作)
  - `<sha> が base からのマージでない` → 実装コミットか別ブランチの取り込みが混ざった。同上
  - `第一親で辿れない` / `一意解決できない` → rebase / force-push でコミットが書き換わった。同上
  - `実装差分を取得しきれない (files 300 件超)` / `コミット 250 件超で判定不能` → API の打ち切りで判定不能。PR を分割するか、head SHA で受け入れ直す

## Verification

- [ ] queue に入れた PR で 5 つの required check が merge group SHA に報告される (PR の Checks タブ → merge group の行)
- [ ] review-gate の commit status が merge queue の required check として数えられている (ADR 0042 動作検証 1 — **statuses API 由来の check の queue 互換は初回に必ず実測**)
- [ ] 受け入れ済み PR に `Update branch` (merge) をしても review-gate が緑のまま、description に「引き継ぎ」が出る
- [ ] 受け入れ後の実装 push で従来どおり赤に戻る

## Rollback

1. queue を止める: ruleset の Require merge queue を無効化 (web UI / PO)。`merge_group:` トリガーは発火しなくなるだけで、PR 側のフローは従来どおり動く
2. 引き継ぎ判定に誤緑の疑いが出たら: `cicd/scripts/review-gate/check.py` の `compute_carryover` 呼び出しを revert する PR を出す (引き継ぎ無し = ADR 0036 の厳格な失効規則に戻る)

## Common Issues

### queue に入れた PR が check pending のままタイムアウト脱落する

- 原因: required check の workflow に `merge_group:` トリガーが無い (新しい check を required に足したときに起きがち。paths フィルタ付き workflow は特に — paths は merge_group に効かないので、常に走って常に結論を出す作りにする)
- 対処: 該当 workflow に `merge_group:` を足す。adr-number-guard / auto-improve-guard の書き方を参考に。**このとき信頼境界を必ず確認する** (ADR 0042 D3 / 次項)

### merge_group を足した workflow が queue ref のコードを write 権限で実行している

- 原因: merge_group の checkout は queue の一時 branch = **PR が改変したコード**。`pull_request` イベントと違い fork でも GITHUB_TOKEN が read-only に落ちないため、write 権限 (statuses / pull-requests 等) を持つ job で PR 由来のスクリプトを実行すると門の迂回 (偽 status) や改ざんに使える (PR #271 Codex P1)
- 対処: リポジトリのスクリプトを実行する job は `ref: main` の信頼版 checkout で実行するか (review-gate の `gate-merge-group` job が例)、write を落として投稿系は別 job に分離する (`test.yml` の `pr-comment` job が例)

### queue 有効化後、マージが二重に走らないか

- review-gate には**マージ執行**もある (ADR 0040 D1 / #253 — 受け入れ済み + auto-merge 武装済みの PR を workflow 自身がマージする)。merge_group の run は執行しない (`gate-merge-group` は `contents: write` を持たない) ので queue と衝突しない。PR 側経路 (`gate-trusted` / sweep) の執行は queue 有効化後も残る — auto-merge の有効化がそのまま queue 投入になるため実害は無い想定だが、**実測で二重・競合が見えたら ADR 0042 D4 の未決として 0040 D1 側を畳む**
- ストール検知 (全緑 2h 未マージ / needs-human・Proposed ADR の 48h) は queue の外側の失敗モードなので、queue 有効化後も残す

### merge group で review-gate が 🔴「merge_group ref から PR を解決できない」

- 原因: queue の一時 branch ref (`gh-readonly-queue/<base>/pr-<N>-<sha>`) の形式が変わったか、想定外の ref。安全側で failure にしている (静かに通さない)
- 対処: run ログの ref を確認し、`cicd/scripts/review-gate/check.py` の `MERGE_GROUP_REF_RE` を実際の形式に合わせて修正

### 追随マージしただけなのに「実装差分が受け入れ時点から変化」で赤

- 原因: main 側が PR と同じファイルに触れた (コンフリクトしなくても patch の文脈 / blob が変わる)。保守的に不成立へ倒す設計 (ADR 0042 D1)
- 対処: 差分を確認して問題なければ新しい head SHA で pm-accept を出す

## Related

- ADR: [0042 pm-accept 引き継ぎ + Merge Queue](../adr/0042-pm-accept-carryover-and-merge-queue.md) / [0036 マージの門](../adr/0036-merge-gate-as-required-check-and-pm-cadence.md)
- スクリプト: `cicd/scripts/review-gate/`
- needs-human: Issue #269 (queue 有効化)
