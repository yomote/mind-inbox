# CLAUDE.md

**このファイルはエージェント向けの作業規約**。プロダクトの説明・構成・起動手順は [README.md](README.md) が入口。

> **`AGENTS.md` との関係**: Codex など `AGENTS.md` を読むエージェント向けに、実装時に効く規約だけを抜き出した [`AGENTS.md`](AGENTS.md) を置いている。**このファイルが正典**で、規約を変えたら AGENTS.md も同じ PR で直す。

## このファイルに何を書くか

**全セッションが毎ターン読む**ので、置いてよいのは 1 種類だけ:

> **破っても気づけない / 壊れてから気づくルール。**

それ以外には、払うタイミングの違う置き場がある:

| 置き場 | 何を置くか | 払うとき |
| --- | --- | --- |
| 領域別 `CLAUDE.md` | その領域を触るときだけ要る不変条件 | そのディレクトリのファイルを触ったとき |
| skill | トリガーのある手順 | その作業を始めたとき |
| subagent (`.claude/agents/`) | 自分では気づけない審査 | 呼んだとき (新品の文脈) |
| SessionStart hook | **このセッションが何者か** | セッション開始時 |

**役割をここに書いてはいけない。** このファイルは窓口 PM も子セッションも subagent も Codex も同じものを読むので、「あなたは窓口 PM です」と書くと子が自分を PM だと思う。役割の判定は [`.claude/hooks/session-start.sh`](.claude/hooks/session-start.sh) が渡す。体制の全体像は [`docs/team.md`](docs/team.md)。

**ここに足すときは、既存の 1 行を消すか、上の置き場に移せないかを先に考える。**

## 全セッション共通

- **成果物は日本語で書く** — PR タイトル・本文、コミットメッセージ、コードコメント、ドキュメント、Issue コメント。コード中の識別子は英語
- **取れなかったものを「異常なし」と書かない** — このリポジトリで最も繰り返している事故。取得・検証に失敗したら成功と区別できる形で出す (`未検証: 理由` / status を error にする / run を落とす)。握り潰し (`2>/dev/null` / `|| true` / 空の catch) を足すときは、**それで何が見えなくなるか**をコメントに書く
- **「設定したか」ではなく振る舞いで書く** — 自動テストが緑でも「動かせば見つかる」層は残る。実際に叩いた結果を PR に貼る
- **判定の 1 行を壊してテストが落ちることを確認してから「テスト済み」と言う** — データの文字列を assert しているだけのテストは、ロジックが壊れても気づけない
- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない** — 仕様を指せないテストも書かない。「仕様がない」と言う
- **実行状態 (計画・進捗) の真実は GitHub Issues** — docs は「なぜ/何を」、Issues は「いつ/誰が/今どこ」。**open Issue にはちょうど 1 個の `stream:*` ラベル**を付ける (`product` / `improve-loop` / `concept` / `factory` / `infra`。迷ったら `product`)
- **自動化の生死は <https://yomote.github.io/mind-inbox/status/>** で見る (GitHub の実データから毎回生成。手書きの台帳は作らない)。**自動化を足したら [`watchers.json`](cicd/scripts/status-page/watchers.json) に 1 行足す。足せないなら作らない**

## 索引

### 作業に入る前に呼ぶ skill

| skill | いつ |
| --- | --- |
| `/dev` | ローカルで起動する / ブラウザで確かめる / テスト・lint を回す |
| `/adr` | ADR を書く / 採番する / Status を動かす |
| `/dispatch` | 作業を分ける / 子セッション・subagent を起こす |
| `/merge` | PR を出したあと / マージしてよいかを判断する |
| `/design-gate` | 新機能・Phase 着手・ADR 級判断の**実装を始める前** |
| `/debrief` `/briefing` | マージや Proposed ADR が溜まった / リリース級の節目 |
| `/release-gate` | リリース PR (`main → release`) の Go/No-Go |
| `/status` `/explain` `/po-feedback` | 戦況図 / 「あれなんだっけ」 / 指示の出し方の講評 |

### 領域を触るときに読まれる CLAUDE.md

- [`apps/frontend/CLAUDE.md`](apps/frontend/CLAUDE.md) — pnpm / `mockApi.ts` / MDX が真実 / E2E の置き場所
- [`apps/bff/CLAUDE.md`](apps/bff/CLAUDE.md) — stub fallback / zod が真実 / 環境変数 / 認証の門
- [`cicd/CLAUDE.md`](cicd/CLAUDE.md) — 2-phase Bicep / デプロイ / コストと公開面で覆さない前提

### 真実の所在

- [`docs/team.md`](docs/team.md) — 誰が何を担って回っているか (PO / 窓口 PM / 子 / subagent / judge / Actions)
- [`docs/design/basic_design.md`](docs/design/basic_design.md) — 構成と責務
- [`docs/testing/strategy.md`](docs/testing/strategy.md) — 4 層のテスト階層 / 書く・書かない判断基準
- [`docs/documentation/strategy.md`](docs/documentation/strategy.md) — 真実の所在マトリクス / 生成物 commit ルール
- [`docs/adr/README.md`](docs/adr/README.md) — アーキテクチャ判断の不変記録 (21 本)。**運用・プロセスの決め事は ADR ではない** — 過去に ADR として書かれた 29 本は [`docs/adr/archive/`](docs/adr/archive/README.md) にあり、現行ルールではない
- [`docs/runbooks/`](docs/runbooks/README.md) — 運用手順 (README や CLAUDE.md には書かない)
- [`docs/debrief/journal.md`](docs/debrief/journal.md) — セッション記録
