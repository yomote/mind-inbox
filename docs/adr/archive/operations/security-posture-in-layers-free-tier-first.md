# 0047. セキュリティ対策を「無料枠優先 + 責任分担が重ならない層」で段階導入する

- Status: Proposed
- Date: 2026-08-12
- Deciders: yomote (PO) — **未承認**。エージェント起案 (CLAUDE.md: エージェント起案の ADR は Proposed で入れ、Accept は user のみ)
- Related: [ADR 0019](independent-judge-agents-security-qa-release.md) (独立 judge) / [ADR 0038](security-checks-as-mechanized-triggers.md) (検査トリガーの機構化 — 本 ADR はその続き) / [ADR 0013](../../0013-standing-low-cost-dev-env-with-auto-deploy.md) (低コスト常設 dev) / [ADR 0018](runtime-verification-in-the-loop.md) (動作検証) / [ADR 0025](../../0025-deploy-container-images-by-immutable-sha-tag.md) (不変 sha) / [ADR 0030](../../0030-persistence-on-cosmos-db-single-store-behind-bff.md) (Cosmos) / [ADR 0036](merge-gate-as-required-check-and-pm-cadence.md) (review-gate)

Technical Story: [Issue #313](https://github.com/yomote/mind-inbox/issues/313) — セキュリティ現状調査と段階的導入計画。2026-08-12 の PM セッションで 4 領域 (アプリ層 / AI 固有 / IaC・CI / ツール調査) を並行調査した結果に基づく。

## Context and Problem Statement

「セキュリティ対策がほぼできていない」という見立てから調査を始めたが、**実測の結論は違った**。

**基準は既にある。** [`security-rubric.md`](../../../../.github/claude/security-rubric.md) は S1〜S7 で、このプロダクト固有のリスク (メンタルヘルスに近い機微データ / public リポジトリ / LLM へのユーザー入力直結) を正しく名指ししている。public リポジトリの典型事故もほぼ塞げていた — `pull_request_target` はゼロ、fork PR の head を checkout する経路なし、全 workflow が `permissions:` を明示宣言、保存 secret は `GITHUB_TOKEN` のみ (Azure は OIDC)、Cosmos / Speech は `disableLocalAuth: true`、deploy のたびに `smoke-test.sh` が「Functions が 401 を返すか」を実測している。

**欠けているのは、基準を人が思い出さなくても機械が照合する経路の方**であり、しかもそれは [ADR 0038](security-checks-as-mechanized-triggers.md) が既に一度立てた問いだった。本 ADR はその続きとして、0038 が扱わなかった 3 つのことを扱う。

### 実測 1 — 機構を作っても、動かなければ同じ

`security-sweep.yml` は 2026-08-11 に main へ入ったが、**2026-08-12 の本調査で手動起動するまで実行回数 0 回**だった (週次 cron の初回発火が 08-17 のため)。ADR 0038 自身が「動作検証の条件」として挙げた workflow_dispatch での実測も行われていなかった。

つまり **ADR 0038 が問題視した構造 (「起動を人に頼った自動化は、動いていないことに誰も気づかない」) が、0038 で作った機構自体でもう一度起きていた**。教訓は「新設した検査は、その場で 1 回手で回して実データを見るまで、入れたことにならない」。

### 実測 2 — 検出はできたが、severity 表示が優先度を逆転させていた

初回 sweep の結果は **102 件** ([Issue #316](https://github.com/yomote/mind-inbox/issues/316))。しかし到達可能性でトリアージすると、表示と実態が食い違っていた:

| 表示                                        | 実態                                                                                                                        |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| critical 4 件 (`shell-quote` / `vitest`)    | **ビルド時ツール**。`vitest` の critical は「Vitest UI サーバが listen 中なら」で成立しない。出荷物に乗らない               |
| high 37 件の多く (`react-router` の RCE 等) | **SSR / RSC モード前提**。このフロントは `vite build` の SPA で `entry.server` も `renderToString` も無く、経路が存在しない |
| **不明 40 件** (Python)                     | **これが本命**。`starlette` / `aiohttp` / `pyjwt` / `cryptography` / `urllib3` は Container Apps 上で実際に動く実行時依存   |

`pip-audit` が severity を出力しない仕様が、そのまま「**一番重要なものが一番軽く見える**」バイアスになっていた。検出の器を増やす前に、この読み違えを直す方が効く。

### 実測 3 — 穴は「アプリの内側」に集中していた

外周 (CI / クラウド設定) より、アプリの内側の方が薄かった:

- **会話層に所有者チェックが無い** — Problem 層は `userId` でパーティション分離済み ([ADR 0030](../../0030-persistence-on-cosmos-db-single-store-behind-bff.md) D5) なのに、`sessions` の partition key は `/id` = session_id で **userId フィールドすら無い**。BFF も ai-agent も session_id の所有権を検証しない。結果、**最も機微な「生の吐き出し全文」だけが全ユーザー共通の名前空間**にあり、session_id は事実上 read/write 両用の bearer token になっている
- **量の上限が 1 つも無い** — BFF の zod に `.max(` が 0 件、履歴の切り詰めも無く毎ターン全量再送、LLM 呼び出しにもサービス間呼び出しにもタイムアウト無し、レートリミット無し
- **その安全網も存在しなかった** — [ADR 0013](../../0013-standing-low-cost-dev-env-with-auto-deploy.md) が二重防御と呼ぶ予算アラートは `!empty(budgetContactEmails)` 条件付きで、その値がリポジトリのどこにも無いため**リソースが一度も作られていない** ([Issue #317](https://github.com/yomote/mind-inbox/issues/317))
- **指示とユーザー入力の境界が無い** — ツール結果と RAG コンテキストが **system ロール**で履歴に入る。間接プロンプトインジェクションの経路そのもの
- **HITL が形骸化** — 承認要求の文面はツール名のみ (引数を見せない)、承認 UI が未実装、`get_inbox_stats(user_id)` は **user_id をモデル出力から受け取る**設計で承認不要

### 実測 4 — public リポジトリであることが最大の予算的資産

追加課金ゼロという制約 ([ADR 0008](pr-review-via-cloud-routine.md) / [0031](agent-reaches-outside-via-github-actions.md) から継承) の下で、**public リポジトリなら CodeQL (SAST) / secret scanning + push protection / Dependabot が全て無料**。ADR 0038 は選択肢 B としてこれを検討し「リポジトリ設定のクリックが絡み、コードだけで完結しない」ため見送っていたが、**その判断は「今日動かすことを優先する」文脈でのものであり、PO に確認できる場では前提が変わる**。

## Decision Drivers

- **追加課金ゼロ** — 個人プロジェクト。有料 SaaS も Azure の有料プランも入れない
- **門を重くしない** ([ADR 0036](merge-gate-as-required-check-and-pm-cadence.md) の driver を継承) — 検査を増やしてリードタイムを悪化させない
- **誤検知でアラートが読まれなくなったら、その検査は動いていないのと同じ** — ADR 0038 の「実績ゼロ」と同型の失敗。**入れる検査の数より、読まれ続ける検査の数**を最大化する
- **層の責任範囲が重ならないこと** (Issue #313 の方針) — 同じものを 2 つのツールが吠えると両方読まれなくなる
- **重大な問題は人の注意力に依存せず自動で止める** (Issue #313 の方針)
- **不可逆な判断・課金が発生する判断は PO に返す** ([ADR 0020](hitl-choice-format-and-needs-human-queue.md))

## Decision Outcome

**「無料枠を先に使い切る → アプリ内側の穴を塞ぐ → 重い検査は後」の 4 フェーズで入れる。** 各層の責任範囲を重ねない。

### 層の責任分担 (何がどこを持つか)

| 層                   | 検査                                       | 持ち場                                        | トリガー                                                                  | コスト |
| -------------------- | ------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------- | ------ |
| **push 時 (事前)**   | secret scanning + **push protection**      | 秘密の混入を**未然に止める**                  | GitHub 側 (常時)                                                          | ¥0     |
| **PR 時 (必須)**     | CI (test / lint) + review-gate             | 既存                                          | PR                                                                        | ¥0     |
| **PR 時 (advisory)** | CodeQL (SAST) / dependency-review / zizmor | コードの脆弱パターン・新規依存・workflow 権限 | PR                                                                        | ¥0     |
| **PR 時 (条件付き)** | `@codex security review` 自動指名          | 敏感パス (ADR 0038)                           | 敏感パス変更時                                                            | ¥0     |
| **週次**             | security-sweep (SCA + secrets)             | 既知 CVE の照合                               | cron (ADR 0038)                                                           | ¥0     |
| **週次**             | Dependabot                                 | **修正 PR の自動生成**                        | GitHub 側                                                                 | ¥0     |
| **リリース**         | 独立 judge (security-reviewer)             | 到達可能性と実害の判定                        | リリース PR ([ADR 0019](independent-judge-agents-security-qa-release.md)) | ¥0     |
| **常時 (実行時)**    | smoke-test の認可実測 + 予算アラート       | 設定が外れたことの検知                        | deploy 毎 / Azure                                                         | ¥0     |

**重複の排除**: SCA は security-sweep と Dependabot の 2 つが見るが、**役割が違う** — sweep は「棚卸しと痕跡」、Dependabot は「修正 PR の生成」。sweep 側は将来 Dependabot が回り始めたら**件数の追跡に軽量化する**余地がある (ADR 0038 の「将来オプション」がここで回収される)。SAST は CodeQL のみ (semgrep / bandit は入れない)。secrets は push protection (事前) / gitleaks (週次・履歴) で、**事前と事後**という別の役割に分ける。

### Phase 0 — 今日やる (コードのみ・クリック不要)

**いずれも「設計判断を伴わず、入れて壊れるものが無い」もの**に限定した。

1. **`.env` を gitignore** — `ai-agent/app/config.py` が `env_file=".env"` を読み、README が `OPENAI_API_KEY=sk-...` の作成を案内しているのに、`.env` はどの gitignore にも無かった。public リポジトリなので、手順どおり開発した人の `git add -A` で鍵が公開される。**唯一の網だった週次 gitleaks は最大 7 日遅れの事後検知であり防止ではない**
2. **BFF の入力に `.max()` を入れる** — コスト枯渇の一次防御。認証済み 1 アカウントで LLM 課金を青天井に燃やせる状態を塞ぐ
3. **ai-agent 応答を検証してから Cosmos に書く** — 現状は `as ExtractionResult` の型アサーションのみで upsert **してから**検証が走る順序の逆転。**攻撃者不要で踏める唯一の不具合**で、壊れた doc が 1 件入ると Problem 一覧が全件 500 になり画面から復旧できない
4. **Python 実行時依存の CVE を潰す** — `starlette` / `aiohttp` / `pyjwt` / `cryptography` / `urllib3`。**本命の 40 件**
5. **third-party action を全て commit SHA に固定** ([Issue #311](https://github.com/yomote/mind-inbox/issues/311)) — `azure/login@v2` が `id-token: write` を持つ箇所でタグを付け替えられると、**サブスクリプション Contributor がそのまま渡る**。ADR 0025 が image に課した規律を action にも適用する
6. **CodeQL workflow を足す** (TS + Python / advisory) — SAST の空白を埋める
7. **`.github/dependabot.yml`** — **必ず絞る** (月次 + groups + `open-pull-requests-limit`)。102 件の未処理がある状態で素直に入れると PR が洪水になり、Phase 0 の他の作業が止まる
8. **`review-gate` の `gate-pr` を trusted checkout にする** — 現状 `statuses: write` を持ちながら PR 側の `check.py` を実行しており、**同一リポのブランチが required check に偽の success を貼れる**。門が自分で開けられる状態

### Phase 1 — PO のクリック (5〜10 分・全て ¥0)

`Settings` → `Advanced Security` (旧 `Code security and analysis`):

- **Secret scanning** を Enable — 2024-03-11 より前に作られた public リポジトリは既定 OFF。gitleaks (HEAD のみ) と違い **git 履歴全体**を見る
- **Push protection** を Enable — **唯一の「事前に止める」層**。sweep も gitleaks も事後
- **Dependabot alerts** を Enable
- **Private vulnerability reporting** を Enable — public リポジトリの外部報告窓口

⚠️ **CodeQL の "Default setup" は有効化しないこと。** Phase 0-6 で workflow 方式 (advanced setup) を入れるため、**両者は排他**で、default を有効にすると workflow 側が止まる。リポジトリの流儀 (コードで完結・痕跡がリポジトリに残る・状況ページで生死が見える) に合わせて workflow 方式を採る。

### Phase 2 — アプリ内側の穴 (設計判断を伴う / design-gate 対象)

**Phase 0 と違い、これらは設計を決めてからでないと書けない。**

1. **会話セッションに所有者を持たせる** ([Issue #319](https://github.com/yomote/mind-inbox/issues/319)) — `sessions` に userId 次元が無い問題。**マルチユーザー化の必須ブロッカー**であり、データが増えるほど移行コストが上がるので早い方が安い。partition key を変えるなら **Cosmos のコンテナ再作成が要る = 不可逆**。design-gate 必須
2. **指示とユーザー入力の境界を作る** — ツール結果 / RAG コンテキストを system ロールに入れない。間接インジェクションの主経路。**どこで境界を引くかが設計判断**なので design-gate 対象
3. **HITL を実体化する** — 承認文面に**ツール引数を出す** (現状はツール名のみ)、承認 UI の実装、`/approve` の所有者チェックと監査記録。**ツールが実体を持つ前に**やる
4. **`x-ms-client-principal` の前提を実測する** — 「EasyAuth が inbound の同名ヘッダを上書きするから安全」というコメントの前提を確かめるテストもスモークも無い。しかも欠損時は fail-open で `"local"` に落ちる。**偽造ヘッダを実環境に投げて弾かれることを smoke-test に足す** ([ADR 0018](runtime-verification-in-the-loop.md))。実測経路の設計が要る (このセッションの egress では届かないため Actions 側から)
5. **監査ログ** — Phase 3 と併せて。機微データを扱う以上、事故時に「何が読まれたか」を答えられる必要がある

**Phase 0 に降格したもの (設計判断が不要だと判明したため)**: 機密の出口 (ログ・例外文字列)、外向き呼び出しのタイムアウト、`get_inbox_stats(user_id)` が **user_id をモデル出力から受け取る**問題、および **AI ツール権限の不変条件テスト**。最後のものは「副作用のあるツールに承認フラグが付いているか」を **LLM 不要・決定的**に検査するもので、Issue #313 の「AI 固有の攻撃パターンをテストとして蓄積する」の最初の一歩にあたる。設計を決める必要が無く、かつ**将来ツールを足した人の付け忘れを機械が止める**ため、先に入れる価値が高い。

### Phase 3 — 実環境側 (ASM / CSPM / CIEM / DAST) — 2026-08-12 PO 裁定で方針決定

Issue #313 が挙げたカテゴリのうち、**クラウドの実状態を見る 4 つ (ASM / CSPM / CIEM / DAST)** をどう入れるか。PO との対話で以下を決めた。

#### 決定 1 — リポジトリを見る検査とは**別の機構**にする。ただし増やすのは 1 つだけ

既存の security-sweep に相乗りさせない。理由は 4 つ:

| 軸             | リポジトリ側 (sweep / CodeQL)              | 実環境側 (ASM / CSPM / CIEM / DAST)                       |
| -------------- | ------------------------------------------ | --------------------------------------------------------- |
| **真実の所在** | git (lockfile・ソース・履歴)               | **Azure の実状態**。リポジトリとズレること自体が検出対象  |
| **必要な権限** | **秘密ゼロ** (public リポジトリを読むだけ) | **Azure の読み取り権限が要る**                            |
| **検出の意味** | 「既知 CVE を含む版がある」                | 「設定が期待からドリフトした」                            |
| **トリガー**   | PR / 週次                                  | **deploy 後 + 定期** (クラウドの状態は PR では変わらない) |

同じ箱に入れると、sweep に理由なく Azure 権限が付いて被害半径が広がり、かつ「npm の CVE」と「Storage が匿名公開」が同じ Issue に混ざって**両方読まれなくなる** (#316 が 102 件で読みにくかったのと同じ失敗)。

#### 決定 2 — 4 カテゴリは「ドリフト検査」1 つに畳める

商用の CSPM / CIEM / ASM が解いているのは **「自分たちが何を持っているか分からない」** という組織の問題である。**このプロジェクトはその問題を持っていない** — インフラは 100% Bicep で定義されており、**台帳は既に存在する**。

したがって問うべきは「何を持っているか」ではなく **「現実は Bicep と一致しているか」= ドリフト**の 1 問だけになる。この言い換えにより、4 カテゴリが 1 つの検査に畳まれ、コストが桁で下がる。

#### 決定 3 — 置き場所は `smoke-test.sh` を育てる (PO 裁定)

**新機構を作るのではなく、既に正しい形をしている既存の仕組みを育てる。** `cicd/scripts/smoke-test/smoke-test.sh` は deploy のたびに Actions から走り、既に以下を実測している:

- 期待する Container Apps の一覧を持ち、**足りないと「露出検査の対象から漏れています」と落ちる** (`:177-183`) — **ASM のインベントリ照合そのもの**
- Functions に未認証アクセスして 401/403 を実測 (`:113`)
- Container App に匿名アクセスして 403 を実測 (`:249`)
- CORS preflight が EasyAuth に巻き込まれていないかを実測 (`:129`)

ここに「Storage の匿名 blob」「TLS 最低版」「HTTPS-only」「ロール割り当ての期待値との差分」を足していけば、CSPM と CIEM の実質を満たせる。CIEM は `az role assignment list` を期待値と突き合わせるだけで足りる (Entra Permissions Management は **2025 年に retire 済み**で選択肢から消えている)。

**残る宿題**: smoke-test は deploy 時にしか走らないため、**定期実行の口を別途足す**必要がある (人が Portal を触ったドリフトは deploy と無関係に起きる)。

#### 決定 4 — 順序: #46 (CD の権限縮小) を先にやる (PO 裁定)

CIEM を先に入れても、返ってくる最大の指摘は **「CD の identity がサブスクリプション全体の Contributor である」**で、それは既に分かっている ([Issue #46](https://github.com/yomote/mind-inbox/issues/46))。既知の指摘を機械に言わせるために機械を作るのは順序が逆。

加えて**実環境検査には Reader 権限の OIDC identity を新設する必要がある**ため、#46 で権限設計を触るときに一緒に整理する方が安い。

#### 決定 5 — 次の一手は CSPM ではなく**監査ログ** (PO 裁定)

診断設定は `bootstrap-core.bicep:597` に SQL 用が 1 本あるだけで、それも `enableSql=false` のため **実際には 1 本も出ていない**。**Cosmos DB と Function App にアクセス記録が無い**。

これが他と質的に違うのは、**「問題を検出できない」ではなく「事故の後に何が読まれたか答えられない」**から。メンタルヘルスに近い個人の悩みを Entra RBAC の 1 層で守っていて、そのアクセス記録が無い状態は、他のどの検出機構より先に埋めるべき穴と判断した。

**実装時に判明した訂正**: 当初の調査は「Container Apps にも記録が無い」としていたが、**誤りだった** — Container Apps Environment の `appLogsConfiguration` が console / system ログを既に同じ Log Analytics へ送っている (`bootstrap-core.bicep:840` 付近)。ここに診断設定を足すと**同じログが二重に取り込まれ、コストだけが倍**になるところだった。Storage も対象外とした — `StorageRead/Write/Delete` は Functions ランタイムの lease / heartbeat で常時大量に出る**最大の取り込み源**である一方、この口座にはランタイムの作業領域しか無く**ユーザーデータは全て Cosmos にある**。守る対象が無い場所に最大のコストを払う形になる。

⚠️ **ログは取り込み量で課金される。** 「セキュリティのために毎月の請求が増えた」は最悪の結果なので、以下で担保した:

- **カテゴリを絞る** — 監査に効くものだけ (Cosmos の `DataPlaneRequests` = 「何が読まれたか」に答えられる唯一のカテゴリ / `ControlPlaneRequests` / Function App の `FunctionAppLogs`)。`AllMetrics` は**全リソースで捨てた** (標準メトリックは無料で見られるものに取り込み課金を乗せるだけ)
- **保持 30 日** — 無料枠の「31 日ぶんは取り込み料金に含まれる」条件を満たし、保持課金の発生条件をそもそも満たさない
- **日次上限 (`workspaceCapping.dailyQuotaGb = 0.15`)** — 見積もり (~90 MB/月 = 無料枠 5 GB の約 2%) が外れても、**構造的に無料枠を超えられない**ようにした。上限超過分は収集されないので課金にならない
- **feature flag** (`enableDiagnostics` / `enableCosmosDataPlaneAudit`) — 取り込み量が最大の DataPlaneRequests だけ独立に落とせる

**残課題 (PO 裁定が要る)**: EasyAuth のサインイン記録 (`AppServiceAuthenticationLogs`) は監査価値が高いが、**Linux Consumption (Y1) では出力されない**。取得には plan 変更 = 課金が前提になるため、Phase 2-4 (`x-ms-client-principal` の前提の実測) と併せて判断する。

#### その他 (優先度低)

- **zizmor** (workflow の静的解析) — Phase 0 で入れた SHA 固定を**継続的に守る機械**として。workflow 16 本 + OIDC + `packages:write` があるので費用対効果は高い
- **IaC スキャン** — **Checkov は使わない** (公式 Issue #7394 で、モジュール化された現実的な Bicep では信頼できないと認めている)。**PSRule for Azure** (Bicep ネイティブ) が候補。ただし初期ルールセットが騒がしいので、誤検知を抑える設定とセットでなければ入れない
- **Trivy** (コンテナ / ベースイメージ CVE) — 現状 SCA はアプリの依存しか見ておらず、ベースイメージは完全に未カバー
- **本格的な DAST (ZAP baseline 等)** — smoke-test の実測で当面代替する

**実装上の制約 (実測済み)**: エージェントのセッションは egress ポリシーにより Azure エンドポイントへ到達できない (CONNECT に 403)。**動的検査は必ず Actions 側から回す** ([ADR 0031](agent-reaches-outside-via-github-actions.md) の原則がそのまま効く)。

### 明示的に「やらない」もの (課金・複雑さが制約と衝突)

| 候補                                            | 理由                                                                                                                                                                                                                                                                                         |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Microsoft Defender for Cloud のプラン有効化** | ⚠️ **初回有効化で 30 日無料トライアルが自動開始し、終了後に自動課金**。しかも**プランごとに個別に 30 日**が始まるため感覚がズレる。Foundational CSPM (全プラン Off) は無料で Secure Score まで見られるので**そこで止める**。`Get-AzSecurityPricing` でプラン状態を定期監視するのが最安の保険 |
| **Azure WAF**                                   | $35〜$330/月。追加課金ゼロの制約と正面衝突                                                                                                                                                                                                                                                   |
| **Entra ID Access Reviews**                     | **P2 ライセンス必須**で無料枠外                                                                                                                                                                                                                                                              |
| **有料の ASM / CSPM / DAST SaaS**               | 同上。公開面は Bicep が真実なので、まず IaC 側で把握する                                                                                                                                                                                                                                     |
| **semgrep / bandit / eslint-plugin-security**   | CodeQL と責任範囲が重複する。**2 つが同じものを吠えると両方読まれなくなる**                                                                                                                                                                                                                  |
| **LLM を採点者にするセキュリティテスト**        | 非決定 + 課金 + 説明不能。判定は「機構が何をしたか」に寄せる (決定的なツール権限テストで代替)                                                                                                                                                                                                |

### 誤検知と開発速度を守る運用方針

- **新規検査は必ず advisory で入れ、required にするのは投稿実績を見てから** (ADR 0038 が `@codex security review` で採った方針を全検査に一般化)
- **CodeQL の query suite は Default のまま** (`security-extended` は誤検知が跳ねる)
- **Dependabot は月次 + groups + limit** で PR 数を抑える
- **到達可能性の判定を検出とセットで残す** — Issue #316 に対して行ったトリアージ (ビルド時 / 実行時 / 該当なし の 3 分類) を**毎回の sweep Issue でやる**。やらないと件数だけが積み上がって読まれなくなる
- **sweep の表示を直す** — pip-audit の severity 欠落が優先度を逆転させていたので、「実行時依存かどうか」を出力に含める

### CI で必須にするもの / 定期でよいもの (Issue #313 の成果物)

| 必須 (マージを止める)                     | 定期実行でよい                     |
| ----------------------------------------- | ---------------------------------- |
| CI (test / lint) — 既存                   | CodeQL (advisory → 実績を見て判断) |
| review-gate — 既存                        | security-sweep (週次)              |
| push protection (GitHub 側で push を拒否) | Dependabot (月次)                  |
|                                           | zizmor / PSRule / Trivy (Phase 3)  |

**この表の左側を今すぐ増やさない**のが要点。門を重くする判断は、advisory での実績が出てから PO が行う。

## Positive Consequences

- 追加課金 **¥0** のまま、SAST (CodeQL) / 事前の秘密ブロック (push protection) / 依存修正の自動 PR (Dependabot) という**今まで完全に空いていた 3 層**が埋まる
- 「検出はするが誰も読まない」を避けるため、**入れる検査の数を意図的に絞り**、責任範囲の重複を排除した
- 攻撃者不要で踏める不具合 (poison document) と、コスト暴走の一次防御 (入力上限) が Phase 0 で塞がる
- 予算アラートが存在しないという**安全網の不在**が可視化され、needs-human として PO に返った

## Negative Consequences

- **Phase 2 が本丸なのに一番遅い** — 会話層の所有者チェック / HITL / 指示境界は設計判断を伴うため design-gate 待ちになる。その間、**マルチユーザー化はできない**(単一ユーザー運用である限り実害は出ないが、データが増えるほど移行コストは上がる)
- **Dependabot と security-sweep が当面重複する** — 役割は分けたが、同じ CVE が 2 箇所で見える期間が生じる。sweep の軽量化は Dependabot の実績を見てから
- **CodeQL は default setup を諦める** — workflow 方式を選んだ分、GitHub 側の改善 (自動言語検出等) の恩恵は受けにくい
- **102 件のトリアージ宿題は消えない** — ADR 0038 が予告したとおり。本 ADR は「実行時依存を先に潰す」順序を与えるだけで、ビルド時の 62 件は残る
- **Phase 1 は PO のクリックに依存する** — ADR 0038 が避けた依存を、今回は意図的に受け入れている。クリックされなければその層は空のままなので、**needs-human として追跡する**

## Considered Options

- **A: 何もしない** — 実測で `.env` の公開経路と poison document が見つかっているため不採用
- **B: 検査ツールを一括導入** — Issue #313 が明示的に禁じている。誤検知でアラートが読まれなくなる失敗 (ADR 0038 の「実績ゼロ」と同型) を招く
- **C: 採用案 — 無料枠優先 + 層の責任分担 + 4 フェーズ**
- **D: アプリ内側 (Phase 2) を最優先** — 本丸ではあるが設計判断を伴い時間がかかる。その間、`.env` の穴と action の SHA 未固定が開いたままになる。**塞ぐのが速い順**を優先した
- **E: 有料 SaaS で一気に可視化** — 追加課金ゼロの driver と衝突。不採用

## 動作検証 (この ADR が実装されたと言える条件)

1. `.env` を作って `git status` に出ないことを実測 (Phase 0-1) ✅ 実施済み
2. 上限を超える入力が BFF で拒否され、**正常な操作は壊れない**ことをテストで確認 (Phase 0-2)
3. 壊れた doc が 1 件 Cosmos にあっても Problem 一覧が 200 を返すことをテストで確認 (Phase 0-3)
4. `pip-audit` の検出件数が **before/after で実測**として減る (Phase 0-4)
5. 全 workflow の `uses:` が 40 桁 SHA になっていることを機械で確認 (Phase 0-5)
6. CodeQL が PR で実際に走り、**PR 所要時間の悪化が許容範囲**であることを実測 (Phase 0-6)
7. Dependabot が有効化後に立てる PR が**洪水にならない**ことを 1 週間観測 (Phase 0-7 / Phase 1)
8. PR ブランチで `check.py` を書き換えても required check に success を貼れないことを確認 (Phase 0-8)
9. **秘密文字列を含む push が GitHub 側で拒否される**ことを実測 (Phase 1 — テスト用のダミー鍵で)
10. 状況ページに CodeQL の行が出て 🟢/🔴 で判定できる (CLAUDE.md: 自動化を足したら `watchers.json` に 1 行足す)

## Links

- [Issue #313](https://github.com/yomote/mind-inbox/issues/313) (調査と計画) / [#316](https://github.com/yomote/mind-inbox/issues/316) (初回 sweep 102 件) / [#317](https://github.com/yomote/mind-inbox/issues/317) (予算アラート不在) / [#311](https://github.com/yomote/mind-inbox/issues/311) (action の SHA 固定) / [#309](https://github.com/yomote/mind-inbox/issues/309) (E2E 暗号鍵の指紋)
- 審査基準: [security-rubric.md](../../../../.github/claude/security-rubric.md) (S1〜S7 — 本 ADR は基準ではなく**その照合機構**を扱う)
