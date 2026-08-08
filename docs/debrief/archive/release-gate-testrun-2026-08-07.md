# Release Gate (0bba631 → dev) — テストラン 2026-08-07

🔴 **NO-GO** — 3 judge (QA / security / biz-owner) が独立に blocker を報告し release-judge が git 実状態で裏取り: ①実 BFF 構成でホーム主導線「新しい相談を始める」が無反応で UC-01 の入口に到達不能 (入口崩壊)、②対象 ref は EasyAuth 有効化パラメータを含まず未認証で公開デプロイされる (認可 OFF 出荷 — 修正 dbea3a1 は ref 外)。

> テストラン注記: 正式なリリース PR ではないため PR スレッド投稿は無し。基点 `e15b11a` は見立て。判定はレポートまで — merge / deploy はしない。
> 実行記録: 初回の security / QA 並列起動はセッション使用量上限で中断 → 上限リセット後に新品コンテキストで最初から再実行して完走。security + QA 並列 → biz-owner-reviewer 直列 → release-judge 集約の順で実施。
> qa-reviewer が作成したテストコード (未 commit、パスは QA レポート参照): `apps/frontend/e2e/playwright.config.ts` (ハーネス設定) / `bff-stub-server.cjs` (BFF stub 起動) / `tests/qa-acceptance.spec.ts` (既存 7 本) / `tests/qa-acceptance-2.spec.ts` (新規 6 本 — session.mdx §13 / new-consultation.mdx バリデーション / FR-8 編集トリアージ / problem-list.mdx フィルタ・空状態)。biz-owner のスクショはセッションのスクラッチ領域のみ (リポジトリ外)。

---

## Release Judge レポート — Mind Inbox リリースゲート (テストラン)

**対象**: ref `0bba631` (range `e15b11a..0bba631`) / 環境: dev / 入力: 4 レポート (開発 / QA / セキュリティ / ビジネスオーナー) すべて受領

## Verdict

**🔴 NO-GO** — QA・セキュリティ・ビジネスオーナーの 3 者が独立に blocker を報告し (うち 2 件は同根の「入口崩壊」、1 件は「認可 OFF のまま公開デプロイ」)、いずれも git 実状態で裏が取れたため。

**同根 blocker A (入口崩壊)** — git で裏取り済み: ホームの「新しい相談を始める」は `Router.tsx:185` で `handleStartConsultation()` を直接呼び、`Layout.tsx:483` が `startNewConsultation(concern.trim())` に**空文字**を渡す。BFF は `router.ts:264` で `concern: z.string().min(1)` により拒否 (一方 `deriveTitle` の空文字→「相談セッション」分岐はデッドコード化)。`transition("newConsultation")` の呼び出し元は 0 件 (`git grep` で確認) — 実 BFF ビルドでは**相談を開始する UI 経路が存在しない**。MDX (home.mdx / new-consultation.mdx = ADR 0005 の真実) と逆向き。

