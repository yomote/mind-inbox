# AGENTS.md

Codex など GitHub 連携エージェント向けの作業指示。Claude Code 向けの規約は [CLAUDE.md](CLAUDE.md)、プロダクトの説明は [README.md](README.md)。

## PR レビューの書き方

- **レビューコメントはすべて日本語で書くこと。** このリポジトリの読者 (PO・開発セッション) の作業言語は日本語。severity ラベル (P1/P2 等) や識別子はそのままでよい
- 指摘には根拠 (該当コード・再現条件) を含める。修正の提案はあれば添える
- このリポジトリのレビュー対応フロー: 指摘 → 実装側が修正 push + スレッド返信 → PM セッションが検証してスレッドを resolve する。**resolve は人間側 (PM) の責務**なので、レビュアーはスレッドを開いたままでよい

## リポジトリの前提 (レビュー時に踏まえること)

- テスト戦略: [docs/testing/strategy.md](docs/testing/strategy.md) — L0〜L4 の階層、「無いと何が静かに通るか」を書けないテストは書かない
- UI 仕様は MDX が真実 ([ADR 0005](docs/adr/0005-mdx-ui-spec-as-truth.md))、型は tRPC の zod / pydantic が真実 (OpenAPI は生成物)
- アーキテクチャ判断は [docs/adr/](docs/adr/README.md) に不変記録がある。覆す提案をする場合は該当 ADR を参照すること
