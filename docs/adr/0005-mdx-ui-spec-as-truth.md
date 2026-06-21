# 0005. UI 仕様は MDX を真実とし、実装が乖離したら実装を直す

- Status: Accepted
- Date: 2026-06-21
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: 既存実装の遡及的記録 (#11) — 判断自体はリポジトリ初期から下されている

## Context and Problem Statement

画面の挙動・フロー (どの画面からどこへ遷移するか、各状態で何を出すか) を、どこを
「真実」として管理するかを決める必要がある。コードを真実にすると仕様が読みづらく
レビュー時に「仕様が無いから判断できない」が起きる。一方、別ドキュメントを真実にすると
実装と乖離して腐る。コーディングエージェント駆動開発では、エージェントが参照できる
明文化された UI 仕様が特に重要になる。

## Decision Drivers

- 仕様の可読性 (画面挙動をレビュー時にドキュメントだけで判断できる)
- 実装との乖離検知 (仕様の型部分が腐らない仕組み)
- エージェントが参照できる single source of truth
- 仕様と実装が食い違った時の「どちらを直すか」の明確さ

## Considered Options

- Option A: **MDX (`docs/frontend/ui_specs/*.mdx`) を真実**とし、preview コンポーネントで型を守る
- Option B: 実装コードを真実とし、仕様は生成 or 後追い
- Option C: Figma 等の外部デザインツールを真実とする

## Decision Outcome

Chosen option: **"Option A" (MDX を真実)**。
MDX は説明文と preview 用 React コンポーネントを inline に書けるため、
**仕様が読み物として成立しつつ、preview コンポーネントを TS コンパイル対象にすることで
型部分が腐らない**。新規画面/挙動変更は MDX を**先に**直し、実装を追従させる。
実装と MDX が食い違った場合は **MDX が真実なので実装を直す** (API の OpenAPI とは
逆向きのルール: API は実装が真実)。Figma (Option C) はコードベース外で乖離検知が
効かず、エージェントからも参照しにくい。

## Positive Consequences

- 画面挙動がドキュメントとして読め、レビュー時に仕様だけで判断できる
- preview コンポーネントの TS コンパイル/render テストで仕様の型部分が腐らない (#10)
- 「どちらを直すか」が一意 (UI は MDX が真実 → 実装を直す)
- エージェントが参照できる UI の single source of truth が存在する

## Negative Consequences

- 挙動変更のたびに MDX を先に更新する規律が必要 (順序を破ると乖離する)
- MDX の preview コンポーネントと実コンポーネントは別物なので、二重メンテになる箇所がある
- 細かい CSS / pixel level のデザインは MDX の範囲外で、別途デザインツールが要る

## Pros and Cons of the Options

### Option A: MDX を真実 (採用)

説明 + preview コンポーネントを MDX に書き、TS コンパイルで守る。

- Good, because 仕様が読み物として成立し、型部分が腐らない
- Good, because 乖離時のルールが一意 (実装を直す)
- Bad, because MDX 先行更新の規律と preview の二重メンテが要る

### Option B: 実装を真実

コードを正とし仕様は後追い。

- Good, because 二重メンテがない
- Bad, because レビュー時に仕様が読めず「判断できない」が起きる
- Bad, because エージェントが参照できる明文化された仕様が無い

### Option C: Figma を真実

外部デザインツールを正とする。

- Good, because ビジュアルデザインの表現力が高い
- Bad, because コードベース外で乖離検知が効かず、エージェントから参照しにくい

## Links

- 仕様: `docs/frontend/ui_specs/*.mdx`
- preview: `apps/frontend/src/spec/previews/`
- 関連 ADR: [0004](0004-mockapi-as-frontend-truth.md) — モック fixture の真実は `mockApi.ts`
- 関連 issue: MDX を TS コンパイル/render で守る [#10](https://github.com/yomote/mind-inbox/issues/10)
- 戦略: [docs/documentation/strategy.md §4.1](../documentation/strategy.md)
