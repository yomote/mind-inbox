# 0030. 永続化は Cosmos DB 1 本に寄せ、BFF の内側だけに置く

- Status: Accepted
- Date: 2026-08-09
- Deciders: omoteforlab (2026-08-09 の design-gate #5 で承認。保管リージョンは Japan East を選択)
- Consulted: —
- Informed: —

Technical Story: [#165](https://github.com/yomote/mind-inbox/issues/165)

## Context and Problem Statement

Problem / Mention / 相談履歴 / 会話セッション / 承認レコードはすべて in-memory の module singleton で保持されており、プロセスが落ちれば消える (`problemRepository.ts:52` / `historyRepository.ts:26` / `repositories.py:31-57`)。Functions は Y1 (Consumption) なのでアイドルで確実にリサイクルされ、**実質的に翌日には空になる**。これは要件 **FR-4「Problem はセッション終了後も永続化され、再起動 / 再ログインで消えない」の未達**であり、同時に concept_deck が競争軸に据えた「継続的に育つ構造化体験」の芯そのものが欠けている状態である。

基本設計 (当時) の Phase 2 は方針として「Cosmos DB / Redis」を挙げていたが、**Azure Cache for Redis は 2026-04-01 から新規顧客の作成がブロックされ、2028-09-30 に廃止される**ことが判明した。方針の再確認が必要になっている。

制約は 3 つ: (1) 月次予算 ¥3,000 (ADR 0013、既に Azure OpenAI / Container Apps / Functions / SWA / Log Analytics が消費している残り枠)、(2) NFR-1「保存データの暗号化 / ユーザーによる削除権 / 保管リージョンの明確化」が**最重要**指定、(3) 次のマイルストーン (#83) でベクトル検索を使うため、そこで作り直しになる選択を避けたい。

## Decision Drivers

- **コスト** — 予算 ¥3,000/月の残り枠に収まること。ストアだけで枠を食い潰さない
- **二重工事の回避** — #83 (embedding 索引) で別ストアへ移す羽目にならないこと。v2 実装計画 §6 が「Problem 永続化と M2 索引の関係 (同一ストアか分離か)」を宿題として明記している
- **扉を増やさない** — 機微な個人データに到達しうる経路を最小にする。#86 (OpenAI の鍵を持つ ai-agent が無認可公開されていた) の再発を防ぐ
- **宣言的管理の内側に置く** — IaC の外で加えた設定は再デプロイで消える (debrief #2 の教訓)
- **差し替えコストの小ささ** — 既存の repository interface と DI を活かし、router / テストに波及させない

## Considered Options

- Option A: Cosmos DB (NoSQL API) 1 本 — 永続データも短命データも同居、BFF の内側だけに置く
- Option B: Table Storage + 将来 Azure AI Search
- Option C: PostgreSQL Flexible Server (pgvector)
- Option D: Cosmos DB + Azure Managed Redis (基本設計 Phase 2 の原案に最も近い形)

## Decision Outcome

Chosen option: **"Option A"**。ドキュメント本体・短命セッション・将来のベクトル検索の 3 つを**サービス 1 個で賄える唯一の選択肢**であり、無料枠が使えれば月額 ¥0、使えなくても serverless で数百円に収まるため。Redis を足す案 (D) は、そもそも新規作成できないうえ、Cosmos のネイティブ TTL で短命データの要件が満たせるため理由が消えた。

### 決定の内訳

- **D1 ストアは Cosmos DB (NoSQL API) 1 本**。基本設計 (当時) の Phase 2 の「Cosmos DB / **Redis**」を **Cosmos 単独**に改める。短命な会話セッション・承認レコードはコンテナ単位の TTL で自動消滅させる (Cosmos の TTL は秒単位・アイテム単位で上書き可)
- **D2 課金モードは free tier (provisioned 1,000 RU/s) を第一候補、取れなければ serverless**。free tier は「1 サブスクリプションに 1 アカウント」「アカウント作成時のオプトイン必須・後から有効化不可」「serverless は対象外」。bicep のパラメータで切り替え可能にし、**free tier の取得に失敗したら serverless で作り直す**
- **D3 データ面のアクセスはマネージド ID + RBAC のみ。アカウントキーは殺す** (`disableLocalAuth: true`)。Function App は既に SystemAssigned のマネージド ID を持っている (`bootstrap-core.bicep:574`)。接続文字列を app settings にも Key Vault にも置かない
- **D4 ストアに触れるのは BFF だけ。ai-agent からは繋がない**。ai-agent / vv-wrapper の Container App は **bicep の外**にあり (`bootstrap-core.bicep:90` のコメント、`enableAiAgentAca` は既定 false でリソース宣言そのものが無い)、マネージド ID を安定して付けられない。会話セッションと承認レコードは **in-memory のまま据え置く** — FR-4 が要求しているのは Problem と履歴の永続化であり、セッションは 1 回の座りの間だけ生きればよい
- **D5 `userId` をスキーマに先に入れる**。EasyAuth が渡す `x-ms-client-principal` から oid を取り、パーティションキー `/userId` に載せる。ヘッダが無いローカル開発では `"local"` にフォールバック。**今は単一ユーザーだが、後から足すと repository 6 メソッドとテスト seed が全部動く**ため、値が 1 種類でも先に切る
- **D6 保管リージョンは Japan East に固定する** (NFR-1「保管リージョンの明確化」)。Functions / SWA は East Asia だが、メンタル状態に関する機微情報の保管地を国内に置くことを優先する。クロスリージョンの往復は増えるが、1 リクエストあたりの呼び出し回数が少なく (全件一覧 or id 点引きのみ)、データ量も小さい (1 ユーザー 1 年で約 270 KB)
- **D7 in-memory 実装は消さず残す**。ローカル開発とテストの既定にする (ADR 0004 の mock 方針と同じ形)。既存テストは `InMemory*` を直接 new しているため、singleton の差し替えでは 1 件も壊れない

### Positive Consequences

- FR-4 が満たされ、「育つ」という製品の芯が初めて成立する
- **#83 (embedding 索引) でサービスを足さずに済む** — Cosmos DB NoSQL のベクトル検索は 2025-01 に GA し、ベクトルインデックス専用の追加料金は無く、消費 RU と通常ストレージとして課金される。3,000 件 × 1536 次元でも 9〜18 MB で、25 GB の無料枠に対して誤差
- アカウントキーを発行しないため、「鍵が漏れる経路」が構造的に存在しない
- 差し替え面が小さい — BFF は新実装 1 ファイル + `context.ts` の 1 行。router は無変更

### Negative Consequences

- **会話セッションは引き続き揮発する**。ai-agent が scale-to-zero で落ちれば中断復帰 (`paused` 画面) は壊れる。現状と同じ挙動であり劣化はしないが、この ADR では解決しない
- Cosmos のパブリックエンドポイントは残る。Private Endpoint は VNet を要し、Functions Y1 は VNet 統合非対応 (ADR 0017 で確認済みの制約) — 守りは `disableLocalAuth` + Entra RBAC の 1 層に依存する
- **デプロイ用 OIDC SP がサブスクリプション Contributor のままなので、そこからデータ面の RBAC を自分に割り当てられる**。#46 (SP ロールの最小化) が未着手であることの影響がデータにも及ぶ
- ~~価格情報の一次ソースが 403 で取得できず、月額はすべて二次情報の概算~~ → **解消** (#168 でネットワーク許可を追加。下記「実測」に一次情報を追記済み)。ただし **serverless / Table Storage / AI Search / PostgreSQL の月額は依然として二次情報の概算**のまま — free tier が取れる見込みになったため、追加調査はしていない
- East Asia ↔ Japan East のクロスリージョン往復が増える (D6 のトレードオフ)

## Pros and Cons of the Options

### Option A: Cosmos DB (NoSQL API) 1 本

ドキュメント DB。永続コンテナ (problems / history) と TTL 付きコンテナを同一アカウントに置く。

- Good, because 無料枠が取れれば **月額 ¥0** (1,000 RU/s + 25 GB、アカウント生涯)。取れなくても serverless で概算 ¥40〜200/月
- Good, because **TTL がネイティブ**で、短命データに削除ジョブを書かなくてよい
- Good, because **ベクトル検索が同じコンテナで完結**する (GA 済み・専用の追加料金なし) → #83 で二重工事にならない
- Good, because クエリが「1 ユーザー分の全件」と「id 点引き」の 2 種類しかなく、`/userId` が自明のパーティションキーになる。クロスパーティションクエリが 1 つも発生しない
- Bad, because 無料枠は 1 サブスクリプションに 1 つで、作成時のオプトインを逃すと取り直せない
- Bad, because Private Endpoint を張るには VNet が要り、現構成では張れない

### Option B: Table Storage + 将来 Azure AI Search

キー・バリュー寄りのストア。

- Good, because 保存コストは最安 (概算 月 ¥6)
- Bad, because **TTL が無い** — 短命データの削除ジョブを自作することになる
- Bad, because **ベクトル検索が原理的に不可**。#83 で AI Search を足すか Cosmos へ移すことになり、**まさに避けたい二重工事**。AI Search の Free は 50 MB / 3 インデックスで、無操作が続くと削除されうる。有料 Basic は概算 ¥11,775/月 = 予算の約 4 倍

### Option C: PostgreSQL Flexible Server (pgvector)

- Good, because pgvector 0.8.0 + DiskANN でベクトル検索まで 1 本で賄える
- Bad, because 最小構成でも概算 ¥2,355〜3,140/月 で、**ストアだけで予算をほぼ全消費**する。無料枠は Azure 無料アカウント限定の 12 ヶ月のみで恒久枠が無い
- Bad, because TTL に相当する仕組みを cron で自作する必要がある

### Option D: Cosmos DB + Azure Managed Redis

基本設計 (当時) の Phase 2 の原案に最も近い形。

- Bad, because **Azure Cache for Redis は 2026-04-01 から新規顧客の作成がブロック済み**。既存顧客も 2026-10-01 で作成不可、2028-09-30 に廃止。今日 (2026-08-09) の時点で選べる可能性が低い
- Bad, because 後継の Azure Managed Redis は最小 SKU の月額が確認できず、C0 相当の超小型 SKU を持たないため予算超過の見込み
- Bad, because Cosmos の TTL で短命データが賄える以上、**サービスを 1 つ増やす理由が無い**

## 実測 (2026-08-09、D2 の前提確認)

[ADR 0031](archive/operations/agent-reaches-outside-via-github-actions.md) の `ops-inspect` ワークフロー ([run 31295125083](https://github.com/yomote/mind-inbox/actions/runs/31295125083)) を `check=cosmos-free-tier` で実行し、実サブスクリプションを読んだ結果:

```text
既存の Cosmos アカウント数: 0
うち free tier を使用中:    0
→ free tier はまだ消費されていない
```

**D2 の第一候補 (free tier + provisioned) が取れる見込み。** serverless へのフォールバックは、現時点では発動しない。

ただしこれは**「1 サブスクリプションに 1 アカウント」の枠が空いているか**の判定であり、無料枠の中身は含まない。

### 無料枠の中身 (一次情報で確認済み)

起案時は Microsoft Learn に到達できず二次情報の概算だったが、#168 でネットワーク許可を追加したため一次情報を取得した ([Azure Cosmos DB lifetime free tier](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier)、2026-08-09 取得):

| 項目                | 公表値                                                                    |
| ------------------- | ------------------------------------------------------------------------- |
| 含まれる枠          | **1,000 RU/s + 25 GB**                                                    |
| 期間                | **アカウントの生涯** (12 ヶ月制限なし)                                    |
| 個数                | **1 サブスクリプションにつき 1 アカウント**。作成時にオプトイン必須       |
| 対象                | provisioned / autoscale。**serverless は対象外**                          |
| 共有スループット DB | 1,000 RU/s まで無料。free tier アカウントでも 1 DB あたり最大 25 コンテナ |

**起案時に懸念していた「free tier が 1,000 RU/s → 100 RU/s に下がったのでは」という説は誤り**だった (Q&A の混同で、公式ドキュメントは一貫して 1,000 RU/s)。D2 の前提は公表値どおりで成立する。

なお削除して作り直せば free tier を再取得できる、とも明記されている。作成時にオプトインを逃しても取り返しはつく。

## 動作検証 (実装後に何を叩けば「効いている」と言えるか)

ADR 0018 に従い、「設定したか」ではなく振る舞いで書く。

1. **再起動で消えないこと** — 相談 → 困りごとを生成 → `az functionapp restart` → 困りごと一覧に同じ id が残っている
2. **翌日も残っていること** — 毎日の golden-path monitor に「一覧が前回 run で作られた困りごとを含む」検証を足す (アイドルで Functions がリサイクルされた後の実測になる)
3. **キーが効かないこと** — Cosmos のアカウントキーで data plane を叩き、拒否される (`disableLocalAuth` の振る舞い確認)
4. **短命データが消えること** — セッション相当のドキュメントを TTL 秒後に読み、消えている
5. **予算** — 1 週間の実コストを Cost Management で確認し、月額換算が想定内であること

## Links

- Issue: [#165](https://github.com/yomote/mind-inbox/issues/165) / 後続: [#83](https://github.com/yomote/mind-inbox/issues/83) (embedding 索引) / [#46](https://github.com/yomote/mind-inbox/issues/46) (SP ロール最小化)
- 要件: `docs/design/requirements.md` FR-4 / NFR-1 / NFR-2
- 現行の構造: [`docs/design/basic_design.md`](../design/basic_design.md) の「永続化」節 (本 ADR の決定を反映済み)
- 出典となった旧方針: [`docs/design/archive/basic_design_poc.md`](../design/archive/basic_design_poc.md) の Phase 2 (「Cosmos DB / **Redis**」— 本 ADR が Redis の部分を改める。archive は現行方針として読まないこと)
- 実装計画: `docs/design/implementation_plan_v2.md` §6 宿題 (Problem 永続化と M2 索引の関係)
- 関連 ADR: [0002](0002-container-apps-not-aks.md) (scale-to-zero) / [0004](0004-mockapi-as-frontend-truth.md) (mock を残す方針) / [0007](0007-problem-centric-two-layer-domain-model.md) (Mention を Problem に内包 = 1 ドキュメントで完結) / [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) (予算 ¥3,000) / [0017](0017-container-apps-access-via-auth-gate.md) (Functions Y1 は VNet 統合非対応) / [0018](archive/operations/runtime-verification-in-the-loop.md) (動作検証)
