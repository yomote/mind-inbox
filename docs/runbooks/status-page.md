# 状況ページ — 「今どれが動いていて、何が問題か」を 1 枚で見る

> **URL**: <https://yomote.github.io/mind-inbox/status/>
>
> 判断の背景: 2026-08-10、PO の「自動化の仕掛かり中が多すぎて把握できない / 今何が起きて
> いるかが動的に分からない」。原因は数ではなく**置き場所**だった。判断は ADR、手順は
> Runbook、進捗は Issue にあるのに、「**この自動化は今も動いているのか**」だけどこにも無い。

## 設計の芯 — ページは状態を持たない

過去に試して駄目だった 2 つを避けている。

| やり方                                 | なぜ駄目だったか                                                                                                                                    |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| セッションで作る HTML (報告会スライド) | **静的なスナップショット**。作った瞬間に古くなり、更新にエージェントの起動が要る                                                                    |
| docs に置く手書きの表                  | **Excel と同じ**。維持する人がいないと腐る。しかも実行状態を docs に置くのは [ADR 0011](../adr/0011-github-projects-as-execution-dashboard.md) 違反 |

このページは **毎回 GitHub の実データから組み立て直す**。手で書く欄はゼロで、状態を保存する
場所も持たない。ページは真実ではなく**生成物**。

- 定義 (何を見張るか): [`cicd/scripts/status-page/watchers.json`](../../cicd/scripts/status-page/watchers.json)
- 組み立て: [`cicd/scripts/status-page/build.py`](../../cicd/scripts/status-page/build.py)
- 実行: [`.github/workflows/status-page.yml`](../../.github/workflows/status-page.yml)
- UX トレンド節: データブランチ `data/ux-observations` から描く ([ADR 0041](../adr/0041-ux-observations-on-git-data-branch.md) D6)。
  PM tick の採点追記はページ再生成のトリガーではないため、採点直後は次の生成
  (毎朝 07:10 JST か手動) まで反映されない

## いつ作り直されるか

- 毎朝 07:10 JST (毎朝の実環境チェックの直後)
- `deploy` / `golden-path-monitor` が終わったとき
- main に push があったとき
- 手動 (`workflow_dispatch`)

置き場所は gh-pages の **`/status/` 配下だけ**。ルートには 2026-07 のフロント配信物が
残っているので触らない。

## プロダクトの現在地 (ページ冒頭 / Issue #280)

2026-08-11 の PO 要望「いつでも開けば、できているもの / 進行中 / 次にやることが見えて、
指させる場所」。自動化の生死と同じ規律 (状態を持たず GitHub の実データから毎回生成) で、
組み立ては [`cicd/scripts/status-page/product_status.py`](../../cicd/scripts/status-page/product_status.py)。

| 欄                    | 何から作るか                                                                                                                             |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 今週の目標            | open milestone のうち**期限が直近の 1 件** + 紐づく Issue/PR の消化状態。無ければ「未設定」— **今週の目標は milestone を切って表現する** |
| いま dev で触れるもの | `deploy.yml` の run 履歴。「dev は何時の commit の状態か / 以降何本のマージが未反映か」。deploy 赤なら ⚠️ + ci-failure Issue へのリンク  |
| 進行中                | open PR (base=main) を機械分類 — 変更ファイルに `apps/` を含めば**プロダクト**、それ以外は**工場** (開発体制)。各行に review-gate の色   |
| 🙋 あなたの番         | `needs-human` ラベルの open Issue + `Status: Proposed` の ADR (main の内容から)                                                          |
| 次の候補              | `P1` ラベルかつ ci 系ラベル無しの open Issue を**作成日昇順で 5 件** (= PM の優先案)。並べ替えの指示は Issue #280 へ                     |

運用上の注意:

- **新しい API を叩くようになったら `.github/workflows/status-page.yml` の `permissions` に 1 行足す** — `permissions` は書いた瞬間に**書かなかったものが `none`** になる。足し忘れるとローカルでは通るのに本番だけ 403 になり、ページは落ちずに「未検証」を並べる (静かに情報が消える)。現在の必要権限: `contents: write` (gh-pages) / `actions: read` (run 履歴 + run の jobs) / `issues: write` (失敗 Issue) / `pull-requests: read` (進行中) / `statuses: read` (review-gate の色)
- **一覧 API は必ず `--paginate` で取る** — PR / Issue の一覧は 1 ページ (最大 100 件) で切れ、**51 件目以降が「無かった」ことになる**。`--paginate` + `--jq` はページごとに jq を適用するので `[...]` で包まず、`.[] | {...}` の 1 行 1 オブジェクトにして `build.gh_objects` で畳む
- 変更ファイルが取得できなかった PR は工場に黙って混ぜず「未検証」として別枠に出す (files も `--paginate` で全ページ取得 — 100 件超の PR で `apps/` を取りこぼさないため)
- review-gate の ❓ は 2 種類 — 「(未評価)」= status がまだ貼られていない / 「(未検証)」= 取得失敗
- 「以降 N 本のマージが未反映」は deploy.yml の **push イベントの run** で数える (手動の up/down は数えない)
- **run の緑 ≠ デプロイした**。deploy.yml は run success でも実デプロイが走らない経路 (guard skip = OIDC 未設定 / `AUTO_DEPLOY_ENABLED` 未設定、手動 `down` = 撤収) を持つ。そこで run の jobs API から `Provision + deploy (up)` の痕跡を見て、**2 つを別々に**決める — 「dev に載っている commit」= 同ステップが **success で完走**した最後の成功 run / 「直近のデプロイが通ったか」= 同ステップが **走った (success or failure)** 最新の push run の結果。後者を分けないと、**失敗の次の push が guard skip で緑になった瞬間に ⚠️ と ci-failure Issue へのリンクが消える**
  - 撤収が最後なら「⚠️ dev は撤収されています」、guard skip だけが続くなら「実デプロイの痕跡がありません」— いずれも「反映済み」とも「赤」とも書かない
  - **「デプロイ経路に入った」痕跡は deploy.yml の no-op marker step が持つ** (`デプロイ経路に入った (marker)` — guard 通過直後・Azure login より前)。Azure login や IMAGE_TAG 解決で落ちると `Provision + deploy (up)` は skipped になり、marker が無いと「試みて失敗した」と「そもそも走らなかった」を区別できない (deploy が赤なのにページだけ緑になる)
  - **`deploy.yml` のステップ名を変えたら `product_status.py` の `DEPLOY_STEP` / `TEARDOWN_STEP` / `MARKER_STEP` を追随させる** (deploy.yml 側にも同じ注意をコメントで置いてある)
  - 赤は保守側に保持する — 失敗した push があり、それより新しい「デプロイできた痕跡」が無い間は、guard skip の緑が何本続いても ⚠️ を消さない (jobs API を追加で叩かず、取得済み run だけで判定)
  - deploy.yml の該当ステップ名を変えたら `product_status.py` の `DEPLOY_STEP` / `TEARDOWN_STEP` も直すこと

