# 0026. CD の赤は毎時の watchdog Routine が検知し、診断と fix PR まで無人で進める

- Status: Accepted (briefing #4, 2026-08-09)
- Date: 2026-08-08
- Deciders: omoteforlab (方向は 2026-08-08 の対話で承認済み。Accept は briefing #4 で稼働中の実体を追認する形で取得)
- Consulted: —
- Informed: —

Technical Story: 2026-08-08、#131 (sha タグ差し替えデプロイ) の初回適用で deploy が赤になったが、赤を見て動く仕組みがなく、PO が実環境を触って「ずんだもんがしゃべらない」と体感で気付くまで放置された。しかも赤の原因は新設の稼働 revision 検証ステップ自体の引数バグで、そこで job が止まり、後続の golden-path 実測 (#111 で常設化済み) まで到達していなかった。

## Context and Problem Statement

デプロイ毎の実測 (smoke-test → golden-path → UI E2E) は ADR 0018 / #111 で既に CD に組み込まれており、「壊れたら deploy が赤くなる」検知層はある。しかし**赤くなった後に反応する主体が人間しかいない**。PO は常時 Actions を見ていないので、検知が機能していても「PO が気付いてから」の対応になり、検知層の価値が半減する。壊れたままの窓を最短で閉じるには、赤を検知して診断・修正を始める無人の反応層が要る。

## Decision Drivers

- **PO が気付く前に対応が始まる** — 検知から反応までを人間の注意力に依存させない
- **追加課金の回避** — ADR 0008 と同じく、サブスク枠の Routine で回す (API キー Actions を増やさない)
- **暴走しない** — merge しない / main に直接 push しない / 不可逆操作をしない。全緑なら何も残さない
- **重複対応しない** — 毎時発火するため、同じ赤に毎回新しい PR/Issue を積まないこと

## Considered Options

- Option A: **毎時の watchdog Routine** (新規セッション起動) が deploy / golden-path-monitor / build-images の直近 run を見て、赤なら診断 → 確実な修正は fix PR → プッシュ通知
- Option B: 赤の検知とプッシュ通知のみ (診断・修正は人間または PM セッション)
- Option C: workflow 失敗イベントから直接エージェントを起動する (event-driven)

## Decision Outcome

Chosen option: **"Option A"**。2026-08-08 に PO が選択肢形式で「診断 + fix PR まで」を承認。

- 毎時 (Routine の最短間隔) に新規セッションを起動し、対象 workflow の直近 run の conclusion を確認する。**全緑なら何も作らず終了する** (Issue/PR/コメントを残さない)
- 赤があれば: 既存の対応 (cd-watchdog ラベルの open Issue / 該当 run に言及する open PR) を先に検索し、未対応の場合のみ失敗ログから診断する。小さく確実な修正 (CD スクリプト / workflow / 設定のバグ等) は fix PR まで作る。**merge は人間** (ADR 0008 と同じ歯止め)
- 不可逆な判断 (インフラ削除・課金追加・公開 API の形・データ操作) は実装せず `needs-human` Issue に選択肢形式で積む (ADR 0020)
- 対応内容は cd-watchdog ラベルの Issue に記録し、Routine の完了通知 (プッシュ) で PO に届く

Option C (event-driven) が反応速度では理想だが、現行の Routine トリガーで workflow 失敗イベントを直接受ける構成が確認できていないため、まず毎時ポーリングで運用を始める (検知遅延は最大 1 時間。デプロイ自体の検証が赤を出すのは数分以内なので、実害は「反応開始」の遅延のみ)。event-driven 化は運用実績を見て別 ADR で判断する。

### Positive Consequences

- 「PO が気付いてから」が「Routine が先に診断を済ませている」に変わる
- 赤の原因が今回のような検証スクリプト自体のバグでも、無人で fix PR まで進む
- 追加課金なし (サブスク枠)。全緑の時間帯はセッションが即終了するだけ

### Negative Consequences

- 反応開始に最大 1 時間の遅延 (毎時ポーリングの上限)
- 毎時のセッション起動がサブスク枠を消費する (全緑なら数ターンで終わる軽い消費)
- 診断を誤った fix PR が積まれる可能性 (merge は人間なので実害はレビューコストまで)

## Links

- 運用手順 (Routine の中身・停止・再作成): [runbooks/cd-watchdog.md](../runbooks/cd-watchdog.md)
- 検知層: [ADR 0018](0018-runtime-verification-in-the-loop.md) / deploy.yml の smoke → golden-path → UI E2E (#111)
- 自動化の基盤選定: [ADR 0008](0008-pr-review-via-cloud-routine.md) (Routine / サブスク枠 / merge は人間)
- 人間の確認・宿題の形式: [ADR 0020](0020-hitl-choice-format-and-needs-human-queue.md)
- 発端のインシデント: #131 初回デプロイの赤 (稼働 revision 検証の引数バグ) と TTS 断線の体感発覚
