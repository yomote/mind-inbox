# 0047. 調査用 read-only ID は保護のないブランチに紐づける (トレードオフを明示して受容する)

- Status: Proposed
- Date: 2026-08-12
- Deciders: PO (yomote), PM セッション
- Consulted: Codex (PR #222 レビュー), PM セルフレビュー (PR #222)
- Informed: —

Technical Story: <https://github.com/yomote/mind-inbox/pull/222> / <https://github.com/yomote/mind-inbox/issues/209>

## Context and Problem Statement

Azure を「読む」だけの確認 (リソース一覧・コスト・ログ) に、これまで書き込み権を持つデプロイ SP
(`main` 限定のフェデレーション資格情報) が必要だった。読むために main へマージする往復が発生し、
2026-08-10 の 1 セッションで 6 往復した。#209 はこれを read-only の識別に分離する。

ところが GitHub の標準のフェデレーション資格情報は**サブジェクトの完全一致**しか受け付けない
(`claude/*` のようなワイルドカードは不可)。そのため調査用の識別は `ops/inspect` という
**専用ブランチ 1 本**に紐づけることになる。しかもこのブランチは「調査のたびにエージェントが
直 push して dispatch する」ための場所なので、**PR 必須のブランチ保護を掛けると用途そのものが
成立しない**。結果として「保護のないブランチに push できること」＝「サブスクリプション スコープの
Reader / Cost Management Reader / Log Analytics Reader を取得できること」になる。

これは構成上のトレードオフであり、[CLAUDE.md](../../CLAUDE.md) の「アーキテクチャに関わる判断は
ADR を先に書く」に該当する。PR #222 のレビューで指摘され、記録が無いまま進めないために本 ADR を置く。

## Decision Drivers

- **読むだけの確認に書き込み権を要求しない** (最小権限 — #46 と同じ方向)
- **調査のループを止めない** — 1 回の確認に PR レビュー往復を要求すると #209 の動機が消える
- **read-only であっても機微データは通る** — Log Analytics には ai-agent の例外詳細や相談ログが載りうる
- **「読める」と「壊せる」を混ぜない** — この識別で書き込みができてはならない

## Considered Options

- Option A: `ops/inspect` に PR 必須のブランチ保護を掛ける
- Option B: 保護なしのまま、トレードオフを明示して受容する (本 ADR)
- Option C: read-only 識別そのものを作らず、従来どおり main 経由で読む

## Decision Outcome

Chosen option: **"Option B"**, because 用途 (調査のたびに直 push) と保護 (PR 必須) は両立せず、
かつこの識別で得られるのは**読み取りのみ**だから。ただし「read-only だから安全」で済ませず、
下の制約を条件として付ける。

### 受容の条件 (この ADR が成立する前提)

1. **この識別には書き込み系のロールを一切付けない** — `ro.sh` が付けるのは Reader /
   Cost Management Reader / Log Analytics Reader の 3 つだけ。増やす場合は本 ADR を改訂する
2. **`ops-inspect` workflow は書き込み操作をしない** — `az` は show/list のみ、`curl` は GET のみ、
   入力は `env:` 経由でのみ参照する (既存の規律。workflow 冒頭に明記されている)
3. **人間が実行するスクリプトはブランチ名で取らない** — `ro.sh` は PO 自身の Azure 権限
   (device-code ログイン / ADR 0006) の下で走るため、**commit sha で固定して取得する**。
   ブランチ名で取ると、このブランチに push できる者が中身を差し替えた瞬間に
   read-only の枠を超えて PO の全権限で任意コマンドが走る (この経路が本 ADR で唯一の「壊せる」穴)
4. **リポジトリの write 権限を持つ主体が増えたら再判断する** — 現状は PO 1 人 +
   その委任で動くエージェントセッションのみ。共同作業者が増えた時点で本 ADR は前提を失う

### Positive Consequences

- 読むだけの確認が **main へのマージ無しで**回る (#209 の動機を満たす)
- 書き込み権を持つデプロイ SP を調査用途に使わなくて済む — #46 (ロールスコープ最小化) と同じ方向に進む
- 「この識別で何が取れるか」がロール 3 つに固定され、レビューで追える

### Negative Consequences

- **リポジトリに push できる主体は、read-only の Azure 資格情報を実質的に取得できる。**
  Log Analytics 経由で相談ログ・例外詳細まで読める点を含めて受容する
- ブランチ保護が無いため、`ops/inspect` の履歴は誰の目も通らずに書き換わりうる (調査ログとしての証拠力は低い)
- 条件 3 (sha 固定) は**運用で守る規律**であり、機械的に強制されていない。`ro.sh` を
  ブランチ名で取る運用に戻ると穴が開く

## Pros and Cons of the Options

### Option A: `ops/inspect` に PR 必須のブランチ保護を掛ける

- Good, because 資格情報に到達する経路にレビューが挟まる
- Good, because 調査ブランチの履歴が証拠として残る
- Bad, because **調査のたびに PR が要る** — 「読むだけの確認に往復が要る」という #209 の問題がそのまま戻る
- Bad, because 保護の設定は web UI からしか行えず、エージェントは自分で復旧できない

### Option B: 保護なしのまま、トレードオフを明示して受容する (採用)

- Good, because 調査のループが止まらない
- Good, because 得られる権限が読み取り 3 ロールに限定され、影響範囲が言い切れる
- Bad, because push 権限 = read-only 資格情報の取得権限になる
- Bad, because 安全性が「規律を守ること」に依存する (条件 3)

### Option C: read-only 識別を作らない

- Good, because 新しい資格情報が増えない
- Bad, because 読むために書き込み権を持つ SP を使い続けることになり、最小権限から遠ざかる
- Bad, because #209 の実測 (1 セッションで 6 往復) がそのまま残る

## Links

- PR: <https://github.com/yomote/mind-inbox/pull/222>
- Issue: <https://github.com/yomote/mind-inbox/issues/209> / 関連: <https://github.com/yomote/mind-inbox/issues/46>
- Runbook: [azure-oidc-cd-setup.md](../runbooks/azure-oidc-cd-setup.md)
- 関連 ADR: [0006](0006-azure-access-via-device-code.md) / [0031](0031-agent-reaches-outside-via-github-actions.md) / [0035](0035-role-split-across-agents-and-actions.md)