## 記号の読み方

| 記号 | 意味                                                  |
| ---- | ----------------------------------------------------- |
| 🟢   | 直近の実行が成功していて、期待周期の中にいる          |
| 🔴   | 直近が失敗、または期待周期を過ぎても痕跡が無い        |
| 🟡   | 動いてはいるが遅れている                              |
| ❓   | **生死を確かめる方法が無い** (動いていない、ではない) |

**❓ がこのページのいちばん大事な欄**。動いたときに痕跡を残さない自動化は、沈黙と正常が
同じ見え方になる。❓ を消す方法は 1 つだけ — その自動化に「**動いたら痕跡を 1 つ残す**」を
足すこと。異常時だけ喋る設計にしない。

見出しが「**取得できませんでした**」のときは、表の緑を信用しないこと。生成そのものが
失敗しており、「取れなかった」を「異常なし」と読ませないためにわざと最優先で出している
(`inspect-env.sh` / `ops-inspect` と同じ規律)。

## 監視項目を足す / 外す

`watchers.json` に 1 行足すだけ。**自動化を作ったらここに足す。足せないなら作らない。**

```jsonc
// CI (GitHub Actions)
{ "id": "foo.yml", "name": "表示名", "what": "何をするか", "expect_hours": 26 }

// Routine (claude.ai 側。痕跡の在り処で生死を測る)
{ "name": "表示名", "what": "…", "expect_hours": 26,
  "trace": { "kind": "issue_comment", "issue": 127 } }
```

`trace.kind` は 4 種類 — `issue_comment` (指定 Issue の最新コメント) /
`issue_label` (ラベル付き Issue の最新更新) / `issue_title` (タイトル一致の最新 Issue) /
`data_branch` (UX 観測データブランチ `data/ux-observations` の `record_kind` 別の最終追記
— ADR 0041。workflow が fetch して `UX_DATA_DIR` で渡す)。
痕跡の在り処が決められないものは `"kind": "unknown"` にして `note` に理由を書く
(❓ として表に出る。**表から消さない** — 消すと存在ごと忘れる)。

## 今わかっている宿題 (2026-08-10 時点)

ページが ❓ を出している 2 本は、いずれも心拍が無いのが原因。

- **cd-watchdog** — 全緑のときは何も残さない設計。「全緑でも心拍だけ残す」に変えると判定できる
- **PR レビュー Routine** — 痕跡の在り処が未定義。次の PR で 1 回確認して、埋めるか畳むかを決める

畳む推奨として PO に上げているもの (未裁定): ux-judge / release-gate / UX 改善ループ 段3。
いずれも「動いていない or 未着手」。

## Common Issues

- **URL が 404** — リポジトリ設定で GitHub Pages のソースが `gh-pages` ブランチになっているか確認する (Settings → Pages)。エージェントからは変更できないので `needs-human`
- **ページが更新されない** — `status-page` ワークフローの run を見る。`変化なし — push しません` なら内容が同じだけで正常
- **全部 ❓ になっている** — `gh` が認証できていない。ワークフローの `GH_TOKEN` を確認する
- **一部だけ「(未検証: …)」になる** — `gh --jq` は**スカラー文字列をクォート無し**で返す (jq -r と同じ)。`| max` を裸で渡すと JSON として読めず、痕跡があるのに未検証になる。必ず `{t: (… | max)}` の形でオブジェクトに包むこと (初回 run で実際に踏んだ。`cicd/scripts/status-page/test_build.py` が回帰を止めている)

## 関連

- [ADR 0011](../adr/0011-github-projects-as-execution-dashboard.md) (実行状態の真実は GitHub 側) / [ADR 0018](../adr/0018-runtime-verification-in-the-loop.md) (動作検証をループに組み込む)
- 個別の運用: [cd-watchdog](cd-watchdog.md) / [ux-probe-judge](ux-probe-judge.md) / [review-agents](review-agents.md) / [ops-inspect](ops-inspect.md)
