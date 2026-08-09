# 0028. UX プローブ記録は artifact ではなく Issue コメントで採点セッションへ運ぶ

- Status: Proposed
- Date: 2026-08-09
- Deciders: omoteforlab (運搬方法は 2026-08-09 の対話で選択肢形式により選択。Accept は debrief で)
- Consulted: —
- Informed: —

Technical Story: 2026-08-09、#156 の毎朝の UX 採点 Routine を登録した直後に、その Routine が入力を取得できないことが判明した。ADR 0027 D1 は記録の置き場を workflow artifact と決めていたが、**agent セッションからは artifact をダウンロードできない**。実装 (#154) は人間の手元を前提に書かれていたため、無人化の完遂条件が満たせない状態だった。

## Context and Problem Statement

毎朝の採点ループは「golden-path-monitor が記録した会話 JSON を judge に渡す」ことで始まる。記録は workflow artifact に保存されており、取得手段は `gh run download` を前提にしていた。ところが Claude Code on the web の agent セッションでは、その取得経路が二重に塞がっている:

- `gh` / `api.github.com` への直接アクセスが 403 (`GitHub access is not enabled for this session`)。Claude GitHub App は All repositories 権限でインストール済みでも変わらない。cloud session は資格情報をサンドボックス内に置かない設計のため、**設定では解けない** (#160)
- GitHub MCP の `download_workflow_run_artifact` は署名付き URL を返すが、その取得先 `*.blob.core.windows.net` が環境の egress ポリシーで拒否される (`connect_rejected: gateway answered 403 to CONNECT`)

つまり **artifact という置き場そのものが、採点する主体から到達できない**。記録が存在していても採点が始まらないので、ADR 0027 D1 の「毎朝の無人採点」は成立しない。

## Decision Drivers

- **agent だけでループが閉じる** — 人間の介在なしに記録が judge に届くこと (無人化の目的そのもの)
- **egress の追加許可を要求しない** — `*.blob.core.windows.net` の全許可は Azure Blob 全体への穴になる
- **既存の蓄積パターンと揃える** — 採点結果は既にスコアボード Issue #127 のコメントとして貯めている
- **障害調査の一次情報を失わない** — 生の記録は人間が後から掘れること

## Considered Options

- Option A: 環境の許可ドメインに `*.blob.core.windows.net` を足し、artifact 運搬を維持する
- Option B: **golden-path-monitor が記録 JSON を蓄積 Issue のコメントとして投稿し、採点セッションは GitHub MCP で読む**
- Option C: 採点自体を GitHub Actions のジョブ内へ寄せる (workflow 内なら token も artifact も普通に扱える)

## Decision Outcome

Chosen option: **"Option B"**。2026-08-09 に PO が選択肢形式で選択。

- `golden-path-monitor.yml` に投稿ステップを足し、記録 JSON を `kind: "ux-probe-record"` の封筒に入れて蓄積 Issue [#162](https://github.com/yomote/mind-inbox/issues/162) へ 1 run = 1 コメントで投稿する
- 採点セッションは最新コメントを GitHub MCP で読み、封筒から記録を取り出して judge に渡す
- **artifact 保存は併存させる** (90 日)。人間が障害調査で生の記録を掘る経路として残す。記録の「正」はコメント側 (採点が読む先)
- 封筒の整形と取り出しは `cicd/scripts/ux-probe/probe-record-comment.py` に同居させる。投稿側と読み取り側は別プロセス・別日に動くため、片側だけ変わっても気づけない。往復テスト (L1) で乖離を止める
- 投稿に失敗しても monitor は赤くしない。この workflow の役目はゴールデンパスの死活であって記録の運搬ではないため。代わりに warning annotation を残し、翌朝の採点セッションが「材料なし」を報告することで二重に気づく

Option A は今の設計を維持できるが、egress をワイルドカードで開ける代償が運搬方法の都合に見合わない (しかもホスト名 `productionresultssaNN` は可変で、狭い許可では足りない)。Option C は構造的にこの種の問題を消せるが、judge は Claude セッションを要するため起動方法から再設計になる。まず B で無人ループを成立させ、C は必要が出たときに別 ADR で判断する。

### Positive Consequences

- 採点ループが agent だけで閉じる。egress の追加許可も `gh` も要らない
- 蓄積の形が #127 (採点結果) と #162 (素材) で揃い、どちらも MCP で読める
- 記録が 90 日の artifact 保持を超えて残る (時系列を後から辿れる)

### Negative Consequences

- 会話全文が Issue に永続する。プローブは合成シナリオなので機密性の問題は無いが、実ユーザーの発話を扱うようになったら再判断が要る
- 記録が 1 コメントの上限 (65536 文字) を超えると投稿できない。往復数やシナリオを増やすと現実的な制約になる (超過時は投稿せず warning を残す — 切り詰めた JSON を流さない)
- 蓄積 Issue に人間が返信すると、最新コメントを記録として読む前提が壊れる (Issue 本文で明示的に禁じている)

## Links

- 運用手順: [runbooks/ux-probe-judge.md](../runbooks/ux-probe-judge.md)
- 前提としていた設計: [ADR 0027](0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) D1 (無人採点ループ) / 実装 #154
- 制約の調査と選択肢: [#160](https://github.com/yomote/mind-inbox/issues/160)
- 蓄積先: 素材 [#162](https://github.com/yomote/mind-inbox/issues/162) / 採点結果 [#127](https://github.com/yomote/mind-inbox/issues/127)
- 独立 judge の原則: [ADR 0019](0019-independent-judge-agents-security-qa-release.md) / 動作検証: [ADR 0018](0018-runtime-verification-in-the-loop.md)
