# 0020. 人間の確認は選択肢形式で出し、人間宿題は needs-human キューに一元化する

- Status: Accepted (debrief #3, 2026-08-08) — 一部改訂: Positive Consequences の「`/status` と board の両方から見える」のうち **board 側は [ADR 0044](0044-stream-lanes-as-the-project-map.md) で退役** (2026-08-11)。現行の可視面は `/status` (status ページへの戦況図の描画は #289 待ち)。**選択肢形式・needs-human キューという本 ADR の核は不変**で、あわせて 0044 D3 が「1 回 3 件・レーン文脈つき」の量的規律を追加
- Date: 2026-08-07
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: po-feedback 初回 (journal 2026-08-07) — 解釈確認の未応答・1 クリック宿題の滞留・「何を見ればいいか分からない」問題への対策。

## Context and Problem Statement

エージェント駆動開発では、人間 (PO) の応答が必要な場面 — 解釈の確認、設計の選択、1 クリック設定、ADR の Accept — が高頻度で発生する。しかし現状、これらは**長い返信の散文に埋まって**流れており、PO が見逃す。po-feedback 初回で実測された通り、解釈確認 4 件が未応答のまま実装が進み、ブランチ保護 (ゲートの強制力そのもの) が数時間放置された。**人間の注意力を責めても解決しない — 確認の「形式」と「置き場」を仕組み化する必要がある**。

## Decision Drivers

- **応答コストの最小化** — 読む量を減らし、答える動作をワンタップにする
- **見逃しの構造的排除** — 「散文のどこかに質問がある」状態をなくす
- **溜まり場の一元化** — 未応答の宿題が会話ログに分散せず、1 箇所で見える (ADR 0011「実行状態 = Issues」に整合)
- **既存機構の再利用** — 新しいツール/サービスを増やさない

## Considered Options

- Option A: **選択肢 UI (AskUserQuestion) + `needs-human` Issue キュー + `/status` 先頭表示** の 3 点セット
- Option B: 確認専用の別セッション / ディスカッションスレッドを都度立てる
- Option C: 現状維持 + PO の注意力運用 (返信テンプレの徹底)

## Decision Outcome

Chosen option: **"Option A"**。3 つの規約で構成する:

1. **選択肢で聞く** — エージェントは、解釈の確認・設計の選択・承認を求めるとき、散文に埋めず **AskUserQuestion ツール (クリック選択式 UI)** で出す。原則: (a) 質問は選択肢化して出す (自由記述は Other に逃がす)、(b) 推奨がある場合は先頭に置き「(推奨)」を付ける、(c) 1 度に最大 4 問。会話の途中で聞けなかった確認は、返信の**最後に独立ブロック**でまとめる (本文中に散らさない)
2. **needs-human キュー** — 人間にしかできない宿題 (web UI の設定、ADR Accept、外部サービス操作、承認) は、発生した時点で **`needs-human` ラベルの Issue** に積む。会話で「お願いします」と言って終わりにしない。完了したら close (どちらが close しても良い)
3. **`/status` の先頭に「🙋 あなたの番」** — status レポートの冒頭に needs-human の残件と Proposed ADR の残数を必ず出す。PO は「何を見ればいいか」を `/status` 1 コマンドに集約できる

補助規約: 確認に**未応答のまま進む**場合 (無人セッション等)、エージェントは「未確認のまま進行」と明示し、確認事項を needs-human Issue または journal に記録する (静かに進まない)。

### Positive Consequences

- 応答が「読む → 探す → 文章で返す」から「見る → タップ」になり、HITL の摩擦が一桁下がる
- 未応答の確認が Issue として物理的に残り、`/status` と board (ADR 0011) の両方から見える
- 無人セッションの「Proposed ADR + Issue に質問を積む」規約 (ADR 0014) と同じ導線に乗る

### Negative Consequences

- 選択肢化はエージェント側の設計コストがかかる (雑な選択肢は誘導になる — 選択肢に「どれでもない」の逃げ道を必ず残す)
- 小さな宿題まで Issue 化するとノイズになる — 「そのターンで完了しない人間作業」だけを積む基準で運用
- AskUserQuestion は CLI/セッション UI 依存 — 環境が変わったら形式は変わり得る (規約の本質は「選択肢化 + 一元化」でツールではない)

## Pros and Cons of the Options

### Option A: 選択肢 UI + needs-human キュー + /status 先頭 (採用)

- Good, because 全部既存機構 (AskUserQuestion / Issues / status skill) の組合せで、新規インフラゼロ
- Good, because 「答える場所」「溜まる場所」「見る場所」が 1 つずつに定まる
- Bad, because エージェント側の規律 (散文に埋めない) に依存する部分が残る

### Option B: 確認専用の別セッション

- Good, because 議論スレッドとして文脈が独立する
- Bad, because 見る場所が増える (セッション一覧に確認スレが混ざり、結局見逃す)
- Bad, because ワンタップ応答にならない (セッションを開いて読んで書く)

### Option C: 現状維持 + 注意力運用

- Good, because 何も作らなくてよい
- Bad, because po-feedback 初回で既に破綻が実測されている (注意力は仕組みではない)

## Links

- 発端: [journal 2026-08-07 po-feedback](../debrief/journal.md)
- 関連 ADR: [0011 Projects=実行ダッシュボード](0011-github-projects-as-execution-dashboard.md) / [0014 理解ゲート+デブリーフ](0014-design-comprehension-gate-and-debrief.md) / [0019 独立 judge](0019-independent-judge-agents-security-qa-release.md)
