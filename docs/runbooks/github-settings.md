# GitHub の設定を宣言から点検 / 適用する (スマホから)

## Trigger

- リポジトリの設定 (ブランチ保護 / required check / secret scanning / push protection / Dependabot / CodeQL) が**今どうなっているか知りたい**とき
- `cicd/github/settings.yml` を編集して、**その内容を実際のリポジトリに反映したい**とき
- 新しいリポジトリに同じ設定を作りたいとき (`settings.yml` をコピーしてこの workflow を置く)
- **#318 で「人にお願いしていたクリック作業」はこれに置き換わる** — Web UI のチェックボックスを 1 つずつ押す代わりに、`settings.yml` を PR で直して `apply` を 1 回流す

> **エージェントはこの操作をできない。** リポジトリ管理系 API はエージェントのセッションから届かない (実測 403 "GitHub access is not enabled for this session.")。これはガードレールなので迂回しない。だからここは **PO 本人が承認タップする** 設計になっている。
>
> **これが現役の仕組み。** [#390](https://github.com/yomote/mind-inbox/issues/390) が「この 5,221 行は Terraform provider の再実装」と判定し、宣言の Terraform 化が `cicd/github/terraform/` で着工しているが、**まだ `terraform plan` すら回せていない** (認証が未決定 / provider 取得が egress で塞がれている)。移行の状態は [`github-terraform.md`](github-terraform.md)。**点検・適用はこの Runbook で行う。**

## Prerequisites

- スマホのブラウザで GitHub にログインできること (承認に使う)
- このリポジトリの admin 権限 (自分のリポジトリなら持っている)
- **何も準備しなくてよいもの**: PAT の発行 / secret の登録 / OAuth アプリの作成 / PC。トークンはどこにも保存しない

## Steps

### A. 今どうなっているかを見る (`check` — 読むだけ)

1. GitHub アプリまたはブラウザで **Actions** タブ → 左の一覧から **`github-settings`** をタップ
2. 右上の **`Run workflow`** をタップ
3. 出てくるフォーム:
   - **Use workflow from**: `main` のまま (**`main` 以外だと最初のステップで止まる**)
   - **mode**: `check`
   - 他は空のまま
4. **`Run workflow`** をタップ → 一覧に新しい run が出るのでタップして開く
5. **run の Summary の一番上に、コードがカード (annotation) で出る** — `ワンタイムコード: XXXX-XXXX`
   - 出るまで数秒。**ログを開いて探す必要はない** (探させる作りで一度失敗している — #362)
   - 出ない場合は run が赤で終わっているはず。黙って待たない作りなので、赤の理由がそのまま Summary に出る
6. 別タブで <https://github.com/login/device> を開く → コードを貼る → **Authorize** をタップ
   - 端末のロック解除 / 2 要素認証を求められたら普通に通す
7. run に戻る。認証できていれば数十秒で終わる
   - 承認待ちの間、ログに `承認待ち… コード XXXX-XXXX / 残り N 秒` が定期的に出る (沈黙しない)

> **窓口 PM (エージェント) に頼む場合**: コードは annotation なので**走行中でも API から読める**。
> 「コードを教えて」と言えば PM が読んで貼れる (生ログは完了後しか取れないので、これが唯一の経路)。
>
> ⚠️ **承認は必ずリポジトリ所有者のアカウントで行うこと。** `user_code` は public な
> run ログに出るため第三者も読めます。別アカウントで承認された場合、workflow は
> **設定を 1 つも読まずに赤で終わります** (トークンの持ち主を `GET /user` で照合しているため)。
>
> ⚠️ **run が終わってもトークンの認可は残ります。** workflow が消すのは runner 上の
> コピーだけで、GitHub 側の grant は生きています (public client なので revoke API を
> 叩けない)。完全に切るには **Settings → Applications → Authorized OAuth Apps →
> 「GitHub CLI」を revoke** してください (ローカルの `gh` ログインも切れます)。
> 同意画面に「GitHub CLI」と出るのは、gh CLI と同じ公開 client_id を使っているためです。

**結果の見方:**

| 見る場所                                                                 | 何が分かるか                                                             |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| run の **Summary**                                                       | 差分の表・適用計画・**ダイジェスト (12 桁)**・比較していない領域         |
| データブランチ `data/github-settings` の `snapshots/<owner>/<repo>.json` | **今の実際の設定** (事実)。`git log -p` で「いつ何が変わったか」が読める |
| Issue (`[github-settings]` で始まるもの)                                 | **ズレがあったときだけ**立つ。無ければ Issue も立たない                  |

### B. 宣言を直す (差分を埋めたいとき)

1. 差分の表を見て、**宣言 (`cicd/github/settings.yml`) が正しいのか、現実が正しいのか**を決める
2. 現実が正しい (= 宣言が間違っていた) なら、`settings.yml` を PR で直す。**それで終わり** (apply は要らない)
3. 宣言が正しい (= 現実を直したい) なら、次の C へ

### C. 適用する (`apply`)

1. **直前に `check` を流しておく** (Summary に出た**ダイジェスト 12 桁**が要る)
2. Actions → `github-settings` → **`Run workflow`**
   - **Use workflow from**: `main`
   - **mode**: `apply`
   - **plan_digest**: check が出した 12 桁を貼る
   - **allow_weakening**: 差分の「向き」列に **弱める** が 1 つでもあり、それを承知で進めるときだけ ON
3. A と同じように device-code の承認をする (**apply でも毎回認証する** — トークンを保存していないから)
4. run が終わったら Summary を読む
   - 「適用の結果」に **適用済み / 失敗 / 未適用** が 1 行ずつ出る
   - 適用後に**もう一度読み直して**差分が消えたことを確認している (消えていなければ run は赤くなる)

## Verification

- [ ] run が緑 (赤なら Summary の `::error::` 行を読む)
- [ ] Summary の「差分」が 0 項目
- [ ] 「未検証」が 0 項目 (1 つでもあれば赤くなる — 読めなかった設定を「一致」とは書かない)
- [ ] `data/github-settings` ブランチに新しいコミットが立っている (**設定が変わったときだけ**立つ。変わっていなければコミットは立たない — それが正常)
- [ ] `[github-settings]` の Issue が閉じている (ズレが解消すると自動で閉じる)

## 🚨 緊急解除 — 門が壊れて誰もマージできないとき

**`enforce_admins: true` (2026-08-21 / #387 の PO 裁定 A) を入れた以上、この節が唯一の脱出口です。**

⚠️ **「宣言を弱める PR を出して apply する」は成立しません。** この workflow は
**main の版でしか動かない** (上の「main の版であることを確認する」ステップ) ので、
弱める宣言を適用するには**まずその PR を main にマージ**する必要があり、
マージできないから困っている場面では循環します。

⚠️ **門は 2 枚あります。片方だけ外しても通れません** (PR #512 Codex P1):

| 層                            | 実体                                                                                                                                            | 宣言で管理しているか                                                                     |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **ruleset**                   | **実際にマージを止めているのはこちら** (#373 が `405 Repository rule violations found / Required status check "review-gate" is failing` を実測) | **していない** — 中身が未取得 ([`rulesets.tf`](../../cicd/github/terraform/rulesets.tf)) |
| **classic branch protection** | `enforce_admins` はこちら。管理者バイパスを塞ぐ                                                                                                 | している (`settings.yml`)                                                                |

**main を一切変えずに、2 枚とも一時的に外す**のが正しい手順です (PO 本人の admin 権限が要る):

### 開ける

1. **ruleset を Disable にする** — `Settings` → `Rules` → `Rulesets` → 対象の ruleset →
   `Enforcement status` を **`Disabled`** に。(ruleset id が分かっていれば API でも可。
   **id は未取得**なので、まず `gh api "repos/yomote/mind-inbox/rulesets"` で引く)
2. **classic の管理者バイパスを開ける** — `Settings` → `Branches` → `main` のルールの
   `Edit` → **`Do not allow bypassing the above settings` のチェックを外す**。API なら:

   ```bash
   gh api -X DELETE "repos/yomote/mind-inbox/branches/main/protection/enforce_admins"
   ```

3. **壊れた門を直す PR をマージする**

### 必ず戻す (2 枚とも)

4. **ruleset の `Enforcement status` を `Active` に戻す**
5. **classic を戻す** — `github-settings` workflow を `mode=check` → `mode=apply` で流す。
   宣言 (`cicd/github/settings.yml`) は `enforce_admins: true` のままなので、
   **apply が管理者バイパスを塞ぎ直します**。確認:

   ```bash
   gh api "repos/yomote/mind-inbox/branches/main/protection/enforce_admins" --jq '.enabled'  # true
   ```

### 戻し忘れをどう検出するか — **半分しか機械が見ていない**

- **classic 側は機械が見ます。** 宣言を `true` のままにしてあるので、戻し忘れると
  次の `check` が「差分あり」で赤くなります。**だから宣言は書き換えない** —
  書き換えると*開けた状態が「正常」として記録され*、戻し忘れが静かに続きます
- 🔴 **ruleset 側は誰も見ていません。** `settings.yml` に ruleset の宣言は無く
  (中身が未取得 / #373・#390)、`check` の比較対象にも入りません。**Disabled のまま
  放置しても、どのレポートも赤くなりません** — 手順 4 を飛ばすと**門が開いたまま
  静かに残ります**。ruleset が宣言に入るまでは、ここは**規律で持つしかない**箇所です。
  開けたときは Issue を 1 本立てて、戻したらそれを閉じること

## Rollback

- **`apply` を途中で止めたい** → run を Cancel する。**操作は 1 つずつ独立して完了する**ので「操作の途中」で止まることはない。何が適用済みかは Summary の「適用の結果」に出る
- **適用した設定を戻したい** → `settings.yml` を元に戻す PR を出し、`check` → `apply` をもう一度流す。保護を弱める向きになるので **allow_weakening** が要る
- **`git log -p` で戻す先が分かる** → データブランチ `data/github-settings` の履歴が「いつどう変わったか」の記録

## Common Issues

### `main` の版だけを実行できます、と言われて止まる

- 原因: **Use workflow from** に `main` 以外を選んだ。特権トークンを握る workflow なので、他ブランチの (書き換えられた) 定義では動かさない
- 対処: `main` を選び直す

### コードを入力する前に run が失敗した / 承認せずに放置した

- 原因: 承認の待ち時間は最大 10 分。承認しなければそこで終わる
- 対処: **何も変更されていない** (認証が最初のステップなので、以降は 1 つも走らない)。もう一度 `Run workflow` する

### コードのカードが出ない

- **これは「まだ出ていない」ではなく異常**。コードを取得できない時点で run は赤で終わる作りになっている (#362)
- 見る場所: Summary のエラー。`device flow が失敗しました: <GitHub の言い分>` か `ワンタイムコードを出せませんでした` が出る
- よくある原因: `client_id` が無効化された等。差し替えるときは **`cicd/scripts/github-settings/device_login.py` を PR で直す**
  - **リポジトリ変数では差し替えられない** (意図的)。設定画面から承認先を変えられると、特権 workflow の宛先を diff にもレビューにも残さずに別の OAuth App へ向けられるため (#366 のセキュリティレビュー major)

### `ダイジェストが一致しません` と出て何も適用されない

- 原因: `check` を流したあとに **宣言か現実が変わった**。「PO が見た差分」と「今から適用する差分」が違うので止めている (これは仕様)
- 対処: `check` をもう一度流し、**新しく出た差分を読んでから**、新しいダイジェストで `apply` する

### `保護を弱める操作が N 件あります` と出て何も適用されない

- 原因: 宣言が現実より**弱い**。宣言の書き間違いで保護が静かに外れるのを防いでいる
- 対処: 表の「向き = **弱める**」の行を読む。意図どおりなら **allow_weakening** を ON にして流し直す。意図と違うなら `settings.yml` を直す

### `N 項目を読み取れませんでした` で run が赤い

- 原因: その API に権限が無いか、その機能がこのリポジトリ / プランで使えない (例: CodeQL default setup が 403)
- 対処: 読めないものを「一致」と書くわけにいかないので赤いまま。**どうしてもこの環境では扱えない機能**なら、`settings.yml` のその項目を `unmanaged` にする。比較も適用もしなくなる代わりに、レポートに毎回「管理対象外」として名前が出る (黙って見ないのとは違う)

### required check を足したら PR が永久に pending になった

- 原因: **その base への PR で走らない check** を required にした。`test.yml` は `main` への PR でしか走らず、`iac-validate` / `adr-number-guard` は paths フィルタ付き
- 対処: `settings.yml` の `contexts` からその名前を外して `apply`。context 名は **job の `name:` そのもの** — job 名を変えるときは `settings.yml` も同じ PR で変える

## Related

- Issue: [#344](https://github.com/yomote/mind-inbox/issues/344) (この仕組み) / [#318](https://github.com/yomote/mind-inbox/issues/318) (置き換わるクリック作業)
- ADR: [0046 環境を宣言から作り直せるものにする](../adr/0046-environment-rebuildable-from-declaration.md) (Azure 側) / [0041 観測データを git のデータブランチへ](../adr/archive/operations/ux-observations-on-git-data-branch.md) (state を Issue に溜めない) / [0006 device-code を主とする](../adr/0006-azure-access-via-device-code.md)
- 宣言 (意図): [`cicd/github/settings.yml`](../../cicd/github/settings.yml)
- スクリプト: `cicd/scripts/github-settings/` (判定は `settings_diff.py` の純関数 / I/O は `sync.py`)
- 関連 Runbook: [`status-page.md`](status-page.md) (この workflow の生死を見る場所)
