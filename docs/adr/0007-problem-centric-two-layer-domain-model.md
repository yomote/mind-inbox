# 0007. 困りごとを Problem 集約とする 2層ドメインモデル (Mention → Problem)

- Status: Accepted
- Date: 2026-06-22
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: プロダクト化フェーズの外部設計 — [`requirements.md`](../design/requirements.md) / [`use_cases.md`](../design/use_cases.md) / [`domain_model.md`](../design/domain_model.md)

## Context and Problem Statement

PoC ([`basic_design.md §4`](../design/basic_design.md)) の集約ルートは **Session** で、困りごとは `OrganizedResult.priorities: string[]` に畳まれ、**セッションを跨いで同一物として残らない**。このため concept_deck §1 の中核課題「同じ悩みを何度も話している / 継続テーマが見えない」を解けない。

プロダクト化 (v1) では、困りごとを**セッションを跨いだ追跡対象**として扱うドメインモデルを決める必要がある。これは basic_design §4 のデータモデルを覆す構造的判断なので、実装前に記録する。

## Decision Drivers

- セッションを跨いだ蓄積・継続テーマの可視化 (concept_deck の中核価値)
- 重複の回避 — 同じ悩みが別レコードとして無限に増えない
- 繰り返しの重点化 — 頻出する悩みを強く扱える
- occurrence ごとに変わる属性 (感情・切迫感) の置き場
- 後からまとめ直せること — グルーピング精度を上げても作り直せる
- 編集権はユーザーが持つ (concept_deck「共同編集」)

## Considered Options

- Option A: **Session 中心モデルを維持** (PoC のまま。困りごとは `priorities: string[]`)
- Option B: **Problem 単層** (Problem を第一級にするが、各セッションの生記録は残さず Problem を直接更新)
- Option C: **2層 Mention → Problem** (PagerDuty のインシデント管理に倣い、生の観測 Mention を Problem に束ねる)

## Decision Outcome

Chosen option: **"Option C" (2層 Mention → Problem)**。

生の観測 (**Mention**) を不変・追記専用で残し、似た Mention を **Problem** に束ねる構造が、Decision Drivers をまとめて満たす唯一の案だから。Session は「Mention を生む / 既存 Problem を再点火するイベント」に格下げする。

**付随する判断 — グルーピング方式**: 意味類似による **自動グルーピング + 事後トリアージ** (PagerDuty 式) を採る。対案「寄せる前に毎回ユーザー確認」は、蓄積体験が承認の摩擦で続かなくなるため不採用。編集権は事後トリアージ (分割 / 統合 / 棚卸し) で担保し、concept_deck の共同編集と両立させる。この判断の帰結として、承認待ちの `candidate` 状態は廃し Problem は `open` 直行で生成される。

**スコープ外**: ラベルのテーマ体系 (主テーマ1つ + 自由タグ、固定7分類 + 未分類) は**安く変更可能な周辺**なので本 ADR では固定せず、[`domain_model.md §2.4`](../design/domain_model.md) に委ねる。定期的な自動再グルーピング (スケジューラ依存) はプロアクティブ・フェーズに切り出す ([`requirements.md §2.2`](../design/requirements.md))。

### Positive Consequences

- セッションを跨いだ蓄積・継続テーマの可視化が成立する
- 重複が増えず、繰り返しを `Problem.mentions` 数で重点化できる
- 感情など時間変化する属性を Mention に正しく置ける (感情の推移が時系列で追える)
- Mention が不変なので、グルーピングを後からより良いロジックで作り直せる
- 承認ゲートが不要になり、ためる体験の摩擦が減る

### Negative Consequences

- エンティティが増え、実装・永続化が複雑化する (Mention / Problem の2系統 + グルーピング処理)
- 自動グルーピングは誤りうる — 事後トリアージ UI が前提になる
- basic_design §4 / 既存 PoC 実装 (`priorities[]`, `HistoryItem`) と非互換 — 移行が要る
- 意味類似判定 (embedding 等) の追加依存が生じる

## Pros and Cons of the Options

### Option A: Session 中心モデルを維持

PoC のまま、困りごとを `priorities: string[]` として扱う。

- Good, because 実装変更がゼロ
- Bad, because 中核価値 (継続テーマの可視化) を解けない
- Bad, because 同じ悩みが毎回別レコードになり重複が増える

### Option B: Problem 単層

Problem を第一級にしてセッションを跨いで蓄積するが、生の観測は残さず Problem を直接更新する。

- Good, because セッション跨ぎの蓄積は実現でき、モデルが単純
- Bad, because 生の観測を捨てるため「今月3回」等の再出現履歴・感情の推移が失われる
- Bad, because 後からのまとめ直し (再グルーピング) ができない

### Option C: 2層 Mention → Problem (採用)

生の観測 Mention を不変で残し、自動グルーピングで Problem に束ねる。

- Good, because 蓄積・重複回避・繰り返し重点・感情推移・まとめ直しを同時に満たす
- Good, because PagerDuty のインシデント管理で実証されたパターン
- Bad, because エンティティ増による複雑さと、自動グルーピング誤りのトリアージ前提

## Links

- 設計: [`requirements.md`](../design/requirements.md) / [`use_cases.md`](../design/use_cases.md) / [`domain_model.md`](../design/domain_model.md)
- 覆す対象: [`basic_design.md`](../design/basic_design.md) §4 (データモデル) / §10 (拡張ロードマップ) — 本 ADR 承認後に更新する
- 着想: PagerDuty incident management (Alert → Incident のグルーピング / デデュープ)
- 戦略: [docs/documentation/strategy.md](../documentation/strategy.md)
