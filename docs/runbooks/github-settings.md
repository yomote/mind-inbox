# GitHub の設定を宣言から点検 / 適用する (スマホから)

## Trigger

- リポジトリの設定 (ブランチ保護 / required check / secret scanning / push protection / Dependabot / CodeQL) が**今どうなっているか知りたい**とき
- `cicd/github/settings.yml` を編集して、**その内容を実際のリポジトリに反映したい**とき
- 新しいリポジトリに同じ設定を作りたいとき (`settings.yml` をコピーしてこの workflow を置く)
- **#318 で「人にお願いしていたクリック作業」はこれに置き換わる** — Web UI のチェックボックスを 1 つずつ押す代わりに、`settings.yml` を PR で直して `apply` を 1 回流す

> **エージェントはこの操作をできない。** リポジトリ管理系 API はエージェントのセッションから届かない (実測 403 "GitHub access is not enabled for this session.")。これはガードレールなので迂回しない。だからここは **PO 本人が承認タップする** 設計になっている。

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
5. **`設定を点検する (check)`** ジョブを開き、**`device-code で認証する`** ステップを開く
   - ログに `First copy your one-time code: XXXX-XXXX` が出る (出るまで 20〜40 秒)
   - **この 8 桁をコピー**
6. 別タブで <https://github.com/login/device> を開く → コードを貼る → **Authorize** をタップ
   - 端末のロック解除 / 2 要素認証を求められたら普通に通す
7. run に戻る。認証できていれば数十秒で終わる

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

## Rollback

- **`apply` を途中で止めたい** → run を Cancel する。**操作は 1 つずつ独立して完了する**ので「操作の途中」で止まることはない。何が適用済みかは Summary の「適用の結果」に出る
- **適用した設定を戻したい** → `settings.yml` を元に戻す PR を出し、`check` → `apply` をもう一度流す。保護を弱める向きになるので **allow_weakening** が要る
- **`git log -p` で戻す先が分かる** → データブランチ `data/github-settings` の履歴が「いつどう変わったか」の記録

## Common Issues

### `main` の版だけを実行できます、と言われて止まる

- 原因: **Use workflow from** に `main` 以外を選んだ。特権トークンを握る workflow なので、他ブランチの (書き換えられた) 定義では動かさない
- 対処: `main` を選び直す

### コードを入力する前に run が失敗した / 15 分待って失敗した

- 原因: device-code の有効期限は 15 分。承認しなければそこで終わる
- 対処: **何も変更されていない** (認証が最初のステップなので、以降は 1 つも走らない)。もう一度 `Run workflow` する

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
- ADR: [0046 環境を宣言から作り直せるものにする](../adr/0046-environment-rebuildable-from-declaration.md) (Azure 側) / [0041 観測データを git のデータブランチへ](../adr/0041-ux-observations-on-git-data-branch.md) (state を Issue に溜めない) / [0006 device-code を主とする](../adr/0006-azure-access-via-device-code.md)
- 宣言 (意図): [`cicd/github/settings.yml`](../../cicd/github/settings.yml)
- スクリプト: `cicd/scripts/github-settings/` (判定は `settings_diff.py` の純関数 / I/O は `sync.py`)
- 関連 Runbook: [`status-page.md`](status-page.md) (この workflow の生死を見る場所)
