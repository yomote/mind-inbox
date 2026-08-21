# 0056. 層は「管理系 / アプリ系」で分け、データは RG 移動ではなくバックアップ + 復元実証で守る

- Status: Accepted (2026-08-15, PO 裁定)
- Date: 2026-08-14
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: [#302](https://github.com/yomote/mind-inbox/issues/302)（ライフサイクル分断）

関連: [ADR 0046](0046-environment-rebuildable-from-declaration.md)（**本 ADR が D1 を supersede する** — 2026-08-15 の Accept で発効。D2〜D10 は現行 — D9 本文の「持続層」は管理系 RG と読み替える）/ [ADR 0003](0003-two-phase-bicep.md)（2-phase Bicep）/ [ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md)（常設 dev / 予算）/ [ADR 0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md)（Cosmos 単一ストア）/ [ADR 0041](archive/operations/ux-observations-on-git-data-branch.md)（git データブランチ — **本 ADR はこの手法をユーザーデータには使わないと決める**）/ [ADR 0045](archive/operations/e2e-artifacts-are-secret-by-default.md)（「管理系 RG」の初出）/ [ADR 0018](archive/operations/runtime-verification-in-the-loop.md)（復元したことのないバックアップはバックアップではない）

> **Status について。** 本 ADR は **2026-08-14 の PO 口頭裁定**（窓口 PM セッション経由。
> 「[#302](https://github.com/yomote/mind-inbox/issues/302) の 2026-08-12 の設計対話を正とする」という指示）に基づく**エージェント起案**で、
> `Proposed` で入っていました（**Status を動かすのは PO の操作** — [`docs/adr/README.md`](README.md) / `/adr` skill Step 4）。
> **2026-08-15 の PO 裁定で `Accepted` へ遷移**し、これをもって
> [ADR 0046](0046-environment-rebuildable-from-declaration.md) **D1 の supersede が発効**しました
> （対応する実装は [#419](https://github.com/yomote/mind-inbox/pull/419)）。
> 遷移時に PO 承認のうえ、D1 へ**読み替えの 1 行**（0046 本文の「持続層に GPG 秘密鍵」）を追記しています。

## Context and Problem Statement

[ADR 0046](0046-environment-rebuildable-from-declaration.md) D1 は、リソースを「**持続層 = 消えると困るもの**（Cosmos / Azure OpenAI / Speech / Log Analytics / Key Vault / バックアップ保管）」と「環境層 = 壊して作り直すもの」に分けると決めた。しかしこの定義は、**[#302 で PO と交わした 2026-08-12 の設計対話（[issuecomment-5263080034](https://github.com/yomote/mind-inbox/issues/302#issuecomment-5263080034)）を取り込み損ねたまま** design-gate を通過したものだった。

その対話で PO が示した整理は別物である:

> 分けるべき軸は「共有かどうか」ではなく **管理系 / アプリ系**。
>
> - **アプリ系 RG** — OpenAI / Speech / **Cosmos** / Container Apps / Functions / SWA。**アプリそのもの**。使い捨てにしてよい
> - **管理系 RG** — Key Vault / Log Analytics / **バックアップ Storage**。**システムを運用するためのもの**で、環境の生死と無関係
>
> 消えて困るものを撤収から守るのではなく、**管理系 RG にバックアップを取って復元可能にする**。これで「アプリ RG は使い捨て」を貫ける。

ズレは実装に出た。[PR #412](https://github.com/yomote/mind-inbox/pull/412) は D1 に従って `main-shared.bicep` に Cosmos / OpenAI / Speech を宣言し、**その実装報告の中で「Issue のコメントと ADR が食い違うが、後から Accepted になった ADR を正とした」と自ら申告していた**。2026-08-14 に PO が裁定し、**#302 のコメントが正**と確定した。

したがって決めるべきことは 2 つ:

1. **層の軸は何か** — 「消えると困るか」か、「運用のためか / アプリそのものか」か
2. **消えて困るデータをどう守るか** — 別 RG へ逃がすのか、バックアップで戻せるようにするのか

制約として、**Azure には何も apply されていない**（#412 は宣言だけで実リソースを 1 つも作っていない）。**今なら移行コストゼロで正せる**が、apply 後は RG 間移動のダウンタイムと再結線が発生する。

## Decision Drivers

- **PO の原設計に従う** — 層の分け方はプロダクトの運用思想そのもので、実装者の裁量で選ぶものではない
- **「アプリ系は使い捨て」を本当に成立させる** — [ADR 0046](0046-environment-rebuildable-from-declaration.md) D9（週次プロビジョンテスト）が要求しているのは「壊して作り直せること」。消えて困るものを逃がして回避すると、逃がしたぶんだけ検証されない領域が増える
- **ユーザーデータを公開面に出さない** — このリポジトリは public で、Problem / Mention は PO 個人の悩みそのもの
- **「戻せる」を主張ではなく実測で言う**（[ADR 0018](archive/operations/runtime-verification-in-the-loop.md)）
- **逃げ道を常用させない** — 撤収ガードが常に拒否する状態になると override が既定運用になり、ガードが何も守らなくなる
- **クォータの制約を無視しない** — Cosmos 無料枠と Speech F0 は 1 サブスクに 1 つ

## Considered Options

- **Option A: 管理系 / アプリ系に分け、データはバックアップ + 復元実証で守る**（本 ADR の採用案 = PO の原設計）
- **Option B: [ADR 0046](0046-environment-rebuildable-from-declaration.md) D1 のまま**（持続層 = 消えると困るもの。Cosmos / OpenAI / Speech を別 RG へ逃がす）
- **Option C: 層は Option A だが、Cosmos の保護は Azure の継続バックアップ（PITR）に委ねる**

## Decision Outcome

Chosen option: **"Option A"**、because 層の軸を「運用のためか / アプリそのものか」に置くと、**アプリ系 RG をまるごと使い捨てにできる**（= [ADR 0046](0046-environment-rebuildable-from-declaration.md) D9 が検証したい対象を逃がさずに済む）。データは逃がすのではなくバックアップから戻す。

### D1 — 層は「管理系 / アプリ系 / デプロイ」で分ける

| 層             | 置き場所               | 中身                                                                                                  | 撤収            |
| -------------- | ---------------------- | ----------------------------------------------------------------------------------------------------- | --------------- |
| **管理系**     | `rg-mgmt-mindbox`      | Key Vault（+ E2E trace 復号鍵）/ バックアップ保管 Storage / Log Analytics / 予算                      | ❌ **触らない** |
| **アプリ系**   | `rg-{env}-mind-inbox`  | **Cosmos / Azure OpenAI / Speech** / Container Apps + managed environment / Function App + Plan / SWA | ✅ **使い捨て** |
| **デプロイ層** | （リソースを作らない） | image の sha 差し替え / zip deploy / 静的配信                                                         | —               |

**RG 名は「shared」ではなく「mgmt」**。「shared」は「環境をまたいで共有する」と読めてしまい、**それは #302 のコメントが明確に撤回した案**（環境をまたぐとユーザーデータが混ざる）。管理系 RG は 1 つだが、**リソース名は環境ごとに分ける**。

**Cosmos / OpenAI / Speech はアプリ系に残す。** これらは「アプリそのもの」であって運用の道具ではない。[ADR 0046](0046-environment-rebuildable-from-declaration.md) D1 がこの 3 つを逃がそうとしたのは「消えると困るもの」を層の軸にした結果であり、ライフサイクルの分断ではなかった。

**Entra のアプリ登録**はどの RG にも属さない（テナントのオブジェクト）。ライフサイクルとしては管理系と同じ扱い（[ADR 0046](0046-environment-rebuildable-from-declaration.md) D5 のまま）。

**アプリ系から管理系への参照はパラメータ渡し**（RG をまたぐ resource 参照はしない）。この点は [ADR 0046](0046-environment-rebuildable-from-declaration.md) D1 から変えない。

**読み替え（2026-08-15 の Accept 時に PO 承認のうえ追記）**: [ADR 0046](0046-environment-rebuildable-from-declaration.md) 本文の「持続層に**バックアップと GPG 秘密鍵**が置かれる」（「受け入れる穴 — 持続層の『再構築』は検証されない」節）は、現行では「**管理系 RG (`rg-mgmt-mindbox`) のバックアップ Storage と、同 RG の Key Vault に置かれた非エクスポートの RSA 鍵オブジェクト**」と読み替える（**バックアップはバックアップのまま**で、D2 のとおり管理系 RG の非公開 Storage として存続する。置き換わるのは**層の呼び名**と**鍵の方式**の 2 点だけ）。層の呼び名（持続層 → 管理系 RG）は本 D1 が置き換え、鍵の方式（GPG 秘密鍵 → 非エクスポート鍵オブジェクト + 封筒暗号 / 復号は Key Vault の中）は [E2E artifact は既定で秘密](archive/operations/e2e-artifacts-are-secret-by-default.md) D5 の 2026-08-12 改訂が置き換えたもので、**0046 の本文は書き換えずここで読み替えを宣言する**（過去 ADR の本文は改変しない / `/adr` skill）。鍵の実体は [`cicd/iac/main-mgmt.bicep`](../../cicd/iac/main-mgmt.bicep) の `e2eTraceKey`、運用手順は [`docs/runbooks/e2e-trace-keys.md`](../runbooks/e2e-trace-keys.md) が正典。

### D2 — データは「守る」のではなく「戻せる」ようにする

消えて困る Cosmos のデータを撤収から**守る**のではなく、**管理系 RG の Storage へバックアップして復元可能にする**。

| 方式                                        | 評価                                                                          |
| ------------------------------------------- | ----------------------------------------------------------------------------- |
| Cosmos 継続バックアップ（PITR）             | Azure 標準。保持期間内なら削除済みアカウントも復元可だが、Cosmos の機能に依存 |
| 定期バックアップ（既定）                    | **復元にサポート問い合わせが要る**。実質使えない                              |
| **管理系 RG の Storage へ自前エクスポート** | **採用**。完全に独立し、何を消しても残る                                      |

⚠️ **バックアップ先は管理系 RG の非公開 Storage に限定する。** [ADR 0041](archive/operations/ux-observations-on-git-data-branch.md) の「git データブランチ」に前例があるので同じ手を使いたくなるが、**このリポジトリは public**。Problem / Mention を公開した時点で取り返しがつかない。Storage 側は `allowBlobPublicAccess: false` / `allowSharedKeyAccess: false`（読み書きは Entra + RBAC のみ）で宣言する。

### D3 — 復元を 1 回通すまで、撤収ガードは Cosmos が居る RG を拒否する（**暫定**）

[ADR 0018](archive/operations/runtime-verification-in-the-loop.md)「動作検証をループに組み込む」を復元にも適用する — **復元したことのないバックアップはバックアップではない**。エクスポートを作っても、**空の Cosmos へ復元して Problem が戻ることを 1 回通す**まで D2 は完遂ではない。

それが済むまで、撤収ガード（`cicd/scripts/env/persistent_layer_guard.py`）は **Cosmos が居る RG の撤収を拒否する**。**これは「Cosmos の置き場所が間違っている」という意味ではなく**、データを裸で消さないための足場である。

**拒否理由を 2 種類に分け、判定コードを別にする**のが判断の核:

| 判定コード                     | 何を止めるか                                    | 性質                             |
| ------------------------------ | ----------------------------------------------- | -------------------------------- |
| `target-is-management-rg`      | 管理系 RG そのものの削除                        | **恒久**。どのフラグでも通さない |
| `management-resources-present` | 層タグ / 名指しの管理系リソースが居る RG の撤収 | **恒久**（明示 override は可）   |
| `data-restore-unproven`        | Cosmos が居る RG の撤収                         | **暫定**（D2 の復元実証まで）    |

同じコードにすると、**復元実証が済んだときに「どちらを緩めるつもりだったか」がコードからもログからも読めなくなる**。

**復元を通したら、`data-restore-unproven` の一律拒否を「直近のバックアップが十分に新しいか」の確認に差し替える。** 差し替えずに実証だけ済ませると、[ADR 0046](0046-environment-rebuildable-from-declaration.md) D9 第 3 段階の週次テストが毎回 override を要求し、**逃げ道が常用になってガードが死ぬ**。

**OpenAI / Speech は撤収を止めない。** アプリそのものでデータを持たない（PO 整理: Speech は実質ロスなし / OpenAI はクォータ取り直しの不確実性のみ）。ただし**黙って通さない** — 撤収で何を失うかを削除前に出す。「止めないという判断」を記録から消さないため。

### D4 — 層タグ `mindInboxLayer=management` を機械可読な判定入力にする

Key Vault / Storage / Log Analytics は**両層に同じ型が居る**（アプリ系の SQL 管理者用 vault / Function App の実行 storage / ops workspace）。型で一括りにすると正当なアプリ系の撤収まで常に拒否され、override が常用になる。逆に型から外すとバックアップ Storage が黙って消える。

そこで **`main-mgmt.bicep` が作るリソースに層タグを刻み、撤収ガードは型ではなくタグで層を見分ける**。タグも名指しも無い両層型は**アプリ系として通すが、何をそう見なしたかを出す**。

### Positive Consequences

- **アプリ系 RG をまるごと使い捨てにできる** — [ADR 0046](0046-environment-rebuildable-from-declaration.md) D9 が検証したい対象（Cosmos / OpenAI / Speech を含む）を逃がさずに済む
- **Cosmos 無料枠 / Speech F0 の「1 サブスクに 1 つ」問題が、隠れずに検証対象になる** — 逃がして回避すると「取り直せるか」が永久に未検証のまま残る
- **移行コストがゼロ** — Azure には何も apply されていない段階で正せた
- **「消えると困る」と「運用のため」が別概念として整理される** — 前者はバックアップ対象を決める軸、後者は層を決める軸
- **緩めてよい拒否とそうでない拒否が、コードとログから区別できる**（D3）

### Negative Consequences

- **D2 が動くまで「使い捨て」は宣言でしかない** — バックアップ / 復元の往復を 1 回通すまで、アプリ系 RG の撤収は暫定的に拒否され続ける
- **OpenAI / Speech が撤収ガードの拒否対象から外れる** — データは持たないが、クォータ / F0 枠を取り直せるかは未検証のまま撤収が通る
- **管理系の bicep だけが検証されない**（[ADR 0046](0046-environment-rebuildable-from-declaration.md) の「受け入れる穴」は本 ADR でも引き継ぐ）
- **[ADR 0046](0046-environment-rebuildable-from-declaration.md) を読むときに 2 本を突き合わせる必要がある** — D1 だけが本 ADR に置き換わり、D2〜D10 は現行（D9 本文の「持続層」は管理系 RG と読み替える）としてあちらに残る

## Pros and Cons of the Options

### Option A: 管理系 / アプリ系 + バックアップ（採用）

- Good, because **PO の原設計**であり、層の軸が「運用のためか / アプリそのものか」という 1 つの問いで決まる
- Good, because アプリ系をまるごと使い捨てにでき、[ADR 0046](0046-environment-rebuildable-from-declaration.md) D9 の検証対象が縮まない
- Good, because データ保護が **Azure の機能ではなく自分たちの経路**になり、毎週テストされる
- Bad, because バックアップ / 復元を作るまで撤収が暫定的に止まったままになる
- Bad, because クォータ再取得の不確実性を、逃がさずに引き受けることになる

### Option B: ADR 0046 D1 のまま（持続層 = 消えると困るもの）

- Good, because 実装済み（[PR #412](https://github.com/yomote/mind-inbox/pull/412)）で、追加作業が要らない
- Bad, because **PO の設計と違う**。層の軸が「消えると困るか」だと、困るものが増えるたびに持続層が太り、使い捨てにできる範囲が縮む
- Bad, because Cosmos / OpenAI / Speech の **RG 間移動にダウンタイムと再結線が要る**（apply 後はコストが跳ね上がる）
- Bad, because **逃がしたぶんだけ検証されない** — [ADR 0046](0046-environment-rebuildable-from-declaration.md) 自身が「持続層の再構築は検証されない」を穴として受け入れており、持続層が太るほどこの穴が広がる
- Bad, because 「別 RG にあるから安全」は**バックアップの代わりにならない**（RG は消せてしまうし、消さなくてもデータは壊れる）

### Option C: 層は A だが Cosmos の保護は PITR に委ねる

- Good, because 実装が要らない（Azure の機能を有効にするだけ）
- Bad, because **復元経路が毎週テストされない** — 壊れていることに気づくのが、いちばん困っているとき
- Bad, because Cosmos の機能に依存し、[ADR 0018](archive/operations/runtime-verification-in-the-loop.md)「動作検証をループに組み込む」(実際に 1 回通す) を満たしにくい
- Bad, because 保持期間の外に出たデータを守れない

## 動作検証（実装後に何を叩くか / [ADR 0018](archive/operations/runtime-verification-in-the-loop.md)）

「設定したか」ではなく**振る舞い**で書く:

| 判断                      | 確かめ方                                                               | 何が言えたら緑か                                                        |
| ------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| D1 管理系 RG は消せない   | `RG=rg-mgmt-mindbox ./scripts/env/cleanup-env.sh`（override 有無とも） | **exit 3 / `az group delete` 0 回**                                     |
| D1 層タグが判定入力になる | アプリ系 RG に層タグ付きの Key Vault を置いて撤収                      | `management-resources-present` で拒否 / 何も消えない                    |
| D3 暫定拒否               | Cosmos が居るアプリ系 RG で撤収                                        | `data-restore-unproven` で拒否 / 何も消えない                           |
| D3 OpenAI / Speech は通す | OpenAI / Speech だけが居るアプリ系 RG で撤収                           | **通る**。ただし「何を失うか」がログに出ている                          |
| D2 バックアップが非公開   | `az storage account show` で公開設定を読む                             | `allowBlobPublicAccess=false` / `allowSharedKeyAccess=false`            |
| D2 復元                   | 空の Cosmos へ復元して `problem.list` を叩く                           | **破壊前の Problem が戻っている**（これが済むまで D2 は完遂ではない）   |
| D3 差し替え               | 復元実証後に同じ撤収を実行                                             | **バックアップが新しければ通る / 古ければ拒否**（一律拒否ではなくなる） |

## Links

- Issue: [#302](https://github.com/yomote/mind-inbox/issues/302) — PO の原設計は [2026-08-12 のコメント](https://github.com/yomote/mind-inbox/issues/302#issuecomment-5263080034)
- PR: [#412](https://github.com/yomote/mind-inbox/pull/412)（[ADR 0046](0046-environment-rebuildable-from-declaration.md) D1 初版に従った実装）/ [#419](https://github.com/yomote/mind-inbox/pull/419)（本 ADR の実装）
- Runbook: [`docs/runbooks/mgmt-layer-apply.md`](../runbooks/mgmt-layer-apply.md)
- 関連 ADR: [0046](0046-environment-rebuildable-from-declaration.md)（D1 を supersede — 2026-08-15 の Accept で発効 / D2〜D10 は現行 — D9 本文の「持続層」は管理系 RG と読み替える）/ [0003](0003-two-phase-bicep.md) / [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) / [0041](archive/operations/ux-observations-on-git-data-branch.md) / [0045](archive/operations/e2e-artifacts-are-secret-by-default.md) / [0018](archive/operations/runtime-verification-in-the-loop.md)
