# デブリーフ journal

> `design-gate` / `debrief` セッションの記録。仕組みは [ADR 0014](../adr/0014-design-comprehension-gate-and-debrief.md)。
>
> 役割は 2 つ: (1) `debrief` skill の「前回以降」の**起点マーカー** (最新エントリの日付を使う)、(2) user の決定と学びの**累積ログ** (解説の深さ調整・振り返りに使う)。

## エントリ形式

新しいエントリを**このセクションの直後 (ファイル上部)** に追記する:

```markdown
## YYYY-MM-DD — {design-gate | debrief}

- **対象**: {扱った設計 / PR / ADR の一覧}
- **決定**: {ADR NNNN Accept / Reject / 修正、design-gate の承認・修正点}
- **学びメモ**: {user が曖昧だった箇所 → どう解説し直したか。次回の解説の深さ調整に使う}
- **持ち越し**: {未消化の項目・次回に回した判断。無ければ「なし」}
```

---

## 2026-08-07 — po-feedback (初回)

- **対象**: レビューエージェント基盤の構築セッション (`claude/security-review-agent-9avoqw`、ADR 0015 一式)
- **👍**: 問題を incentive 構造で設定 (「実装者はリリース側に流れる」) / 段階的具体化でちゃぶ台返しゼロ / 「毎回走ると無駄」のコスト感覚
- **👎**: 音声由来の曖昧語 (「東京だったら」「衛生的な機能」等) にエージェントが解釈を明示したが**未応答のまま実装が進んだ** / 1 クリック宿題の滞留 (ブランチ保護が未設定 = ゲートの強制力がまだ無い) / スコープ宣言の不在 (release-judge R0 は PO の「入るもの/入らないもの」宣言がないと UNKNOWN が並ぶ)
- **📌 次の期間で 1 つだけ**: エージェントの「〜と解釈しました」に、合ってる/違うの**一言だけ**返す
- **持ち越し**: ブランチ保護の設定、ADR 0015 の Accept/Reject、リリース PR 運用の初回実践

## 2026-08-06 — design-gate

- **対象**: issue #69「SWA Free 化 + Functions EasyAuth(Entra 自分限定) + CORS + 予算アラート」。根拠 ADR: [0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md)
- **決定**:
  - **承認** — この設計で実装に進む。あわせて ADR 0013 を `Proposed` → `Accepted`、ADR 0009 を `Superseded by 0013` に更新
  - 「自分限定」の強さ: **A. 単一テナント限定のみ**（テナントに実質本人しか居ないため、設定が最小で壊れにくい方を採る）
  - 予算アラート: **月 ¥3,000**、通知先はアカウントのメール。actual 50% / forecast 80% / actual 100% の 3 段
  - ローカル開発: **認証なし**（Functions Core Tools に EasyAuth が無く、ローカル BFF は自機内のみ）
- **学びメモ**: 理解確認で「第三者が Functions を直接 curl したら?」に対し **「CORS がブロックするので届かない」** を選択 → **CORS はブラウザ側の規約であって認可ではない**（curl/Postman はヘッダを無視して到達する。CORS の役割は "他人のサイトの JS がログイン中のブラウザを踏み台にするのを防ぐ" こと）を解説し直した。実際に止めるのは EasyAuth の 401。次回この領域を扱うときは「守りの層がどこにあるか」を先に図で固定してから細部に入る
- **持ち越し**: EasyAuth 有効時に **CORS preflight (OPTIONS) が 401 になる既知リスク**の実測。実デプロイ時に未認証 401 とあわせて確認し、preflight が弾かれる場合は `globalValidation.excludedPaths` などの回避を runbook に追記する
