# AGENTS.md

Codex など **`AGENTS.md` を読むエージェント**向けの作業規約。
Claude Code は [`CLAUDE.md`](CLAUDE.md) を読む。**両者の内容は一致していること** —
食い違ったら CLAUDE.md が正典で、こちらを直す。

このファイルは「実装するときに効くルール」に絞ってある。プロセス全体 (design-gate /
debrief / セッション運用) は CLAUDE.md 側。

## PR レビューの書き方

- **レビューコメントはすべて日本語で書くこと。** このリポジトリの読者 (PO・開発セッション) の作業言語は日本語。severity ラベル (P1/P2 等) や識別子はそのままでよい
- 指摘には根拠 (該当コード・再現条件) を含める。修正の提案はあれば添える
- このリポジトリのレビュー対応フロー: 指摘 → 実装側が修正 push + スレッド返信 → **再レビュー依頼 (`@codex review`) → あなた (レビュアー) の再レビューが OK を出してから** PM がスレッドを resolve する。resolve の操作は PM が行うが、**解消の判定はレビュアーのもの**。スレッドはあなたが開いたままでよい
- **再レビューでは、過去の指摘が解消されたかを必ず判定すること** — 解消済みならその旨を明記し (例: 「前回の P1 (…) は解消を確認」)、未解消・不十分なら同じ指摘を再提起する。何も言わずに新しい指摘だけを返すと、解消判定ができない

## このプロダクトについて

日本語で対話しながら「困りごと」を吐き出し、AI が構造化して蓄積する Web アプリ。
構成は BFF (Azure Functions + tRPC) / フロント (React + Vite) / AI Agent (Python FastAPI) /
VOICEVOX (音声合成)。詳細は [README.md](README.md) と
[`docs/design/basic_design.md`](docs/design/basic_design.md)。

## 言語

**成果物は日本語で書く。** PR タイトル・本文、コミットメッセージ、コードコメント、
ドキュメント、Issue コメント。コード中の識別子は英語。

## コマンド

```bash
npm run test:fast   # bff / frontend / ai-agent / scripts を並列。**PR を出す前に緑にする**
npm run lint        # eslint + ruff + markdownlint
npm test            # test:contract → test:fast → test:e2e
```

アプリ別:

```bash
npm --prefix apps/bff run build                 # BFF は npm
pnpm --dir apps/frontend install                # フロントは **pnpm** (npm ではない)
pnpm --dir apps/frontend test
uv run --directory apps/services/ai-agent pytest   # Python は uv
```

`cicd/scripts/` 配下の Python テストは `npm run test:scripts` に登録する
(登録しないと CI で走らない)。

## テストの規律 (ここが一番よく破られる)

正典: [`docs/testing/strategy.md`](docs/testing/strategy.md)。層は **契約 / 単体 / スモーク / E2E**
の 4 つで、新規テスト名に `[契約]`/`[単体]`/`[スモーク]`/`[E2E]` を前置する (旧 `[L0]`〜`[L3]` からの
移行中 — 読み替えは §6)。実装時に効く規律は次の 4 つ。

- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない。** その 1 文を
  テストコードのコメントに必ず残す (Python は docstring、TypeScript は直前のコメント —
  正典 [`docs/testing/strategy.md`](docs/testing/strategy.md) §1.2 は言語非依存の「コメント」要件)
- **単体テストを書いてよいのは「壊れても例外が出ず、データが静かに間違う」ところだけ。**
  受け渡し・ルーティング・型の詰め替えには書かない。派手に落ちるものは実環境の通し
  (ゴールデンパス) が守る
- **状態・副作用を持つ新モジュールは、判定を純粋関数に切り出してテストする。**
  シェルや workflow の中に判定を埋めない。テストが書けない構成は設計の警報
- **テストが本当に効くか確かめる (ミューテーション)。** 判定の 1 行を壊してテストが
  落ちることを確認してから「テスト済み」と言う。データの文字列を assert しているだけの
  テストは、ロジックが壊れても気づけない

## 絶対に破らない規律 — 取れなかったものを「異常なし」と書かない

このリポジトリで最も繰り返している事故がこれ。実例:

- ログ取得のクエリが失敗しているのに「エラー無し」と表示した
- コストの API が `null` を返しているのに合計 `0.00` (= 0 円) と表示した
- `job.status == success` を「復旧した」と解釈したが、実際は guard で skip されていた

**取得・検証に失敗したら、成功と区別できる形で出す** (`未検証: 理由` / status を error に
する / run を落とす)。握り潰し (`2>/dev/null`、`|| true`、空の catch) を足すときは、
**それで何が見えなくなるか**をコメントに書く。

## ドキュメント

正典: [`docs/documentation/strategy.md`](docs/documentation/strategy.md)。

- **アーキテクチャに関わる判断は ADR を先に書く** — `docs/adr/` に MADR 形式。
  エージェント起案は `Status: Proposed` で入れる (`Accepted` にできるのは user だけ)
- **Accepted の ADR 本文は書き換えない。** 状態が変わったら Status 行だけ更新するか、
  新規 ADR で supersede する。索引 `docs/adr/README.md` も同じコミットで同期する
- **生成物は手書きしない** — OpenAPI は zod / pydantic から再生成、構成図はスクリプトから
  再生成。UI 仕様は `.mdx` が真実で、乖離したら実装を直す
- **運用手順は `docs/runbooks/` に集約** (README に書かない)

## 触ってはいけないもの

- **`Status: Accepted` の ADR 本文** (上記)
- **`apps/frontend/src/api/mockApi.ts` を「テストごとに別 mock」に増やすこと** —
  mock 兼テスト fixture として 1 つに保つ (ADR 0004)
- **生成物の手書き** — `docs/cicd/iac/infra_arch.svg`、OpenAPI スキーマ
- **stub fallback の破壊** — `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` 未設定でも BFF は
  動くこと (ローカルで外部サービス無しに触れる特性)
- **秘密の追加** — 長期クレデンシャルをリポジトリにも CI にも置かない。外部アクセスは
  OIDC (Actions) か device-code (対話) で取る

## PR の出し方

本文は次の見出しを埋める。

```markdown
## Summary        目的 / 変更 / 影響 / リスク / 結論
## Changes        何を変えたか
## Verification   下の表
## Review focus   特に見てほしい点
## Known limitations  未検証・未対応を正直に
```

`## Verification` は**「設定した」ではなく「振る舞い」**で書く。

| Check | Result |
| --- | --- |
| Unit / contract | 実行したコマンドと結果 |
| E2E | 同上 / 対象外なら「対象外」 |
| Live verification | 実環境で叩いた結果。**やっていないなら「未検証」と書く** |

**回していないものを PASS と書かない。** 未検証は未検証と書けば通る。

## マージの門

`main` はブランチ保護がかかっており、required check が揃わないとマージできない。

- `review-gate` — PM の受け入れコメント / レビュースレッド全解決 が揃うと緑
- `test (...)` / `lint-and-build`
- ブランチが `main` より遅れていると弾かれる → `main` を取り込んでから push

**未解決のレビュースレッドを残したままマージしない。** 直せない指摘は、なぜ直さないかを
返信し、必要なら Issue に切り出してから解決する。

## 迷ったら

- 仕様が無いのにテストを書こうとしている → **「仕様が無い」と言う。** 勝手に仕様を発明しない
- 判断がアーキテクチャに及ぶ → **実装を止めて Proposed の ADR を書く**
- 不可逆な操作 (DB スキーマ破壊的変更 / 外部サービス追加 / 公開 API の形 / データ削除)
  → **実装せず Issue に質問を積む**
