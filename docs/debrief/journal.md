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

(まだエントリなし — 初回の debrief では全期間を対象にする。ADR 0014 自体が最初の承認対象)
