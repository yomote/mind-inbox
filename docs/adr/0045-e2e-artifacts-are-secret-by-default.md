# 0045. 実環境 E2E の成果物は既定で秘密扱いにし、trace は公開鍵で暗号化して残す

- Status: Proposed
- Date: 2026-08-12
- Deciders: PO (yomote) / 窓口 PM セッション
- Related: [ADR 0018](0018-runtime-verification-in-the-loop.md) (動作検証をループに組み込む — 証拠が残らないと検証が成立しない) / [ADR 0031](0031-agent-reaches-outside-via-github-actions.md) (外の事実は Actions 経由) / [ADR 0017](0017-container-apps-access-via-auth-gate.md) (実環境は認証の門で閉じる — 門が本物だから実トークンが要る)

Technical Story: 2026-08-12、[#293](https://github.com/yomote/mind-inbox/issues/293) の調査が丸一日「実環境の SSE がハングする」という**誤った仮説のまま**動けなかった。原因は `deploy.yml` が `upload-artifact` を 1 つも持たず、Playwright の失敗証拠が runner ごと消えていたこと。PR #299 でスクリーンショットと `error-context.md` を残せるようにしたところ、**その初回の artifact で原因が判明した** (入力欄が空・送信ボタンが disabled で、SSE は呼ばれてすらいなかった)。同時に「では trace も上げたい」が自然な要求として出たが、trace には実アクセストークンが入る。

## Context and Problem Statement

実環境 E2E (`e2e-live`) の成果物は、**診断に不可欠であると同時に、秘密を含む**。この 2 つは同じ理由から来ている。

- **実トークンが要るのは、門が本物だから** — BFF は EasyAuth で閉じており ([ADR 0017](0017-container-apps-access-via-auth-gate.md))、ブラウザが送る Bearer トークンを Azure が実際に検証する。`e2e-live/entra-login.ts` は `authorize` / `token` エンドポイントを `page.route` で偽装するが、**`access_token` だけは本物でなければ全 API が 401 になる**。偽装できるのは `id_token` まで (msal はクライアントで署名検証しないため)
- **trace はネットワークを丸ごと記録する** — `playwright.live.config.ts` の `trace: "retain-on-failure"` により、失敗時の trace には応答本文と `Authorization` ヘッダが入る。E2E ステップの `::add-mask::` は **Actions のログにしか効かず、artifact の中身は伏せられない**
- **このリポジトリは public** — GitHub Actions の artifact は、public リポジトリでは**サインイン済みの GitHub ユーザーなら誰でも**ダウンロードできる。つまり業界で最もよく挙がる対策「artifact のアクセスを制限する」が**私たちには使えない**

実測 (2026-08-12、合成テストで秘密文字列を仕込んで生成物を照合):

| ファイル | 秘密の出現 |
| --- | --- |
| `error-context.md` (aria スナップショット) | **0 件** |
| `trace.zip` | **5 件** — 内訳は `resources/src@….txt` (**spec のソース**) 1 / `test.trace` 1 / `0-trace.trace` (アクション記録 + **DOM スナップショット**) 3 |

**「network 部分だけ外せばよい」は成立しない**ことがここで分かった。秘密は 3 種類のエントリに散らばり、DOM スナップショットは hidden input の value も `data-*` 属性も丸ごと含む (aria スナップショットとは別物)。外科的な除去は、結局「列挙漏れで漏れる」危険を持つ。

さらに Playwright 側に組み込みのリダクション機能は**無い** ([#19992](https://github.com/microsoft/playwright/issues/19992) は 2023 年から `P3-collecting-feedback` のまま / [#31728](https://github.com/microsoft/playwright/issues/31728) は機能が入らず closed / [#38673](https://github.com/microsoft/playwright/issues/38673) が 2026-01 に再提案)。2026 年に入った `maskColor` はスクリーンショットの見た目のマスクで、trace のリダクションではない。

そして**この問題は再発する**。今後 HAR・動画・レポートを残したくなるたびに同じ判断を迫られる。都度考え直すのは PO の裁定帯域の無駄でもある。

## Decision Drivers

- **証拠が残らないと動作検証が成立しない** ([ADR 0018](0018-runtime-verification-in-the-loop.md))。今回それが実害として出た (1 日の空転)
- **失敗が閉じ側に倒れること** — スクラブ方式は「秘密の見た目を全部列挙できている」前提に立ち、想定外の符号化ですり抜けると**静かに公開される**
- **エージェントがボトルネックなく調査できること** — 人間が毎回復号して貼り直す運用は、改善ループの速度を PO の可用性に縛る
- **CI に秘密を増やさない** — 長期クレデンシャルを置かない ([ADR 0031](0031-agent-reaches-outside-via-github-actions.md) の driver を継承)
- 判断を 1 回で固定し、成果物が増えるたびに再検討しない

## Considered Options

- **Option A: 何も上げない (現状復帰)** — 安全だが、1 日空転した状態に戻る
- **Option B: スクラブしてから平文で上げる** — 組み込みが無いため一般的な回避策だが、失敗が公開側に倒れる
- **Option C: artifact のアクセスを制限する** — 業界標準だが **public リポジトリでは選べない**
- **Option D: 公開鍵で暗号化して上げ、秘密鍵はエージェントの実行環境に置く** (採用)
- **Option E: 対称鍵 (GitHub Secrets のパスフレーズ)** — GitHub Secrets は**入れた値を画面から読み出せない**ため、控えを失うと過去の artifact が永久に開けない

## Decision Outcome

Chosen option: **"Option D"**。

### 決定の内訳

- **D1 実環境 E2E の成果物は既定で秘密扱いとする** — public リポジトリなので「上げない」が既定。上げるものは個別に理由と安全性を示す
- **D2 平文で上げてよいのは「秘密ゼロを実測で示したもの」だけ** — 現時点ではスクリーンショット (`*.png`) と `error-context.md` の 2 種。**推測ではなく測定で示すこと** (今回は秘密を 3 経路に仕込んで 0 件を確認した)。新しい種類を平文で足すときは同じ測定を通す
- **D3 trace は公開鍵で暗号化して上げる** — 暗号化は中身の形に依存しないので、**列挙漏れという失敗モードが存在しない**。これが Option B との決定的な差
- **D4 公開鍵はリポジトリに commit する** (`cicd/keys/e2e-artifacts.pub.asc`) — 公開鍵は公開してよい。**CI に秘密を 1 つも増やさない**。CI が乗っ取られても過去の artifact は復号できない (暗号化しかできない)
- **D5 秘密鍵は Claude Code 実行環境の環境変数に置く** (`E2E_ARTIFACT_PRIVATE_KEY`) — GitHub Secrets には置かない (workflow が復号できても、**public リポジトリでは Actions のログが公開**なので出力先が無い)。エージェントが手元で復号し、`playwright show-trace` に食わせる。**人間を復号のボトルネックにしない**
- **D6 公開鍵が無い間は trace を残さず、warning を出して続行する** — 鍵の準備前に平文で上がる事故を構造的に防ぐ (fail closed)。「鍵が無いから黙って何もしない」ではなく**必ず 1 行喋る**
- **D7 暗号化の結果を機械で検証する** — 出力ディレクトリに `.gpg` 以外のファイルが 1 つでもあれば **run を落とす**。「暗号化したつもり」で平文が混ざる事故を、成功パスの中で潰す
- **D8 `sources: false` で spec を trace に同梱しない** — 実測で trace は**テストのソースコードを含んでいた**。spec にハードコードされた秘密がそれだけで載るため、live 設定では落とす

### なぜ gpg か

GitHub の ubuntu ランナーに**最初から入っている** (`age` は導入ステップが要る)。鍵の管理は PO の手元だけで完結する。

## ⚠️ D5 は ADR 0031 と緊張関係にある (PO 裁定が要る)

**この ADR は Accepted の [ADR 0031](0031-agent-reaches-outside-via-github-actions.md) と正面から衝突しうる。** 隠さず明記する (2026-08-12 の Codex レビュー P1 指摘)。

0031 は Decision Drivers に「**秘密を増やさない — サンドボックスに長期クレデンシャルを置かない**」を掲げ、Option D (SP 秘密をサンドボックスの環境変数に置く) を「**長期クレデンシャルをサンドボックスに置くことになり、ADR 0009 の『保存する秘密を作らない』を正面から崩す**」として棄却している。**D5 はまさにその形**である。

### 相違点 (被害範囲が違う)

| | ADR 0031 が棄却した Option D | 本 ADR の D5 |
| --- | --- | --- |
| 鍵が守るもの | Azure サブスクリプション (Contributor) | dev の trace 1 種類 |
| 漏れたときにできること | **Azure リソースの作成・削除・課金** | 14 日以内の暗号化 artifact を復号 → 中の**期限 1 時間の dev BFF トークン**が読める |
| 有効期間 | 長期 (ローテーションまで) | 長期 (ローテーションまで) — **ここは同じ** |

被害範囲は桁違いに小さいが、**「長期クレデンシャルをサンドボックスに置く」という性質そのものは同じ**である。「小さいから良い」で押し切るのは 0031 の規律を実質的に空洞化させる。

### したがって

- **本 ADR は 0031 を supersede しない。** D5 は 0031 の原則に対する**限定的な例外の提案**であり、**PO の裁定を要する** (次回 debrief)
- 裁定までの間、D5 を前提とした実装は入るが、**鍵の設置自体が人間の作業**なので、PO が貼らない限り例外は発動しない (構造的に暴走しない)
- **Reject された場合の代替**: ①秘密鍵を PO の手元だけに置き、trace が要るときだけ PO が復号して共有する (エージェントがボトルネックを負う代わりに 0031 を守る) ②外部 KMS / 復号ブローカーを立てる (鍵を持ち出さない。ただし新規の外部サービス依存が増える)
- **エージェント環境を信頼する範囲を明文化していない**点も未解決。この環境変数を読めるセッションは復号できるため、実質「セッションで実行される任意のコードが鍵に到達しうる」。ここを統制する仕組み (鍵を読む主体の限定) は本 ADR では定義していない

## Consequences

### Positive

- 失敗の証拠が**構造的に**残る。診断できないまま仮説で 1 日溶かす事象が再発しない
- 秘密の漏出が**形に依存しない方法**で防がれる。スクラブと違い「列挙漏れ」という失敗モードが無い
- CI に長期クレデンシャルが増えない
- エージェントが自分で復号して調査できるため、改善ループが PO の可用性に律速されない
- 成果物が増えたとき (HAR・動画) の判断が「D1/D2 に照らす」で済む

### Negative / リスク

- **秘密鍵の置き場が人間の 1 回作業になる** — Claude Code 環境の環境変数への登録は web UI 操作で、エージェントからは実行できない ([ADR 0031](0031-agent-reaches-outside-via-github-actions.md) D6 と同じ制約)。**登録されるまで trace は残らない** (D6 により平文では上がらない)
- **秘密鍵を失うと過去の暗号化 artifact は開けない** — ただし artifact の保持は 14 日なので損失は限定的。鍵の再生成は公開鍵を差し替えるだけ
- **エージェントの実行環境を信頼する前提に立つ** — 環境変数を読めるセッションは復号できる。裏を返せば、その環境が侵害された場合の想定被害は「dev の BFF を 1 時間叩けるトークンを含む trace が読める」まで (トークンの audience は BFF アプリで、Azure Resource Manager ではない)
- 暗号化された artifact は**人間がそのままでは中身を見られない** — 復号手順を Runbook に置く必要がある

### 適用範囲

本 ADR は **`e2e-live` (実環境に実トークンで当たるテスト)** の成果物を対象とする。mock E2E (`apps/frontend/e2e/`) や単体テストの成果物は実トークンを持たないため対象外。

## Links

- Issue: [#293](https://github.com/yomote/mind-inbox/issues/293) (誤った仮説で 1 日空転した実例) / [#262](https://github.com/yomote/mind-inbox/issues/262)
- PR: [#299](https://github.com/yomote/mind-inbox/pull/299) (スクリーンショット + error-context の artifact 化 — 本 ADR の前段)
- Playwright: [#19992](https://github.com/microsoft/playwright/issues/19992) / [#31728](https://github.com/microsoft/playwright/issues/31728) / [#38673](https://github.com/microsoft/playwright/issues/38673) (組み込みリダクションが 3 年越しで未実装であることの根拠)
- GitHub Docs: [Downloading workflow artifacts](https://docs.github.com/en/actions/managing-workflow-runs/downloading-workflow-artifacts) (public リポジトリの artifact は署名済みユーザーなら誰でも取得できる)