**同根 blocker B (認可 OFF 出荷)** — git で裏取り済み: `git show 0bba631:cicd/iac/main-bootstrap.parameters.json` に `applyFunctionAuthLockdown` / `functionAuthEntraClientId` が無く、EasyAuth 無効 + 認証無効フロントが出荷される。直後の `dbea3a1` (#78) の commit message 自体が「認証無効ビルドの出荷を防ぐ」とこの穴の実在を裏付けており、**0bba631 はこの修正を含まない**。

## チェックリスト

| 項目                                                      | 判定                                        | 証拠                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **R0-1** 開発機能一覧 ⇔ QA マトリクスの機能集合一致       | **PASS**                                    | `git log e15b11a..0bba631` の 10 commit (#57/#58/#60/#65/#71/#72/#73/#74/#75/#76) と QA マトリクスが一致。infra/ci 4 件は QA が明示的に対象外宣言し security が担当 — 無言の抜けなし                                                                                                                                                                           |
| **R0-2** 「実装なし / 部分的」の機能が残っていないか      | **FAIL (人間判断へ)**                       | QA マトリクス: FR-4 永続化が**部分的** (`problemRepository.ts` は in-memory、`TODO(PoC)` — requirements §6「in-memory を脱却」と §8「v1 は in-memory」が真実ソース内で矛盾) / relink・merge が **UI 経路なし** (FR-8 一部未達)                                                                                                                                 |
| **R0-3** 「やらなかったこと」の明示                       | **FAIL**                                    | 開発レポート自身が「明示的なスコープ宣言なし (テストランのため基点は見立て)」と記載。rubric 上、無言のスコープ縮小は FAIL (正式リリースでは必須)                                                                                                                                                                                                               |
| **R0-4** プロダクトコンセプト整合 (企画観点)              | **PASS (条件付き)**                         | 中身は ADR 0007 (Problem 中心 2 層) の直接実装で、コンセプト「モヤモヤ→構造化→地図」を前進させる。biz レポートも抽出レビュー (🔁4回目) と詳細タイムラインを「コンセプトを初めて体感できる出来」と評価。ただし blocker A により**リリース構成ではコンセプトの 1 歩目 (話す) が始められない** — 出しても前進しない。ホームに蓄積が見えない (biz feel) は次期課題 |
| **R1-1** CI green (レイヤ別)                              | **L1+L2: UNKNOWN / L0: UNKNOWN / L3: FAIL** | L1+L2: 呼び出し元・開発レポートは 0bba631 push で success と申告するが、このセッションは GitHub API 遮断 (`gh` 不在、API 403「GitHub access is not enabled」) で**実行結果を独立確認できず** (テストファイルの実在は QA が確認済み)。L0: placeholder (rubric 規定で green 扱い不可)。L3: QA 実測 13 本中 **2 fail**                                            |
| **R1-2** QA 受け入れテスト (L3) 実行・pass                | **FAIL**                                    | 11/13 pass だが fail 2 件が主導線 (home.mdx 導線 / new-consultation.mdx 空入力可) で、blocker A と同根。切り分け済み (アプリ側)。なお実装者 L2 `router.test.ts:154` "rejects empty concern" は MDX と**逆向きの挙動を固定**している                                                                                                                            |
| **R1-3** 未解決 PR レビュースレッド                       | **N-A**                                     | テストランでリリース PR が存在しない                                                                                                                                                                                                                                                                                                                           |
| **R1-4** security blocker = 0 / 急所スキャン UNKNOWN なし | **FAIL**                                    | blocker 1 件 (EasyAuth OFF 出荷 — 上記 B、git 裏取り済み)。major 2 件 (予算アラート未作成 / —)。急所 (依存: npm・pnpm・pip audit 実行済み / 秘密情報: gitleaks 0 件) は覆われているが、semgrep・動的チェック (未認証 curl 実測) は UNKNOWN                                                                                                                     |
| **R1-5** qa-reviewer blocker = 0                          | **FAIL**                                    | blocker 1 件 (入口崩壊 — 上記 A、git 裏取り済み)。major 2 件 (FR-4 永続化 / relink・merge UI 無し)                                                                                                                                                                                                                                                             |
| **R1-6** biz-owner blocker = 0 / ウォークスルー実施済み   | **FAIL** (実施は済)                         | 実操作ウォークスルーは実 BFF + モック両構成で**実施済み** (スクショあり)。blocker 1 件 (同 A、再現 2/2)。major 3 件 (一時保存の再開導線なし / 危機時サポートに連絡先ゼロ / UI仕様プレビューが白画面スタックトレース)。実 BFF での下流全画面は UNKNOWN (入口 blocker で到達不能)                                                                                |
| **R2-1** 不可逆な変更なし                                 | **PASS**                                    | SQL 撤去 (#72) は未使用リソースの撤去で ADR 0013 は **Accepted** (2026-08-06, design-gate #69 — `git show` で確認)。Container Apps 3→1 (#76) は IaC で可逆。公開 API 破壊・課金追加なし (むしろコスト削減)。永続データは存在しない (in-memory)                                                                                                                 |
| **R2-2** ADR 級判断に ADR あり                            | **PASS**                                    | ADR 0013 (Accepted) / ADR 0014 (Proposed — docs/skill のみで可逆、規定どおり承認キューに残る) が range 内に実在                                                                                                                                                                                                                                                |
| **R2-3** 環境変数・シークレットが deploy 先に設定済み     | **FAIL**                                    | 新規 `VITE_BFF_BASE_URL` / `VITE_ENTRA_CLIENT_ID` / `VITE_ENTRA_TENANT_ID` は deploy-frontend.sh が deployment outputs から解決する設計だが、0bba631 の parameters.json に認可パラメータが無いため **outputs が空 → 認証無効ビルド** (blocker B と同根)。`budgetContactEmails` も未設定で予算アラート不成立 (security major)                                   |
| **R3-1** rollback 手順の特定                              | **PASS**                                    | ghcr は `sha-<full-sha>` 不変タグ + `IMAGE_TAG=sha-<sha>` でのロールバック手順が `docs/runbooks/ghcr-images.md` に明記 (`git show 0bba631:` で確認)。Functions/SWA は前 ref からの再デプロイ (deploy scripts) で戻せる (zip/バージョンの保管はなく再ビルド前提な点は注記)                                                                                      |
| **R3-2** マイグレーションのロールバック安全性             | **N-A**                                     | SQL 未プロビジョニング (enableSql=false 既定)・マイグレーション同梱なし                                                                                                                                                                                                                                                                                        |
| **R3-3** smoke-test が変更面をカバー                      | **FAIL (部分的)**                           | smoke-test.sh は更新され EasyAuth 401 / CORS preflight 検査を追加 (良)。ただし (a) **auth-off 時は warn 止まりで緑通過** — blocker B を検知できない (security 裏付けと `git show 0bba631:` の該当分岐で確認)、(b) 新規面 `consultation.extract` / `problem.*` は未カバー (health.ping のみ)                                                                    |
| **R4-1** Runbook 追従                                     | **PASS**                                    | `docs/runbooks/ghcr-images.md` (新規 85 行) / `entra-spa-auth-and-budget.md` (新規 118 行) / `azure-oidc-cd-setup.md` 更新を range diff で確認                                                                                                                                                                                                                 |
| **R4-2** コスト構造変更が予算前提 (ADR 0013) と整合       | **PASS (注記あり)**                         | SWA Free 化 / ACR 廃止 / Container Apps 3→1 はすべて ADR 0013 (Accepted) のコスト削減方針そのもの。注記: 二重防御の予算アラートは 0bba631 の CI 経路では作成されない (R2-3 / security major)                                                                                                                                                                   |

## Blocker / 残リスク一覧

**Blocker (マージ/デプロイ不可):**

1. **入口崩壊**: 実 BFF 構成でホーム主 CTA が無反応 (空 concern → BAD_REQUEST、catch なし・エラー UI なし)。UC-01 に UI から到達する手段がない。QA (L3 fail 2 件) + biz (再現 2/2) + release-judge の git 裏取りの 3 点で確定
2. **認可 OFF 出荷**: 0bba631 のデプロイは EasyAuth 無効の Function App + 認証無効フロント。未認証で Problem 読み書き・OpenAI 課金消費が可能。修正 `dbea3a1` (#78) は ref 外。予算アラート (検知網) も同経路で不成立

**残リスク (major / UNKNOWN):**

- FR-4 永続化が in-memory (scale-to-zero で「蓄積」が静かに消える) — requirements 内の矛盾未解消
- relink / merge の UI 経路なし (誤グルーピング時にユーザーが最終編集者になれない)
- 一時保存の再開導線なし (「保存した」ものが実質消える) / 危機時サポートに連絡先ゼロ / UI仕様プレビューが本番導線に露出
- L1+L2 CI green を release-judge が独立検証できていない (GitHub API 遮断) / L0 placeholder
- 実 LLM での抽出品質・再出現検知 (FR-7)・Entra 認証フロー・Functions HTTP ラッパー経由・音声入出力は全レポートで UNKNOWN
- semgrep / 動的セキュリティチェック未実施 / react-router runtime high 系

## 作業指示リスト

| 宛先               | 指示 (具体的に)                                                                                                                                                                                                                                                                                                                                                                                            | 解除される項目              |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| 実装               | **[blocker A]** home.mdx どおりホーム CTA を `newConsultation` 画面遷移に直す (ADR 0005: MDX が真実、乖離時は実装を直す)。あわせて new-consultation.mdx「空入力でも開始可能」に合わせ BFF `router.ts:264` の `min(1)` を外し `deriveTitle` の既存分岐を活かすか、MDX 側の真実を人間と確定。逆向きの L2 `router.test.ts:154` も修正。`handleStartConsultation` に catch + エラー表示を追加 (無音失敗の根絶) | R1-2, R1-5, R1-6, R0-4 条件 |
| 実装 / 人間        | **[blocker B]** リリース ref を `dbea3a1` (#78) 込みで切り直す (0bba631 単体を公開 URL にデプロイしない)。テストランで既にデプロイ済みなら、Function App への未認証 curl が 401 を返すか即時実測                                                                                                                                                                                                           | R1-4, R2-3                  |
| 実装               | smoke-test.sh: (a) dev の恒常運用では auth-off (`EASYAUTH_ENABLED!=true`) を warn でなく ng に、(b) `problem.list` / `consultation.extract` の疎通チェックを追加                                                                                                                                                                                                                                           | R3-3                        |
| 実装               | `extractor.py:143` のパース失敗ログから raw 本文を除去 (長さ/ハッシュのみ)。次回リリースレポートに「やらなかったこと」節を必須化                                                                                                                                                                                                                                                                           | security minor, R0-3        |
| 実装               | biz major 3 件: 一時保存の再開導線 / 危機時サポート画面に実連絡先 (公開前は blocker 相当) / UI仕様プレビューを本番ビルドから除外                                                                                                                                                                                                                                                                           | R1-6 major                  |
| qa-reviewer        | blocker A 修正後、L3 13 本を再実行 (fail 2 件の pass 確認)。可能なら Functions HTTP ラッパー (`trpc.ts`) 経由の構成で                                                                                                                                                                                                                                                                                      | R1-1(L3), R1-2              |
| security-reviewer  | 新 ref のデプロイ後、未認証 curl 401・予算アラート実在・CORS preflight を実 URL で実測 (静的追跡の UNKNOWN 解消)。semgrep はローカルルール等の代替手段を検討                                                                                                                                                                                                                                               | R1-4 UNKNOWN                |
| biz-owner-reviewer | blocker A 修正後、実 BFF 構成で下流全画面 (対話→抽出→レビュー→一覧・詳細・トリアージ) を再ウォークスルー (現状すべてモック観察のため UNKNOWN)                                                                                                                                                                                                                                                              | R1-6 UNKNOWN                |
| 人間               | ① FR-4 の真実を確定 (requirements §6「in-memory 脱却」vs §8「v1 は in-memory」— in-memory 容認なら §6 を修正し「消える」旨の UI 明示を検討) ② relink/merge を v1 スコープに含めるか判断 (use_cases ⇔ MDX の整合含む) ③ L1+L2 CI green の証跡 (Actions run URL) を次回ゲートで提示 ④ QA 探索チャーター 3 件 (実 LLM 抽出 / scale-to-zero 跨ぎの蓄積消失 / iOS 実機音声) の実施                              | R0-2, R1-1, QA major        |

**判定の要約**: 3 judge の blocker はすべて独立した実測 + release-judge の git 裏取りで確定しており、「玄関が開かない」×「鍵がかかっていない」の 2 点だけで NO-GO は動きません。一方、修正はどちらも小さく特定済み (`dbea3a1` は既に main にあり、入口修正は導線 1 本 + バリデーション 1 行の方向性が真実ソースで確定可能) で、抽出レビュー・詳細画面はコンセプトを体現できていると 2 judge が評価しています。上記 blocker 2 件 + 再検証で次回ゲートは大きく前進する見込みです。

---

<details><summary>開発リリースレポート</summary>

## 開発リリースレポート (e15b11a..0bba631, テストラン)

- 入る機能 / 変更:
  - #57 ai-agent: Phase A — /extract (Mention 抽出 + グルーピング + テーマ分類)
  - #58 bff: Phase B core — problem.\* ルーター + /extract 結線 + Problem リポジトリ
  - #60 bff: Phase B triage — problem.triage (8 アクション)
  - #65 frontend: Phase C core — problems.ts を実 BFF に結線
  - #71 frontend: Phase C — organize→extract の UI 移行 (困りごと抽出を主導線に)
  - #72 infra: ADR 0013 + 未使用 SQL 撤去 / #73 ci: ghcr 事前ビルド / #75 infra: SWA Free 化 + EasyAuth + 予算アラート / #76 iac: Container Apps 環境 3→1 統合
- やらなかったこと: 明示的なスコープ宣言なし (テストランのため基点は見立て)
- 実装者テストの状況: router.test.ts / problems.test.ts / msal.test.ts / test_extractor.py / test_l2_endpoints.py が追加・変更。CI (test.yml) は 0bba631 の push で success (2026-08-06)。ただし L0/L3 レイヤは placeholder が残っている (docs/testing/strategy.md 記載)
- 既知の懸念: 対象 ref の直後に main へ dbea3a1「fix(iac): 認可パラメータを parameters.json に固定 — 認証無効ビルドの出荷を防ぐ (#69)」が積まれており、対象 ref 0bba631 はこの修正を含まない (`git show dbea3a1` で内容確認可)

</details>

<details><summary>QA レポート (受け入れマトリクス / テスト実行結果)</summary>

## QA レポート — リリースゲート テストラン (dev / ref `0bba631`, range `e15b11a..0bba631`)

**verdict: 🧪 要修正 (blocker あり)**

理由 (1 行): 実 BFF 構成 (dev リリースの構成) でホームの主導線「新しい相談を始める」が無反応の行き止まりになり、UC-01 の入口にユーザー操作で到達できない — 受け入れテスト fail 2 件が同根で確認済み。

## 前提と検証環境

- 審査対象時点のコードは `git show 0bba631:` で確認。作業ツリーの `apps/` は 0bba631 と一致 (差分は QA 用 e2e 足場のみ) を `git diff 0bba631 -- apps/` で確認済み
- L3 構成: Vite dev (5173, dev=standalone で認証ゲートはスキップ・API は実 BFF) + BFF stub ハーネス (7071, `AI_AGENT_BASE_URL`/`VOICEVOX_BASE_URL` 未設定 = stub モード。strategy.md §3 L3 準拠)。Azure Functions Core Tools が無いため、ビルド済み `apps/bff/dist` の appRouter を `@trpc/server` standalone アダプタで公開する形で代替 (Functions HTTP ラッパー `trpc.ts` 自体は未検証 → UNKNOWN 参照)
- `npm run test:e2e` はリポジトリ側がまだ placeholder のため、`apps/frontend/e2e/` で `npx playwright test` を直接実行
- L0〜L2 は再レビューしない (rubric 出力ルール 4)。CI (test.yml) は 0bba631 push で success という呼び出し元申告を、テストファイルの実在確認 (`router.test.ts` / `problems.test.ts` / `test_extractor.py` / `test_l2_endpoints.py`) とあわせて参照した

## Q1 — 受け入れマトリクス

| 機能 (導出元)                                                                              | 実装                                                                                                           | 受け入れテスト                                                                             | 判定                                                                                                                                                                 |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #57 ai-agent /extract: Mention 抽出 + グルーピング + テーマ分類 (FR-2/FR-3, UC-01 段1/段2) | あり (`app/extractor.py` / `app/main.py` / `app/schemas.py` で実在確認)                                        | L2 (実装者, CI green) + L3 は stub 経路のシナリオのみ                                      | OK (実 LLM での抽出品質は UNKNOWN)                                                                                                                                   |
| #58 bff problem.\* ルーター + /extract 結線 + Problem リポジトリ (FR-2, UC-01)             | あり (`router.ts` の consultation.extract / materializeExtraction / problem.list・get, `problemRepository.ts`) | L2 + **L3 GP-1 pass** (抽出 → レビュー → 一覧 → 詳細)                                      | OK                                                                                                                                                                   |
| #60 problem.triage 8 アクション (FR-8, UC-04)                                              | あり (discriminatedUnion 8 種を `router.ts` で確認)                                                            | L2 全 8 種 + L3 で resolve/shelve相当/reopen/dismiss/editTheme/editTitle を UI 経由で pass | OK — ただし **relink / merge は UI 操作経路なし** (findings #3)                                                                                                      |
| #65 frontend problems.ts 実 BFF 結線 (Phase C core)                                        | あり (`problems.ts` の tRPC 結線 + triage 写像)                                                                | L1 (実装者) + L3 全シナリオがこの結線経由で pass                                           | OK                                                                                                                                                                   |
| #71 organize→extract UI 移行 — 困りごと抽出を主導線に (dialogue session.mdx §13)           | あり (`SessionControls.tsx`: 抽出=primary / 整理結果へ=secondary 併存)                                         | **L3 pass** (主導線遷移 + 旧導線併存 + 空文字送信不可)                                     | OK — ただし入口 (ホーム導線) が blocker (findings #1)                                                                                                                |
| FR-6 ライフサイクル: 解決/棚卸し/再オープン、解決済みは既定一覧から外れ参照可 (UC-04)      | あり                                                                                                           | **L3 pass**                                                                                | OK                                                                                                                                                                   |
| FR-9 / UC-05 プラン接続 (problem.createPlan, 状態不変)                                     | あり                                                                                                           | **L3 pass**                                                                                | OK                                                                                                                                                                   |
| FR-4 蓄積・永続化「in-memory を脱却している」(requirements §6)                             | **部分的** — `problemRepository.ts` は in-memory (`TODO(PoC): 本番は Cosmos DB`、再起動で消える)               | なし                                                                                       | **要判断** (findings #2。requirements §6 と §8 が食い違い)                                                                                                           |
| FR-7 再出現検知 / UC-03 (🔁 バッジ・再燃提示)                                              | あり (extractor の existing 判定 + `materializeExtraction` の追記経路)                                         | L2 のみ。stub は常に new 1 件を返すため **L3 では駆動不能**                                | 実環境で UNKNOWN → チャーター 1                                                                                                                                      |
| #75 frontend 認証移行 (msal.ts / EasyAuth 前提)                                            | あり (`msal.ts`、`authEnabled=false` 時は素通し)                                                               | L1 (msal.test.ts) のみ。実 Entra が必要で L3 不能                                          | UNKNOWN (実環境確認が必要)                                                                                                                                           |
| #72 / #73 / #75(iac) / #76 infra・ci                                                       | —                                                                                                              | —                                                                                          | QA 実行対象外。作業ツリーの `cicd/iac/` は 0bba631 と不一致、かつ 0bba631 は直後の修正 dbea3a1 (iac 認可パラメータ) を含まない — release-judge / security 側で判定を |

## Q3 — テスト実行結果 (L3 E2E)

テストコードのパス (指示どおり **commit していない**。プロダクトコードは 1 行も変更していない):

- `/home/user/mind-inbox/apps/frontend/e2e/playwright.config.ts` — ハーネス設定 (既存足場を再利用。`executablePath: /opt/pw-browsers/chromium`)
- `/home/user/mind-inbox/apps/frontend/e2e/bff-stub-server.cjs` — BFF stub モード起動ハーネス (既存足場を再利用。dist 成果物をそのまま束ねるだけ)
- `/home/user/mind-inbox/apps/frontend/e2e/tests/qa-acceptance.spec.ts` — 既存 7 本。各アサーションを真実ソース (use_cases UC-01〜05 / 各 MDX) と突合して妥当性を確認の上採用
- `/home/user/mind-inbox/apps/frontend/e2e/tests/qa-acceptance-2.spec.ts` — **今回新規 6 本** (session.mdx §13 受け入れ条件 / new-consultation.mdx バリデーション / FR-8 編集トリアージ / problem-list.mdx フィルタ・空状態)

実行結果: **13 本中 11 pass / 2 fail** (Chromium, 34.6s)

| テスト                                                                                                     | 結果                                                        |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| [L3] GP-1 ゴールデンパス UC-01→UC-02: 話す → 抽出 → 一覧・詳細 (新規/既存内訳・🆕・種バッジ・タイムライン) | pass                                                        |
| [L3] UC-04 棚卸し: 解決 → 既定一覧から除外 → 「棚卸し済みも表示」で参照 → 再オープン                       | pass                                                        |
| [L3] UC-05 次の一歩: プラン生成で詳細に表示・状態は不変                                                    | pass                                                        |
| [L3] UC-01 その場トリアージ: 却下で一覧から消える                                                          | pass                                                        |
| [L3] 入力の現実: 絵文字・改行・長文・日英混在で対話継続 (Q2)                                               | pass                                                        |
| [L3] UI 挙動: 抽出レビューでリロード → ホーム復帰・蓄積は一覧から辿り直せる (Q2)                           | pass                                                        |
| [L3] session.mdx: 空文字/空白のみは送信不可・入力で活性                                                    | pass                                                        |
| [L3] session.mdx: 危機時サポート / 一時保存・中断への遷移                                                  | pass                                                        |
| [L3] session.mdx: 旧「整理結果へ」(secondary 併存) で result へ遷移                                        | pass                                                        |
| [L3] FR-8: 主テーマ付け替え + タイトル編集が保存され一覧にも反映                                           | pass                                                        |
| [L3] problem-list.mdx: テーマフィルタ絞り込み・該当ゼロは空状態案内・並び替えトグル提示                    | pass                                                        |
| [L3] home.mdx 導線: ホーム「新しい相談を始める」で行き止まりにならない                                     | **fail (アプリ側)** — /home に留まりエラー表示なし          |
| [L3] new-consultation.mdx: 空入力でも開始可能 (タイトル「相談セッション」)                                 | **fail (アプリ側)** — BFF が BAD_REQUEST で拒否し遷移しない |

**fail の切り分け (両方アプリ側・同根)**: BFF `consultation.start` は `concern: z.string().min(1)` で空文字を拒否する。一方 (a) new-consultation.mdx (真実, ADR 0005) は「空入力でも開始可能 (タイトルは『相談セッション』になる)」と明記し、BFF 自身も `deriveTitle` に空文字 → 「相談セッション」の分岐を持つ (デッドコード化)。(b) ホームの「新しい相談を始める」は home.mdx の「→ `newConsultation`」に反して `handleStartConsultation()` を直接呼び (`Router.tsx:185`)、concern は常に空 → 実 BFF では必ず BAD_REQUEST。例外は `void` で捨てられ (catch なし・エラー UI なし)、ユーザーには「押しても何も起きないボタン」になる。さらに `transition("newConsultation")` はコードのどこからも呼ばれておらず、`/consultations/new` へ UI から到達する導線が存在しないため、**実 BFF ビルド (dev の `.env.production` は `VITE_USE_MOCK=false`) では相談を開始する手段がない**。なお実装者の L2 (`router.test.ts:154` "rejects empty concern") はこの実装挙動の側を固定しており、MDX と逆向き。テスト実行前に curl で BFF 単体でも BAD_REQUEST を再現確認済み。この挙動自体は基点 e15b11a にも存在するが、本 range (#65/#71) で UI が実 BFF 結線に切り替わったことで初めてユーザー影響が顕在化する。

## Findings

| Severity    | 対象                              | 指摘                                                                                                                                                                                                                                                                                                                                                                           | 根拠 (真実ソース)                                                                                                                                              |
| ----------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **blocker** | ホーム主導線 / consultation.start | 実 BFF 構成でホーム「新しい相談を始める」が無反応 (空 concern → BAD_REQUEST、エラー表示なし)。newConsultation 画面へ UI から到達不能で、UC-01 の入口が塞がる。修正候補: home.mdx どおり `newConsultation` へ遷移させる、または BFF が空 concern を許容 (deriveTitle の既存分岐を活かす)                                                                                        | home.mdx「新しい相談を始める -> `newConsultation`」/ new-consultation.mdx「空入力でも開始可能」/ ADR 0005 (MDX が真実、乖離時は実装を直す)。L3 fail 2 件で再現 |
| major       | Problem 永続化 (FR-4)             | `problemRepository` は in-memory で再起動で消える。requirements §6 FR-4 受け入れ「in-memory を脱却している」と §8「v1 は in-memory」が真実ソース内で矛盾。dev は scale-to-zero 方針 (NFR-4) なので「溜まる・見える」(UC-02) がセッションを跨いで静かに成立しない。どちらが v1 の真実か明確化を                                                                                 | requirements.md §6 FR-4 vs §8 / `apps/bff/src/repositories/problemRepository.ts` の `TODO(PoC)` コメント                                                       |
| major       | トリアージ relink / merge         | problem-detail.mdx 振る舞い「誤グルーピングは『再リンク』で直す」・UC-03 代替 4a (別 Problem として切り出す) に対し、relink/merge は BFF API のみで UI 操作経路が無い。#60 の約束 (BFF 8 アクション) は満たすが、誤グルーピング時にユーザーが最終編集者になれない (FR-8 の一部未達)。MDX の UI 要素にも relink が無いため、真実ソース間 (use_cases ⇔ MDX) の整合もとる必要あり | use_cases.md UC-01 手順5 / UC-03 4a / FR-8 / problem-detail.mdx 振る舞い                                                                                       |
| minor       | home.mdx との UI 要素乖離         | home に「設定 / プライバシー」ボタンが無い (アカウントメニューへ移動)。逆に「困りごと一覧」ボタンは MDX 未記載。MDX が真実なのでどちらかへ寄せる                                                                                                                                                                                                                               | home.mdx UI要素・遷移 / `HomeScreen.tsx` / ADR 0005                                                                                                            |
| minor       | 入力欄プレースホルダ              | dialogue session.mdx §5.3 は「ここに入力」、実装は「ここに入力 / 話して入力」                                                                                                                                                                                                                                                                                                  | dialogue session.mdx §5.3 / `SessionComposer.tsx:53`                                                                                                           |
| charter     | 抽出の BFF⇔ai-agent 継ぎ目        | `consultation.extract` は sessionId のみを渡し、会話本文は ai-agent 側 in-memory SessionRepository 依存。ai-agent の scale-to-zero 再起動を挟むと抽出が空振りする可能性 (stub では検証不能)                                                                                                                                                                                    | `router.ts` extract / `ai-agent app/main.py` のシングルトン repo コメント                                                                                      |

## UNKNOWN (実行できなかった項目)

- **Azure Functions HTTP ラッパー経由の BFF 起動** (`apps/bff/src/functions/trpc.ts`): Core Tools 不在のため standalone アダプタで代替。router 以下は検証済みだが Functions トランスポート層は未検証
- **Entra ID 認証フロー (#75 frontend)**: 実テナントが必要。`authEnabled=false` の素通し分岐のみ通過
- **実 LLM での抽出品質・再出現検知 (FR-7/UC-03)・抽出ゼロ件の受け止め表示**: stub は常に「新規 1 件」固定のため L3 で駆動不能 (L2 で分岐はカバー済み)
- **音声入出力 (FR-1 音声 / TTS)**: headless Chromium では Web Speech 非対応
- **infra/ci 変更 (#72/#73/#75/#76)**: QA 実行対象外。0bba631 が直後の iac 修正 dbea3a1 を含まない点も含め release-judge へ

## Q4 — 探索チャーター (人間へのおすすめ、最大 3 件)

1. **dev 実環境の抽出フロー**を**実 LLM での多論点・雑談のみ・再言及の 3 パターンの吐き出し**で探索し、**抽出ゼロ件の受け止め表示・複数 Mention 分割・🔁再出現/再燃バッジの提示妥当性** (stub では検証不能な FR-2/FR-7) を探す
2. **dev 環境の Problem 一覧**を**30 分以上の放置後の再操作 (scale-to-zero 再起動を跨ぐ)** で探索し、**蓄積の消失・古い sessionId での抽出時のエラー提示の欠如**を探す
3. **スマホ実機 (iOS Safari)** を**音声入力 → 抽出 → 読み上げの主導線**で探索し、**Web Speech 非対応表示・自動再生ブロック・タップ起点の音声解錠の壊れ**を探す

---

後処理: 起動した BFF stub / Vite プロセスはすべて停止済み。プロダクトコードの変更なし (`git status` はテストハーネスの untracked のみ)。テストコードは指示どおり未 commit でパスに残置。

</details>

<details><summary>セキュリティレポート (スキャン実行状況 / findings)</summary>

## セキュリティレビュー レポート — mind-inbox release-gate (テストラン)

## Verdict (セキュリティ)

🔒 **要修正 (blocker あり)** — リリース対象 ref `0bba631` は EasyAuth を既定 OFF のままデプロイする。公開 URL に未認証で OpenAI 課金と機微データ(モヤモヤ)がさらされる。これを塞ぐ `dbea3a1` (#78) は ref に含まれない。

対象: `e15b11a..0bba631` (dev / テストラン)

---

## スキャン実行状況

| ツール                                             | 実行 (✅/UNKNOWN)             | 生の検出数                                          | 判定後に残った数                                                                                                                                                    |
| -------------------------------------------------- | ----------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| gitleaks (git log e15b11a..0bba631)                | ✅ (GitHub releases から取得) | 0                                                   | 0                                                                                                                                                                   |
| git grep 秘密パターン (diff)                       | ✅                            | 4 (全て `secrets.GITHUB_TOKEN` 等の正当参照/削除行) | 0                                                                                                                                                                   |
| npm audit (bff, lockfile)                          | ✅                            | 9 (mod3/high3/crit3)                                | 0 (全て devDependency)                                                                                                                                              |
| pnpm audit (frontend, lockfile)                    | ✅                            | 44 (crit0/high28/mod14/low2)                        | 2 (runtime: react-router)                                                                                                                                           |
| pip-audit (voicevox requirements.txt)              | ✅                            | starlette 0.46.2 × 9                                | 1 (到達性低)                                                                                                                                                        |
| pip-audit (ai-agent uv.lock → 手動照合)            | ✅ (バージョン抽出のみ)       | starlette 1.0.0 (大半修正済)                        | 0                                                                                                                                                                   |
| bandit (ai-agent / voicevox app)                   | ✅                            | 0                                                   | 0                                                                                                                                                                   |
| semgrep (auto / p/security-audit)                  | UNKNOWN                       | —                                                   | — (プロキシが semgrep.dev レジストリを 403 で遮断。ローカルルール無し)                                                                                              |
| trivy / osv-scanner / checkov                      | UNKNOWN                       | —                                                   | — (環境に無し。IaC は目視 + grep で代替)                                                                                                                            |
| 動的チェック (未認証 curl / 外部通信観察 / ヘッダ) | UNKNOWN                       | —                                                   | — (Azure Functions Core Tools 無し・env に fastapi 未導入・並行 qa と共有 tree のため起動断念。静的追跡で代替、下記 F1 の実測は smoke-test.sh のロジック検証で代替) |

---

## Findings (セキュリティ)

| Severity    | 箇所 (file:line)                                                                                 | 指摘                                                                                                                                                                                                                                                                                        | 悪用経路 (1 文)                                                                                                                                                                                  | 出所                                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| **blocker** | `cicd/iac/main-bootstrap.parameters.json` (0bba631 時点) + `cicd/scripts/deploy/provision.sh:64` | ref に `applyFunctionAuthLockdown` / `functionAuthEntraClientId` が無く、bicep 既定は `false`/`''` のため `functionEasyAuthEnabled=false` となり authsettingsV2 が作られない → Function App が未認証で公開デプロイされる (deploy-frontend.sh も client ID 空を読んで認証無効フロントを出荷) | 公開された Function ホスト名に `curl https://<func>/api/trpc/problem.list`(や consultation.extract) を無トークンで投げれば、他人のモヤモヤ(Problem)を読み書きでき、OpenAI 課金も自由に消費できる | 目視 (追跡: parameters.json→provision.sh→deploy.yml up→bootstrap-core.bicep:904) / `dbea3a1` が同一穴を修正 |
| **major**   | `cicd/iac/main-bootstrap.parameters.json` + `provision.sh:64`                                    | `budgetContactEmails` も未指定で、`budgetAlertEnabled = enableBudgetAlert && !empty(budgetContactEmails)` が false → CI デプロイ経路では予算アラート(二重防御)も作られない。F1 の課金消費に対する検知網が無い                                                                               | 上記 F1 で誰かが OpenAI を回し続けても、¥3,000 アラートが未作成なので請求で気づけない                                                                                                            | 目視                                                                                                        |
| minor       | `apps/frontend/pnpm-lock.yaml` (react-router 7.13.2)                                             | 出荷 runtime 依存 react-router に high 多数 (open redirect / DoS / CSRF / RSC turbo-stream RCE)                                                                                                                                                                                             | `<Link>`/`useNavigate` へバックスラッシュ経由の相対 URL を仕込めば別オリジンへ誘導しうる (open redirect)                                                                                         | pnpm audit                                                                                                  |
| minor       | `apps/services/voicevox/requirements.txt` (starlette 0.46.2)                                     | 外部 ingress のコンテナに脆弱 starlette。ただし wrapper は StaticFiles/FileResponse/フォームを使わず (`app/main.py` は JSON のみ)、Host ヘッダ由来の認可判定も無いため主要 CVE は到達しない                                                                                                 | 大半は非到達。Range DoS/form limit は該当コード無し                                                                                                                                              | pip-audit                                                                                                   |
| minor       | `apps/services/ai-agent/app/extractor.py:143`                                                    | LLM 生出力を JSON パース失敗時に `logger.warning("Extract JSON parse failed: %r", raw)` で記録。raw はユーザー会話由来 → モヤモヤ本文が Log Analytics に落ちうる (S3)                                                                                                                       | 特定入力でパースを崩せば、その回の相談本文断片がログに残る                                                                                                                                       | 目視                                                                                                        |
| info        | `apps/frontend/pnpm-lock.yaml`, `apps/bff/package-lock.json`                                     | crit/high の大半 (vitest, concurrently, esbuild, vite, postcss 等) は devDependency でビルド/テスト専用。成果物に入らない                                                                                                                                                                   | 出荷物に含まれず到達不可                                                                                                                                                                         | npm/pnpm audit                                                                                              |
| info        | `cicd/scripts/deploy/deploy-ai-agent.sh` / `deploy-voicevox-wrapper.sh` (`--ingress external`)   | ai-agent / voicevox コンテナは external ingress かつ無認可 (レンジ前から)。新規 `/extract` はこの公開コンテナ上で動く。BFF は内部 FQDN 経由だが container 自体は公開                                                                                                                        | 公開 FQDN に直接 `/extract` を打てるが session_repo 依存で影響限定。**本レンジでの新規変更ではない**                                                                                             | 目視                                                                                                        |
| info        | `apps/frontend/src/auth/msal.ts:44`                                                              | アクセストークンを `localStorage` に保存 (XSS 時に盗まれうる)。dev 個人利用の明示判断でコメントあり                                                                                                                                                                                         | XSS が別途成立すればトークン奪取 (XSS 単独 sink は未検出)                                                                                                                                        | 目視                                                                                                        |

---

## 補足・確認事項

- **CORS は認可でない点は実装側も正しく認識**: `bootstrap-core.bicep` の Function CORS は `allowedOrigins=[SWA]` / `supportCredentials:false` で、コメントも「守りは EasyAuth の 401」と明記。frontend の `trpc/client.ts` も `authEnabled` 時のみ Bearer を付与し、EasyAuth を認可層に置く設計は妥当。**問題は F1 のとおり、その EasyAuth 自体が 0bba631 の CI 経路では有効化されないこと**。
- **smoke-test は auth-off を止めない**: `smoke-test.sh` の該当分岐は `EASYAUTH_ENABLED!=true` を warn 止まり (200 reachable なら ok) にするため、認可欠落をゲートで検知できない。F1 が緑で通過しうる裏付け。
- **秘密情報**: gitleaks (10 commits, ~207KB) と diff grep で新規コミット秘密なし。`dbea3a1` が commit する Entra client ID は SPA バンドルに載る公開識別子で秘密ではない (正当)。Log Analytics の `sharedKey` は listKeys() 参照で平文コミットなし。
- **入力検証 (S2)**: 新規 tRPC (`consultation.extract`, `problem.*`, `triage`) は zod + discriminatedUnion で受けており生 dict 受けは無し。pydantic 側 (`schemas.py`) も alias/範囲制約付き。`_coerce_*` で LLM 出力を丸めており injection sink (eval/exec/シェル/動的 import) は未検出。SSRF: ユーザー値を URL 連結する箇所なし (base URL は env)。
- **LLM (S5)**: `requiresApproval`/human-in-the-loop フローは本レンジで撤去されていない。`/extract` プロンプトはユーザー会話をテンプレに埋めるが、出力を HTML 描画/コード実行に流す経路なし (frontend に dangerouslySetInnerHTML 等なし)。

## 推奨対応

1. **(blocker) リリース ref に `dbea3a1` (#78) を含める**、または 0bba631 を公開 URL にデプロイしない。含めれば F1・F2 とも解消 (parameters.json に `applyFunctionAuthLockdown=true` / client ID / budget 運用固定が入る)。テストランで既に公開 URL を出したなら、Function App が未認証で到達可能でないか実測 (未認証 curl が 401 か) を推奨。
2. (提案) 依存スキャン (dependabot/CodeQL) と `pnpm audit --prod` を CI に常設し、runtime 依存 (react-router 等) の高危険度を定期検知。voicevox の starlette/fastapi 系は次回バンプ推奨。
3. (提案) `extractor.py` のパース失敗ログを raw 本文非出力 (長さ/ハッシュのみ) に。

## UNKNOWN (実施できなかった項目)

- 動的チェック全般 (未認証 curl の実 HTTP 応答、外部通信の観察、セキュリティヘッダ/エラー本文の内部情報漏れ) — 起動系ツール不在と共有 tree 制約で未実施。F1 は静的追跡で確定、実 URL での 401 実測は未取得。
- semgrep SAST — プロキシがレジストリを 403 遮断。bandit(Python) は実行できたが 0 件、JS/TS 側の SAST は目視で代替。
- trivy/checkov による Bicep・Dockerfile の設定スキャン — 未導入。目視 + grep で代替。

</details>

<details><summary>ビジネスオーナーレポート (ウォークスルー / 違和感 / 総評)</summary>

## ビジネスオーナーレポート — Mind Inbox リリースゲート (テストラン)

**対象**: ref `0bba631` / dev 環境相当 (フロント `VITE_USE_MOCK=false` + BFF stub モード @ :7071)
**手法**: rubric W1〜W3 に従い Playwright で実操作。スクショは `/tmp/claude-0/-home-user-mind-inbox/25de3838-77be-5437-9f0f-f4c15ee59f05/scratchpad/biz-owner/shots/` 配下。

---

## Verdict (ビジネスオーナー)

**🙅 この状態では出せない (blocker あり)** — 実 BFF 接続では、ホームの主 CTA「新しい相談を始める」が無反応で、相談フローに一切入れない。ゴールデンパスが 1 歩目で完走できない。

---

## ウォークスルーログ

パスはすべて `.../scratchpad/biz-owner/shots/` 配下。**[実]** = 実 BFF 接続 (:5173、リリース対象の構成)、**[モック]** = フロント単体モック (:5174、入口 blocker の切り分けと下流画面の補助観察のため。判定の主軸ではない)。

| ステップ                 | 操作                                                                               | スクショ                                                              | 感じたこと 1 行                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| [実] 初回アクセス        | `http://localhost:5173/` を開く                                                    | `01-home-first-visit.png`                                             | オンボーディングなしで即ホーム。ボタン 4 つだけで「地図が育つ」気配がない                               |
| [実] 相談開始            | 「新しい相談を始める」をクリック (2 回試行 + 5 秒待機)                             | `03-consult-screen-wait.png`, `03-retry-wait-5s.png`                  | **何も起きない**。エラー表示もスピナーもなく、壊れたのか自分が悪いのか分からない (裏では 400)           |
| [実] 別ルート探索        | `/new-consultation` `/consultation` `/session` `/consultations/current` へ直接 URL | `01-try-new-consultation.png`, `01-real-direct-session.png`           | 全部 /home に戻される。相談への入口はこの壊れたボタンだけ                                               |
| [実] 困りごと一覧        | 「困りごと一覧」をクリック                                                         | `04-go-problems.png`                                                  | 「該当する困りごとはありません。」だけ。初回ユーザーに次の一歩の案内がない                              |
| [実] 履歴                | 「履歴・振り返り」をクリック                                                       | `02-go-history.png`                                                   | 同上。空の行き止まり                                                                                    |
| [実] UI仕様プレビュー    | ホームの「UI仕様プレビュー」をクリック                                             | `05-go-specs.png`                                                     | 真っ白画面に生の Vite エラー + スタックトレース。初見なら「壊れたアプリ」と確信する                     |
| [実] 設定                | アカウント → 設定、ダークテーマ切替                                                | `03-click-settings.png`, `06-dark-wait.png`                           | 「ローカルモック版のため…初期化されます」と書いてあり不安。項目は（ダミー）だらけ。ダークテーマは動く   |
| [実] ログアウト          | アカウント → ログアウト                                                            | `03-real-logout.png`                                                  | 「起動画面 / オンボーディング」という開発用語の見出しページに着地                                       |
| [実] BFF 単体確認        | `consultation.start` を curl で直叩き                                              | (ログのみ)                                                            | 非空の concern なら正常応答 = **壊れているのはフロント→BFF の結合 (空文字送信)**                        |
| [モック] 相談開始        | 同じボタンをモックで                                                               | `02-mock-click-new.png`                                               | モックでは対話セッションに遷移する = 実 BFF のみの退行と確定                                            |
| [モック] 対話            | モヤモヤを 2 回入力・送信                                                          | `08-mock-wait-reply2.png`                                             | 受け止め→追加質問のトーンは穏当。ただ毎回「〜を1つ教えてください」の指示形でやや尋問調                  |
| [モック] 抽出レビュー    | 「困りごとを抽出」                                                                 | `09-mock-extract.png`                                                 | **ここは良い**。新規/既存に追加/🔁4回目/引用つきで「蓄積されていく」感覚がある                          |
| [モック] 却下            | 「これは違う（却下）」                                                             | `08-mock-reject.png`                                                  | 押した瞬間カードが消える。確認も取り消しもなく、誤タップが怖い                                          |
| [モック] 一覧→詳細       | 一覧から「転職すべきか迷っている」を開く                                           | `09-mock-to-list.png`, `12-mock-detail-wait.png`                      | **ここも良い**。言及タイムライン・感情タグ・テーマ変更・トリアージ 3 ボタン。コンセプトが形になっている |
| [モック] 整理結果        | セッションから「整理結果へ」                                                       | `06-to-result.png`                                                    | 感情チップと無ラベルの箇条書き 3 行。これが論点なのか提案なのか読み取れない                             |
| [モック] 行動プラン→保存 | 「行動プランへ」→「保存して履歴へ」                                                | `11-to-plan.png`, `09-history-wait.png`                               | 保存→履歴に現れる流れ自体は分かる。履歴下部の「選択中: …」は意味不明                                    |
| [モック] 危機時サポート  | セッションから「危機時サポート」                                                   | `06-click-crisis.png`                                                 | 見出しが「危機時サポート導線」で、中身は一般論 1 文。**電話番号も窓口リンクもない**                     |
| [モック] 一時保存/中断   | 「一時保存 / 中断」→ホーム→再度相談開始                                            | `09-click-pause.png`, `07-home-after-pause.png`, `08-start-again.png` | 「一時保存されました」と言われたのに、再開する入口がどこにもなく、次は白紙の新規セッション              |

---

## Findings (ビジネスオーナー)

| Severity    | 画面/箇所                           | 違和感                                                                                                                                                                                                                                                                                                          | 根拠                                                                                                               |
| ----------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **blocker** | [実] ホーム「新しい相談を始める」   | クリックしても**完全に無反応** (再現 2/2)。裏では `concern` 空文字で BFF が 400 を返しているがユーザーには何も見えない。直接 URL も全て /home へ戻されるため、**ゴールデンパス (相談→対話→抽出→レビュー→トリアージ) に 1 歩も入れない**。BFF 単体は非空入力で正常、モックでは遷移する = フロント×BFF の結合退行 | 期待とのズレ (押しても何も起きない) / コンセプト体現の全否定 (話し始められない)                                    |
| **major**   | [モック] 一時保存 / 中断            | 「現在のセッションは一時保存されました。」と表示するが、**再開の入口がどこにもない**。ホームに戻って再度相談を始めると白紙の新規セッション。吐き出した内容が「保存した」と言われて実質消える                                                                                                                    | 期待とのズレ (保存したものがどこに行ったか分からない) / 信頼感。concept_deck「話した内容が流れず蓄積される」の真逆 |
| **major**   | [モック] 危機時サポート             | 見出しが「危機時サポート**導線**」(内部用語)。中身は「緊急連絡先・医療機関・信頼できる人へすぐ連絡してください」の 1 文のみで、**具体的な連絡先・電話番号・リンクがゼロ**。一番助けが要る瞬間に空箱。一般公開なら blocker 相当と判断する                                                                        | W2 文言 (弱っている人が使う) / concept_deck 冒頭注の位置づけ                                                       |
| **major**   | [実] ホーム「UI仕様プレビュー」     | エンドユーザー向けメニューに開発者向けボタンが同居し、押すと**真っ白画面 + 生のスタックトレース** (MDX import 解決失敗) の行き止まり。この環境では 100% 再現                                                                                                                                                    | 導線 (行き止まり) / 信頼感 (「壊れた?」)                                                                           |
| minor       | [実] 設定                           | 実 BFF 接続なのに「**ローカルモック版**のため、データはブラウザ再読み込みで初期化されます。」と表示。「プロフィール設定（ダミー）」等プレースホルダむき出し。データが消えると宣言されたら怖くて何も話せない                                                                                                     | 文言 / 信頼感                                                                                                      |
| minor       | [実] 困りごと一覧・履歴の空状態     | 「該当する困りごとはありません。」「履歴はまだありません。」だけで、初回ユーザーを相談開始へ誘導しない。行き止まり                                                                                                                                                                                              | 導線 (次に何をすればいいか分からない)                                                                              |
| minor       | [実] オンボーディング               | 見出しが「起動画面 / オンボーディング」(開発用語)。本文 1 文で、コンセプト (蓄積して育つ) の説明なし。しかも初回アクセスではスキップされ、ログアウトして初めて出会う                                                                                                                                            | 文言 / 導線                                                                                                        |
| minor       | [モック] 抽出レビューの却下         | 「これは違う（却下）」が即時削除で、確認も取り消し (undo) もない。共同編集というより一発削除                                                                                                                                                                                                                    | 期待とのズレ / FR-8 の思想 (最終編集者はユーザー) との温度差                                                       |
| minor       | [モック] 整理結果                   | 感情チップと箇条書き 3 行が無ラベルで並び、どれが論点でどれが提案か分からない。concept_deck §3 の「論点・感情・事実・問い」の構造には見えない                                                                                                                                                                   | コンセプト体現                                                                                                     |
| minor       | [モック] 困りごと詳細の「種」バッジ | 最古の言及につく「種」が意味不明 (初出の意?)。専門用語むき出し                                                                                                                                                                                                                                                  | 文言                                                                                                               |
| feel        | ホーム全体                          | (感覚) ボタン 4 つだけで、溜まったテーマ・最近の困りごと・繰り返しの気づきが一切見えない。「自己理解の地図として育つ」感覚がホームに存在しない。一覧・詳細の出来が良いだけに落差が大きい                                                                                                                        | concept_deck §13「話したら右側に頭の中が形になっていく」                                                           |
| feel        | [モック] セッション中リロード       | (感覚) 対話途中でリロードすると会話が消えて /home に放り出される。長文を吐き出す用途で誤リロード全消しは怖い (実 BFF では入口 blocker のため未検証)                                                                                                                                                             | 信頼感                                                                                                             |
| feel        | [モック] 履歴                       | (感覚) 下部の「選択中: 仕事のことで悩んでいます。」が何のためにあるのか分からない。過去結果を開くと URL が `/consultations/current/result` になり「現在の相談」扱いに見えるのも混乱する                                                                                                                         | 導線                                                                                                               |
| feel        | [モック] 対話ガイド                 | (感覚) 毎回「次に、〜を1つ教えてください。」と指示形で返り、受け止めより進行役感が強い。stub 文言のため低確度                                                                                                                                                                                                   | 文言 (W2)                                                                                                          |

---

## UNKNOWN (操作できなかった箇所 — 想像で補完していない)

- **実 BFF 接続での対話セッション〜抽出〜レビュー〜一覧・詳細・トリアージの全画面**: 入口 blocker のため到達不能。上記の下流画面の所見はすべてモックモードでの補助観察であり、実 BFF での挙動 (抽出結果の永続化、実データでの一覧表示など) は **UNKNOWN**
- **音声入力 (STT) / 読み上げ (TTS)**: headless ブラウザのためマイク・音声は未評価 (UNKNOWN)
- **実 AI の応答・抽出品質**: stub モードのため対象外
- **UI仕様プレビューの MDX ロード失敗**: この環境固有の可能性は否定できない (ソースの MDX ファイル自体は存在する)。他環境での再現性は UNKNOWN

---

## 総評

ビジネスオーナーとして、**今これをユーザーに出すことはできない**。理由は単純で、玄関のドアが開かない — リリース対象の構成 (フロント + 実 BFF) では「新しい相談を始める」を押しても何も起きず、このプロダクトの存在理由である「モヤモヤを話す」が始められない。しかも失敗が完全に無音で、弱っているときにこれに当たったら「自分が何か間違えた」と感じて閉じるだろう。BFF 単体は正常に動くことを確認したので、これはフロントが空の相談内容を送っているだけの結合退行に見え、修正自体は小さいはずだ。一方で、モックで下流を歩いた限り、抽出結果レビュー (新規/既存に追加/「🔁4回目」) と困りごと詳細の言及タイムラインは「話したことが流れず、束なって育つ」というコンセプトを初めて体感できる出来で、ここは素直に良い。だからこそ、入口の blocker に加えて「一時保存が保存されていない」「危機時サポートが空箱」という信頼の根幹に関わる major を先に潰したい。この 3 点が直れば、印象は「壊れたチャットアプリ」から「育つ地図の原型」に一気に変わると思う。

</details>
