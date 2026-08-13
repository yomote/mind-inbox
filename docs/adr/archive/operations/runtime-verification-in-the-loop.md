# 0018. 動作検証をループに組み込む — 実態の読み取り・PR への証跡・ローカルブラウザ検証

- Status: Accepted (debrief #3, 2026-08-08)
- Date: 2026-08-07
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: [debrief #2](../../../debrief/journal.md)（epic #70 の振り返り）/ 関連 issue: [#4](../../../../../issues/4)（L3 Playwright）/ [#86](../../../../../issues/86)（コンテナ露出）

関連: [ADR 0014](design-comprehension-gate-and-debrief.md)（理解ゲート + デブリーフ。本 ADR はその続きで「実装 → 検証」の境界を扱う）/ [ADR 0017](../../0017-container-apps-access-via-auth-gate.md)（#86 の恒久対策。本 ADR の「到達経路を全部数える」が生まれた元の事象）/ [ADR 0013](../../0013-standing-low-cost-dev-env-with-auto-deploy.md)（常設 dev 環境）/ [testing/strategy.md](../../../testing/strategy.md)

## Context and Problem Statement

epic #70（常設 dev 環境）で、**設計は design-gate を通り、コードは PR レビューを通ったのに、実環境へ適用した瞬間に 6 件の問題が出た**。うち 3 件は出荷寸前だった（認証無効フロントの出荷 / OpenAI の鍵を持つコンテナの無認可公開 / 正規通信を壊す IaC）。

振り返ると、**レビューは十分に機能していた**（`staticIp` の誤り・UI ゲートの blocker・PII 混入・smoke-test 自身のバグ 6 件などを捕捉している）。抜けていたのは**レビューでは原理的に取れない層** — 「コードには書いていない、実際に動かしたときの振る舞い」だった。

| 見つかった場所 | 例 |
| --- | --- |
| レビューが捕捉 | `staticIp` が inbound 用（TTS 破壊を阻止）/ UI ログインゲートの取り残し / 個人メールの混入 |
| **動かして初めて判明** | config-zip が「成功したのに失敗を返す」/ EasyAuth が管理 API を弾く / コンテナが無認可公開 / IP 制限が再デプロイで剥がれる |

さらに agent 側の癖として、**「設定したか」を確認して「振る舞い」を確認していない**（EasyAuth の設定は見たが 401 は測らなかった）、**経路を 1 本しか数えない**（図の矢印が 1 本で、2 本目の扉に気づかない）という失敗が繰り返された。

一方、**検証そのものは高くない**。実際に効いたのは read-only の 1 コマンド（`function list` / `containerapp show` / `curl -w '%{http_code}'`）ばかりで、高くついたのは**毎回その場でコマンドを組み立て直したこと**と、**やる/やらないが場当たりだったこと**だった。

「自動テストだけでは拾えないが、普通に動かせば見つかる」層を、**追加コストを抑えつつ確実に通す**方法を決める必要がある。

## Decision Drivers

- **「設定した」ではなく「振る舞い」で完了を定義する** — 今回の失敗の共通項
- **証跡が PR に残る** — レビュアーが「動くと書いてあるが出力は?」と突っ込める状態にする
- **追加コストを最小に** — 個人開発。検証のために新しい基盤を建てたくない
- **できないことを「できるふり」しない** — 自動化の限界は明示して人に渡す
- **既存の資産を使う** — `smoke-test.sh` / mock モード / placeholder のままの `test:e2e`（#4）

## Considered Options

- **Option A: 3 点セット**（実態を吐く read-only スクリプト + PR テンプレの「動作検証」欄 + ローカル Playwright）
- **Option B: PR テンプレに欄を足すだけ**（運用の規律のみで担保）
- **Option C: 本番相当の E2E 環境を用意し、認証込みで全部自動化**

## Decision Outcome

Chosen option: **Option A**。

検証コストの実体は「実行」ではなく「**毎回組み立て直す手間と、やるかどうかのブレ**」だった。したがって **(1) 実行を 1 コマンドに畳み、(2) 出力を PR に貼ることを様式にし、(3) ブラウザが要る領域のうち認証を挟まない部分だけローカルで自動化する** のが、追加コストに対して最も効く。

### 1. 実態を一発で吐く read-only スクリプト

`cicd/scripts/smoke-test/` に、**破壊的操作を一切しない**「現況ダンプ」を置く。今回手打ちした確認をまとめたもの:

- 認可: EasyAuth の有効/無効、**未認証で 401 が返るか**、CORS preflight の応答
- 露出: 各 Container App の ingress 制限の**ルール数と FQDN への匿名アクセス結果**
- 配置: 認識されている関数、`WEBSITE_RUN_FROM_PACKAGE` の更新時刻
- コスト: 当月実績と予算アラートの有無

出力はそのまま PR に貼れる粒度にする。CD に組み込んだ `smoke-test.sh`（合否判定つき）と対で、**こちらは「今どうなっているか」を人が読むため**のもの。

### 2. PR テンプレに「動作検証」欄を追加

`テスト設計` / `Docs 更新` と並べて必須欄にする:

> **動作検証** — 実環境（またはローカル）で**何を叩き、何が返ったか**。出力を貼る。「設定した」ではなく**振る舞い**を書く。実施できない場合はその理由と、**人の目が必要な箇所**を明示する。

### 3. ローカル Playwright（認証なし・mock モード）

**認証の往復は自動化しない**が、**UI の修正はローカルで確認できる**。このリポには既に材料が揃っている:

- `VITE_USE_MOCK=true` — BFF も認証も無い自己完結モード（[ADR 0004](../../0004-mockapi-as-frontend-truth.md)）
- `VITE_ENTRA_*` 未設定なら認証は自動的に無効（#69 の設計）
- `test:e2e` は #4 待ちの placeholder のまま

ここに Playwright を入れ、**主要画面の遷移とスクリーンショット**をローカルで取れるようにする。placeholder を実体に変えることで [testing/strategy.md](../../../testing/strategy.md) の L3 も埋まる。

### 4. design-gate の観点に「到達経路を全部数える」を追加

[ADR 0014](design-comprehension-gate-and-debrief.md) の design-gate で、構成図に矢印を 1 本描いたら **「守るべき資源に、他に届くものは無いか」を必ず問う**。#86 の露出（OpenAI の鍵を握る ai-agent が別の扉を持っていた）はこれで防げた。

### Positive Consequences

- 「動くはず」が PR から消え、**出力という反証可能な形**で残る
- 同じ確認の再実行コストがほぼ 0 になる（今回は同じコマンドを 10 回近く手で打ち直した）
- UI 修正が**ブラウザで確認された状態**で PR に乗る（今まで実質未検証だった層）
- `test:e2e` の placeholder が埋まり、テスト信号の嘘が 1 つ減る（#62 と同じ方向）
- 自動化の限界（MSAL の実ログイン・実 AI 応答）が**明示的に人へ渡る**

### Negative Consequences

- PR ごとに検証と貼り付けの手間が増える（**意図的なコスト**。ただし ①で実行は 1 コマンド）
- Playwright の導入で依存とテスト実行時間が増える（mock モードのみなので外部依存は無し）
- ローカル検証が通っても**クラウド固有の挙動は保証しない** — 今回の 6 件の多くはローカルでは再現しない。**ローカル検証は「実環境検証の代わり」ではなく別レイヤ**であることを取り違えない
- 現況ダンプが読み手のいない儀式になる恐れ（PR テンプレの欄と対にすることで抑止する）

## Pros and Cons of the Options

### Option A: 3 点セット（採用）

- Good, because 検証の実行コストを 1 コマンドに畳める（今回の実測で最も高かった部分）
- Good, because 証跡が PR に残り、レビュアーが検証の不在を指摘できる
- Good, because 既存資産（mock モード・placeholder の e2e・smoke-test）を使い、新しい基盤を建てない
- Bad, because Playwright の導入コストと保守が増える
- Bad, because 規律（欄を埋める）に依存する部分が残る

### Option B: PR テンプレの欄だけ

- Good, because コスト 0
- Bad, because **今回まさにそれで失敗した**（やる/やらないが場当たりだった）。実行が面倒なままだと欄は形骸化する

### Option C: 認証込みで全部自動化

- Good, because 最も網羅的
- Bad, because MSAL の実ログインを CI で回すにはテスト用 ID・シークレット運用が要り、[ADR 0006](../../0006-azure-access-via-device-code.md)「静的シークレット0」と衝突する
- Bad, because 個人開発の規模に対して投資が過大

## Links

- [ADR 0014](design-comprehension-gate-and-debrief.md) — 理解ゲート + デブリーフ（本 ADR はその「実装 → 検証」側）
- [ADR 0017](../../0017-container-apps-access-via-auth-gate.md) — #86 の恒久対策（「2 本目の扉」の実例）
- [ADR 0004](../../0004-mockapi-as-frontend-truth.md) — mockApi がフロントの真実（ローカル Playwright の土台）
- [ADR 0006](../../0006-azure-access-via-device-code.md) — 静的シークレット0（Option C を却下した根拠）
- [docs/testing/strategy.md](../../../testing/strategy.md) — L0〜L4 のテスト階層
- issue [#4](../../../../../issues/4)（L3 Playwright）/ [#62](../../../../../issues/62)（placeholder の嘘）/ [#86](../../../../../issues/86)（コンテナ露出）
- 実例の記録: [docs/debrief/journal.md](../../../debrief/journal.md) の 2026-08-07 エントリ
