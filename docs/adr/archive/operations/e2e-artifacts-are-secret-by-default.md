# 0045. 実環境 E2E の成果物は既定で秘密扱いにし、trace は公開鍵で暗号化して残す

- Status: Accepted (2026-08-12, debrief にて PO 承認)
- Date: 2026-08-12
- Deciders: PO (yomote) / 窓口 PM セッション
- Related: [ADR 0018](runtime-verification-in-the-loop.md) (動作検証をループに組み込む — 証拠が残らないと検証が成立しない) / [ADR 0031](agent-reaches-outside-via-github-actions.md) (外の事実は Actions 経由) / [ADR 0017](../../0017-container-apps-access-via-auth-gate.md) (実環境は認証の門で閉じる — 門が本物だから実トークンが要る)

> **この文書は ADR ではありません。** [#385](https://github.com/yomote/mind-inbox/pull/385) で
> 「運用・プロセスの決め事は ADR ではない」が正典化され、当時の ADR 0045 はここへ退避しました
> (番号も退役 / [archive の README](../README.md))。**上の `Status:` 行は退避時点のまま凍結**してあり、
> 以後 Accept / Reject の対象にはなりません。**ここを直して運用を変えないでください。**
>
> **2026-08-12 の debrief で PO が下した裁定の記録**として、本文には次の 2 つが入っています
> (当時は ADR の Status 遷移として扱ったものです):
>
> 1. 本文全体を **Accept** (この時点で `Status:` が Proposed から Accepted になった / [PR #326](https://github.com/yomote/mind-inbox/pull/326))
> 2. **D5 を改訂** — 「エージェント復号は当面おこなわない」を撤回し、**エージェントは復号してよい。ただし秘密鍵は一度も Key Vault の外に出さない**（非エクスポートの鍵オブジェクト + `az keyvault key decrypt`）へ。暗号方式も gpg → 封筒暗号 (AES-256-GCM + RSA-OAEP-256) に変更 ([PR #332](https://github.com/yomote/mind-inbox/pull/332) = この記録)
>
> **現行の正典はここではありません**:
>
> | 何                                      | どこ                                                                                                                                                     |
> | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 層の分け方 / 鍵をどの RG に置くか       | [ADR 0056](../../0056-management-and-app-layers-with-backup-based-data-protection.md) D1 (管理系 RG `rg-mgmt-mindbox` / **Accepted** / 2026-08-15 発効)  |
> | 鍵の実体 (非エクスポート / 鍵長 / 権限) | [`cicd/iac/main-mgmt.bicep`](../../../../cicd/iac/main-mgmt.bicep) の `e2eTraceKey`                                                                      |
> | 適用手順                                | [`docs/runbooks/mgmt-layer-apply.md`](../../../runbooks/mgmt-layer-apply.md)                                                                             |
> | 鍵の運用 (復号 / ローテーション / 失効) | [`docs/runbooks/e2e-trace-keys.md`](../../../runbooks/e2e-trace-keys.md) (鍵ファイルの置き場は [`cicd/keys/README.md`](../../../../cicd/keys/README.md)) |
>
> 以下の本文で「持続層 RG」と読める箇所は、[#419](https://github.com/yomote/mind-inbox/pull/419) 以降
> **管理系 RG (`rg-mgmt-mindbox`)** に読み替えてあります。

Technical Story: 2026-08-12、[#293](https://github.com/yomote/mind-inbox/issues/293) の調査が丸一日「実環境の SSE がハングする」という**誤った仮説のまま**動けなかった。原因は `deploy.yml` が `upload-artifact` を 1 つも持たず、Playwright の失敗証拠が runner ごと消えていたこと。PR #299 でスクリーンショットと `error-context.md` を残せるようにしたところ、**その初回の artifact で原因が判明した** (入力欄が空・送信ボタンが disabled で、SSE は呼ばれてすらいなかった)。同時に「では trace も上げたい」が自然な要求として出たが、trace には実アクセストークンが入る。

## Context and Problem Statement

実環境 E2E (`e2e-live`) の成果物は、**診断に不可欠であると同時に、秘密を含む**。この 2 つは同じ理由から来ている。

- **実トークンが要るのは、門が本物だから** — BFF は EasyAuth で閉じており ([ADR 0017](../../0017-container-apps-access-via-auth-gate.md))、ブラウザが送る Bearer トークンを Azure が実際に検証する。`e2e-live/entra-login.ts` は `authorize` / `token` エンドポイントを `page.route` で偽装するが、**`access_token` だけは本物でなければ全 API が 401 になる**。偽装できるのは `id_token` まで (msal はクライアントで署名検証しないため)
- **trace はネットワークを丸ごと記録する** — `playwright.live.config.ts` の `trace: "retain-on-failure"` により、失敗時の trace には応答本文と `Authorization` ヘッダが入る。E2E ステップの `::add-mask::` は **Actions のログにしか効かず、artifact の中身は伏せられない**
- **このリポジトリは public** — GitHub Actions の artifact は、public リポジトリでは**サインイン済みの GitHub ユーザーなら誰でも**ダウンロードできる。つまり業界で最もよく挙がる対策「artifact のアクセスを制限する」が**私たちには使えない**

実測 (2026-08-12、合成テストで秘密文字列を仕込んで生成物を照合):

| ファイル                                   | 秘密の出現                                                                                                                                     |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `error-context.md` (aria スナップショット) | **0 件**                                                                                                                                       |
| `trace.zip`                                | **5 件** — 内訳は `resources/src@….txt` (**spec のソース**) 1 / `test.trace` 1 / `0-trace.trace` (アクション記録 + **DOM スナップショット**) 3 |

**「network 部分だけ外せばよい」は成立しない**ことがここで分かった。秘密は 3 種類のエントリに散らばり、DOM スナップショットは hidden input の value も `data-*` 属性も丸ごと含む (aria スナップショットとは別物)。外科的な除去は、結局「列挙漏れで漏れる」危険を持つ。

さらに Playwright 側に組み込みのリダクション機能は**無い** ([#19992](https://github.com/microsoft/playwright/issues/19992) は 2023 年から `P3-collecting-feedback` のまま / [#31728](https://github.com/microsoft/playwright/issues/31728) は機能が入らず closed / [#38673](https://github.com/microsoft/playwright/issues/38673) が 2026-01 に再提案)。2026 年に入った `maskColor` はスクリーンショットの見た目のマスクで、trace のリダクションではない。

そして**この問題は再発する**。今後 HAR・動画・レポートを残したくなるたびに同じ判断を迫られる。都度考え直すのは PO の裁定帯域の無駄でもある。

## Decision Drivers

- **証拠が残らないと動作検証が成立しない** ([ADR 0018](runtime-verification-in-the-loop.md))。今回それが実害として出た (1 日の空転)
- **失敗が閉じ側に倒れること** — スクラブ方式は「秘密の見た目を全部列挙できている」前提に立ち、想定外の符号化ですり抜けると**静かに公開される**
- **エージェントがボトルネックなく調査できること** — 人間が毎回復号して貼り直す運用は、改善ループの速度を PO の可用性に縛る。**この driver は D5 (非エクスポート方式) で満たす**。ただし [#302](https://github.com/yomote/mind-inbox/issues/302) の Key Vault ができるまでは暫定で PO 復号
- **CI に秘密を増やさない** — 長期クレデンシャルを置かない ([ADR 0031](agent-reaches-outside-via-github-actions.md) の driver を継承)
- 判断を 1 回で固定し、成果物が増えるたびに再検討しない

## Considered Options

- **Option A: 何も上げない (現状復帰)** — 安全だが、1 日空転した状態に戻る
- **Option B: スクラブしてから平文で上げる** — 組み込みが無いため一般的な回避策だが、失敗が公開側に倒れる
- **Option C: artifact のアクセスを制限する** — 業界標準だが **public リポジトリでは選べない**
- **Option D: 公開鍵で暗号化して上げ、秘密鍵は管理系 RG の Key Vault に非エクスポートで置き、復号は Key Vault の中で行う** (採用 / 2026-08-12 の debrief で「鍵を取り出して手元で復号」から改訂)
- **Option E: 対称鍵 (GitHub Secrets のパスフレーズ)** — GitHub Secrets は**入れた値を画面から読み出せない**ため、控えを失うと過去の artifact が永久に開けない

## Decision Outcome

Chosen option: **"Option D"**。

### 決定の内訳

- **D1 実環境 E2E の成果物は既定で秘密扱いとする** — public リポジトリなので「上げない」が既定。上げるものは個別に理由と安全性を示す
- **D2 平文で上げてよいのは「秘密ゼロを実測で示したもの」だけ** — 現時点ではスクリーンショット (`*.png`) と `error-context.md` の 2 種。**推測ではなく測定で示すこと** (今回は秘密を 3 経路に仕込んで 0 件を確認した)。新しい種類を平文で足すときは同じ測定を通す
- **D3 trace は公開鍵で暗号化して上げる** — 暗号化は中身の形に依存しないので、**列挙漏れという失敗モードが存在しない**。これが Option B との決定的な差。**方式は封筒暗号 (envelope encryption)**: ランダムな AES 鍵でデータを暗号化し、その AES 鍵を Key Vault の RSA 公開鍵で wrap して添える (D5 の非エクスポート復号を成立させるため / 2026-08-12 改訂)
- **D4 公開鍵はリポジトリに commit する** — 公開鍵は公開してよい。**CI に秘密を 1 つも増やさない**。CI が乗っ取られても過去の artifact は復号できない (暗号化しかできない)。**公開鍵の実体は Key Vault の鍵オブジェクトの公開部**で、commit するのはその写し (2026-08-12 改訂。旧 `cicd/keys/e2e-artifacts.pub.asc` は GPG 形式で、移行までの間だけ有効)
  - **公開鍵と「どの鍵バージョンか」を同じ 1 ファイルに入れて commit する** (2026-08-12 の Codex P2 指摘で追加)。D9 は `.enc` に wrap 時の鍵バージョンを記録することを要求しているが、**CI は Azure の資格情報を持たない** (それが D4 の要点) ので、バージョンをリポジトリから受け取る以外に知る方法が無い。PEM だけを commit すると、**D9 が要求する情報の供給経路が存在しない**
  - **別ファイルに分けない** — 別なら片方だけ更新して**食い違ったまま暗号化し続ける**ことができる。1 ファイルにするのは、その事故を**起こしにくくする**ため (D7 が成果物を 1 ファイルに束ねるのと同じ理由)
  - **ただし 1 ファイルにしても食い違いは防げない** (2026-08-12 の Codex 指摘で訂正。**初版は「1 ファイルなら構造的に起きない」と書いていたが、これは誤り**)。`publicKeyPem` と `keyVersion` は同じ JSON の中で**独立に編集できる**し、実際ローテーション手順は Azure に 2 回問い合わせる。食い違っても **JSON の読み込みも公開鍵による暗号化も成功し**、`.enc` に誤ったバージョンが記録される。気づくのは復号を試みた時
  - **したがって機械で担保する。2 つとも契約とする**:
    1. **JSON 全体を 1 つの Azure 応答から生成するスクリプトで作る** — 人が 2 つのコマンド結果を貼り合わせない。手順書ではなくコードで固定する
    2. **ローテーション時に「記載したバージョンで wrap → decrypt できる」ことを実測する** — 公開鍵で試しに wrap し、`keyVersion` を指定した `az keyvault key decrypt` で開け、元に戻ることを確かめる。**これが通らない JSON を commit しない**
  - **CI はこのファイルを読めなければ暗号化せず落とす** — バージョンが取れないまま暗号化すると、**開けない artifact ができる** (壊れていることが 14 日後まで分からない)。D6 の fail closed をここにも適用する
- **D5 秘密鍵は管理系 RG の Key Vault に「鍵オブジェクト」として非エクスポートで置き、復号は Key Vault の中で行う** (2026-08-12 の debrief で PO 裁定により改訂)。**エージェントは復号してよい。ただし鍵そのものは一度も Key Vault の外に出ない。**
  - **やり方**: artifact に添えた wrap 済み AES 鍵を `az keyvault key decrypt` で Key Vault に開かせ、返ってきた AES 鍵で手元のデータを復号する。認証は [ADR 0006](../../0006-azure-access-via-device-code.md) の device-code (短命トークン)
  - **なぜ「鍵を取り出して手元で復号」にしないか**: `gpg --import` 相当をやると秘密鍵がサンドボックスのディスクに落ち、以降そのセッションの任意コードが読める。**一度読み出せばセッションの外へ持ち出せる**ため、鍵を交換するまでの**全 artifact** が復号可能になる。「サンドボックスは使い捨てだから被害は限定的」は成り立たない
  - **非エクスポート方式で消える穴 / 消えない穴** (2026-08-12 の Codex 指摘で **4 度訂正**。**毎回「この方式で防げる範囲」を広く見積もりすぎていた**)
    - **消える**: **RSA 秘密鍵そのもの**が持ち出せない。鍵を交換するまでの全 artifact をまとめて開ける「万能鍵」は侵害者の手に残らない
    - **消えない (1) 露出は「セッション単位」ですらない — 更新資格情報を持ち出せる** (4 度目の訂正)。`az login` は**アクセストークンだけでなく更新資格情報 (refresh token) を MSAL キャッシュとしてディスクに置く**。侵害された環境の任意コードは**これを持ち出して別環境から `decrypt` を呼び続けられる**。したがって露出はセッションの寿命にも短命なアクセストークンの有効期間にも縛られず、**明示的に revoke するまで**続く
      - **実測 (2026-08-12)**: このセッション自身が、失効したアクセストークンを**保存済みの refresh token だけで更新**し、PO の関与なしに Azure API を叩き直した。「持ち出せる / 使い回せる」は仮説ではなく観測事実
      - **したがって失効手順を契約とする** — 復号に使った identity の更新資格情報を revoke する手順を Runbook に置き、**侵害が疑われたら実行する**。「トークンはいずれ切れる」に頼らない
      - **恒久的に絞るには「専用の最小権限プリンシパル」か「JIT / ブローカー」が要る** — どちらも本 ADR の範囲を超えるので、**[#302](https://github.com/yomote/mind-inbox/issues/302) の実装時に PO 裁定とする**。それまでは、復号に使う identity が **PO 個人の資格情報である**こと (= サブスクリプション全体に届く) を承知のうえで使う
    - **消えない (2) 一度返した AES 鍵は失効できない** — `az keyvault key decrypt` は**平文の AES 鍵を手元に返す**。侵害されたセッションは保持中の wrapped key を順に開き、**返ってきた AES 鍵 (または復号済みの平文) を保存できる**。トークンが切れても、**その時点で開いた artifact は読まれ続ける**。時間で閉じるのは「**まだ開いていない wrapped key と、将来の artifact**」に対する能力だけ
    - **本当に 1 件ごとに絞りたい場合**は、ブローカー (1 件だけ復号して結果を返す仲介) か JIT 権限が要る。**本 ADR では採らない** — 上の 2 つを受け入れる
    - **したがって「侵害の影響が時間で閉じる」とは言えない。** 言えるのは「**資格情報が revoke されるまでの間に取得できた artifact に限られる**」まで。**「侵害された時点で保持されていた artifact」でも「セッションが終わるまで」でもない** — 資格情報が生きている間に `e2e-live` が回れば**新しく生成された artifact も対象**で、その資格情報は**別環境へ持ち出せる**。境界は「侵害の瞬間」でも「セッションの終わり」でもなく「**revoke した瞬間**」
    - **時間が自動で閉じてくれる部分は無い。** 閉じるのは**人が revoke したとき**だけ。だから上の失効手順が「あれば良いもの」ではなく契約になる
  - **GitHub Secrets を選ばない理由**: workflow が復号できても、**public リポジトリでは Actions のログが公開**なので復号結果の出力先が無い
  - **環境変数を選ばない理由**: [ADR 0031](agent-reaches-outside-via-github-actions.md) の「サンドボックスに長期クレデンシャルを置かない」に反する。加えて Claude Code の公式ドキュメントが「**cloud environments have no dedicated secrets store, so don't add API keys or other credentials**」と明示している ([Configure cloud environments](https://code.claude.com/docs/en/cloud-environments))
  - **置き場所は管理系 RG (`rg-mgmt-mindbox`)** — 環境 (`rg-{env}-mind-inbox`) の中に置くと `cleanup-env.sh` の RG 削除に巻き込まれる ([#302](https://github.com/yomote/mind-inbox/issues/302))。**層の定義の正典は [ADR 0056](../../0056-management-and-app-layers-with-backup-based-data-protection.md) D1** (Accepted / 2026-08-14 の PO 裁定 → 2026-08-15 に Accept され発効。[ADR 0046](../../0046-environment-rebuildable-from-declaration.md) D1 の「持続層」を置き換え済み)
  - **管理系 RG に適用が済むまでの暫定**: それまでは秘密鍵を PO の手元に置き、**復号は PO のみ**。2026-08-12 の debrief で「Key Vault だけ先に作る」案は**採らない**と PO が判断したため、**エージェント復号が使えるようになるのは管理系 RG が立ってから**。宣言 (`main-mgmt.bicep` の `e2eTraceKey` / 非エクスポート) は [#419](https://github.com/yomote/mind-inbox/pull/419) で main に入っており、残るのは [Runbook](../../../runbooks/mgmt-layer-apply.md) の一度きりの手動適用
  - **移行**: 既に GPG 形式で残っている artifact (例: `e2e-live-trace-31571455835`) は**現行の GPG 鍵で PO が復号する**。封筒暗号への切り替えは新規分から。切り替え時は **D7 の許可拡張子 (`.gpg` → `.enc`) と、`encrypt-e2e-traces.sh` を呼ぶ「全 workflow」の upload glob・`PUBKEY` を同じ PR で替える** (取り残すと、その workflow の成果物だけが無言で上がらなくなる。詳細は D7)
- **D6 公開鍵が無い間は trace を残さず、warning を出して続行する** — 鍵の準備前に平文で上がる事故を構造的に防ぐ (fail closed)。「鍵が無いから黙って何もしない」ではなく**必ず 1 行喋る**
- **D7 暗号化の結果を機械で検証する** — 出力ディレクトリに**許可された拡張子以外のファイルが 1 つでもあれば run を落とす**。「暗号化したつもり」で平文が混ざる事故を、成功パスの中で潰す
  - **封筒暗号でも成果物は 1 ファイルに束ねる** (2026-08-12 の Codex P2 指摘で明確化) — wrapped AES 鍵・nonce・暗号文を**単一の `*.enc` に含める**。別ファイルに分けると (a) upload の glob から漏れて復号不能になる (b) 許可拡張子を増やして D7 の検査が緩む、のどちらかが起きる
  - したがって**許可拡張子は移行前が `.gpg`、移行後が `.enc` の 1 種類だけ**。
  - **替える対象は `deploy.yml` だけではない。** `cicd/scripts/deploy/encrypt-e2e-traces.sh` を呼ぶ**全 workflow**の (a) upload の `hashFiles(...)` 条件 (b) `upload-artifact` の `path:` glob (c) `PUBKEY` の 3 つを、スクリプトと**同じ PR で**替える。2026-08-14 時点の呼び元は **`deploy.yml` と `golden-path-monitor.yml` の 2 つ**で、`.gpg` が 4 行・`PUBKEY` が 2 行ある
  - **取り残すと「赤くならずに証拠が消える」** — upload の条件が `hashFiles('e2e-trace-enc/**/*.gpg') != ''` なので、スクリプトだけ `.enc` にすると条件が偽になり、**ステップはスキップされて run は緑のまま**になる。`golden-path-monitor.yml` は毎朝回るので、**気づかないまま日次の trace を失い続ける**
  - **文面では防げないので機械で押さえる** — `cicd/scripts/deploy/test_encrypt_e2e_traces.py` に、**スクリプトの許可拡張子と、スクリプトを呼ぶ全 workflow の glob 拡張子が一致すること**を突き合わせるテストを置いた。片方だけ替えた PR はここで落ちる (2026-08-14 追加。#300 で同型の「片方だけ替える」事故を踏んでいる)
- **D8 `sources: false` で spec を trace に同梱しない** — 実測で trace は**テストのソースコードを含んでいた**。spec にハードコードされた秘密がそれだけで載るため、live 設定では落とす
- **D9 データ暗号は認証付き (AEAD) に限る — 改ざん・破損を復号時に必ず検出する** (2026-08-12 の Codex P2 指摘で追加)。gpg は署名なしでも MDC で完全性を見ていたが、封筒暗号を自前で組む以上、**認証を明示的に契約として書かないと落ちる**
  - **アルゴリズムは `AES-256-GCM` に固定する**。CBC / CTR 等の非認証モードを選んではいけない — 暗号文を書き換えても復号が「成功」してしまい、**壊れた trace を本物として読む**ことになる
  - **AES 鍵の wrap は `RSA-OAEP` (Key Vault の `RSA-OAEP-256`)**。`az keyvault key decrypt --algorithm RSA-OAEP-256` と対になる
  - **直列化形式に認証タグを含める** — `.enc` は次の 4 要素をこの順で持つ (バージョン付きヘッダ + 各要素の長さを前置し、実装は既存のライブラリで読み書きする):

    | 要素             | 内容                                                                                                                                                              |
    | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
    | version          | 形式のバージョン (将来の鍵/方式変更を壊さず入れるため)                                                                                                            |
    | wrapped key      | RSA-OAEP-256 で wrap した AES-256 鍵 + **どの Key Vault 鍵バージョンで wrap したか** (鍵ローテーション後も過去分を開けるため / `docs/runbooks/e2e-trace-keys.md`) |
    | nonce            | GCM の 96-bit nonce (鍵ごとに再利用しない)                                                                                                                        |
    | ciphertext + tag | AES-256-GCM の暗号文と 128-bit 認証タグ                                                                                                                           |

  - **復号時の認証失敗は hard error** — タグ検証に失敗したら、平文を 1 バイトも出力せず非ゼロで終了する。「一部だけ読めた」で先に進まない
  - **`openssl enc` は使わない** — GCM を安全に扱えない (タグの取り回しがコマンドラインに無い)。CI 側は python の `cryptography` (`AESGCM`) を使う。**「ランナーに同梱」という初版の選定理由はここで失効している**
  - **往復テストには改ざんの異常系を必ず入れる** — 「暗号化 → 復号 → 一致」だけでは非認証モードでも緑になるので、**暗号文を 1 バイト書き換えたら復号が失敗する**ことを性質として確かめる ([テスト戦略](../../../testing/strategy.md) §3 のプロパティ)

### 暗号方式 — gpg から封筒暗号へ (2026-08-12 改訂)

初版は **gpg** を選んだ。理由は GitHub の ubuntu ランナーに**最初から入っている**こと (`age` は導入ステップが要る)。

しかし D5 を非エクスポート方式にしたため、**復号を Key Vault の中でやる必要があり、gpg のファイル形式のままでは成立しない** (gpg は秘密鍵が手元にある前提)。そこで**封筒暗号**に変える:

| 側                  | やること                                                                                                                                          | 使うもの                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| CI (暗号化)         | ランダム AES-256 鍵で **AES-256-GCM** 暗号化 → その AES 鍵を Key Vault の RSA **公開鍵**で **RSA-OAEP-256** wrap → D9 の形式で 1 ファイルに束ねる | python `cryptography` (`AESGCM`) + 公開鍵 |
| エージェント (復号) | wrapped AES 鍵を `az keyvault key decrypt --algorithm RSA-OAEP-256` で開かせる → 返った AES 鍵で GCM 復号 (**タグ検証必須**)                      | device-code + `az`                        |

**独自アルゴリズムは作らない** — これは KMS を使うときの定石 (envelope encryption) で、標準の AES-GCM と RSA-OAEP を組み合わせるだけ。とはいえ**自前で組み合わせる以上、実装ミスの余地はある**ので、実装時に往復テストを必ず置く — **「暗号化 → 復号 → 一致」だけでは足りず、「暗号文を書き換えたら復号が失敗する」まで確かめる** (D9)。前者だけなら認証の無い方式でも緑になる。

## ADR 0031 との関係 — 衝突は解消した

初版の D5 は「秘密鍵を Claude Code 実行環境の環境変数に置く」としており、**Accepted の [ADR 0031](agent-reaches-outside-via-github-actions.md) と正面から衝突していた** (2026-08-12 の Codex レビュー P1 指摘)。0031 は Decision Drivers に「サンドボックスに長期クレデンシャルを置かない」を掲げ、Option D (SP 秘密を環境変数に置く) を「ADR 0009 の『保存する秘密を作らない』を正面から崩す」として棄却している。

**さらに、Claude Code の公式ドキュメントも同じことを言っていた**:

> Anyone who uses the environment can read the values, and **cloud environments have no dedicated secrets store, so don't add API keys or other credentials.**

独立した 2 つの情報源が同じ結論だったため、**D5 を書き換えて衝突を解消した**。さらに 2026-08-12 の debrief で「鍵を取り出さず Key Vault の中で復号する」形に改訂した。**現在の D5 は**:

- **保存された秘密を増やさない** — 鍵は Key Vault の鍵オブジェクトにあり、認証は device-code の短命トークン ([ADR 0006](../../0006-azure-access-via-device-code.md))
- **サンドボックスに長期クレデンシャルを置かない** — 環境変数にも GitHub Secrets にも置かず、**秘密鍵そのものはエージェント環境に一度も現れない**
- **ただし復号はエージェントがしてよい** — `az keyvault key decrypt` を呼ぶだけで、鍵は Key Vault の外に出ない。**旧版の「鍵に触る操作をエージェント環境で行わない」は、この形に置き換わった**

つまり 0031 を supersede する必要はなく、**0006 (device-code) と 0009 (no stored secret) の延長線上に収まったまま、driver「エージェントがボトルネックなく調査できること」も満たせる**。

**残る露出の範囲は「資格情報が失効するまでに取得できた artifact」** — 認証済みセッションが侵害されれば、資格情報の有効期間中は wrapped key を何度でも開けるうえ (その間に生成される新しい artifact も含む)、**開いて返った AES 鍵は資格情報が切れても失効しない** (D5 の「消えない」参照)。鍵が持ち出せる旧案との差は「**再利用可能な万能鍵が残らず、鍵交換なしに以後ずっと読まれ続けることはない**」ことで、ゼロではない。

### 残る前提

- **管理系 RG (`rg-mgmt-mindbox`) にまだ適用していない** ([#302](https://github.com/yomote/mind-inbox/issues/302) / 宣言は #419 で着地済み)。それまでは暫定で PO の手元に置く
- 暫定期間中は復号が PO 経由になる。**エージェント復号が効くのは #302 の完了時**。2026-08-12 の debrief で「Key Vault だけ先に作る」案は採らないと判断されたため、**この暫定は #302 と同じ長さ続く**

## Consequences

### Positive

- 失敗の証拠が**構造的に**残る。診断できないまま仮説で 1 日溶かす事象が再発しない
- 秘密の漏出が**形に依存しない方法**で防がれる。スクラブと違い「列挙漏れ」という失敗モードが無い
- CI に長期クレデンシャルが増えない
- 平文で上げる証拠 (スクリーンショット / error-context) はエージェントが直接読めるため、**多くの調査は PO を介さずに進む** (2026-08-12 の [#293](https://github.com/yomote/mind-inbox/issues/293) は実際そうだった)
- 成果物が増えたとき (HAR・動画) の判断が「D1/D2 に照らす」で済む

### Negative / リスク

- **暫定期間中は復号が PO 経由になる** — 公開鍵は既に commit 済みなので、**暗号化された trace は今この瞬間から artifact として残る**。変わるのは「誰が復号できるか」だけで、管理系 RG ([#302](https://github.com/yomote/mind-inbox/issues/302)) ができるまでは秘密鍵が PO の手元にあるため、**エージェントは自力で復号できない**
- **秘密鍵を失うと過去の暗号化 artifact は開けない** — ただし artifact の保持は 14 日なので損失は限定的。鍵の再生成は公開鍵を差し替えるだけ
- **#302 が終わるまでは PO がボトルネックになる** — その間エージェントは復号できない。**[#293](https://github.com/yomote/mind-inbox/issues/293) が実例で、artifact の保持期限 (8/26) が実質の締切**になっている。なお鍵が漏れた場合に読めるのは「dev の BFF を 1 時間叩けるトークンを含む trace」まで (トークンの audience は BFF アプリで、Azure Resource Manager ではない)
- **封筒暗号は自前で組み合わせる** — 標準の AES-GCM + RSA-OAEP を使うので独自アルゴリズムではないが、実装ミスの余地はある。往復テストを機械で置くこと。**「暗号化 → 復号 → 一致」だけでは足りない** — 認証の無い方式でも緑になるので、**改ざんの異常系**まで含める (D9)
- **露出は「revoke するまで」で、時間が自動で閉じる部分は無い** — 非エクスポート方式でも、侵害された環境の任意コードは **`az` の更新資格情報 (MSAL キャッシュ) を持ち出して別環境から `decrypt` を呼び続けられる**。「1 artifact ごとに承認」でも「セッション単位」でもなく、**その間に `e2e-live` が回れば新しい artifact も対象**。さらに **`decrypt` が返す AES 鍵は平文なので、revoke 後もその時点で開いた分は読まれ続ける** (2026-08-12 の Codex 指摘で 4 度訂正)。旧案との差は「**再利用可能な万能鍵が残らない**」ことに限られ、**失効は人が revoke して初めて起きる**
- **復号には device-code の認証が要る** — 完全な無人化ではない。ただし **「セッションを跨ぐたびに PO の承認が要る」は誤り** — 更新資格情報が残っていれば承認なしに更新できる (2026-08-12 に本セッションで実測)。PO の承認が要るのは**資格情報を新しく取るとき**だけ
- 暗号化された artifact は**人間がそのままでは中身を見られない** — 復号手順を Runbook に置く必要がある

### エージェント復号をどう扱ったか (経緯)

**初版の D5 は「秘密鍵を Claude Code 実行環境の環境変数に置く」だった。** 2026-08-12 の Codex レビュー (P1) が ADR 0031 との衝突を指摘し、エージェントが D5 を書き換えたが、そのとき**鍵の置き場所を直す (Key Vault へ) だけでなく、エージェント復号そのものを「当面おこなわない」に狭めた**。

**この狭め方が PO の合意内容と食い違っていた。** 同日の debrief で PO から「Key Vault に入れた上でやると決めたはず」と指摘され、判明した。ADR は Proposed のまま承認キューに載っていたので手続き上は正しいが、**「あなたが合意した内容を狭めます」と目立つ形で書いていなかった**のは反省点。決定を狭める変更は、理由の節に畳まずに Status の近くで宣言する。

**保留の技術的な理由自体は正しかった** — 鍵を取り出して手元で復号すると、`gpg --import` の時点でサンドボックスのディスクに落ち、以降は持ち出せる。使い捨てでも被害は限定されない。

**取りこぼしていたのは「鍵を取り出さない方法がある」こと。** 初版の ADR 自身が恒久解として Key Vault の鍵オブジェクト + `az keyvault key decrypt` を挙げていたのに、選択肢として PO に提示されなかった。debrief でこれを提示し、**PO が非エクスポート方式を選択**して D5 に反映した。

**教訓**: 「安全のためにできないことにする」判断は、**「安全なままできる方法」を探し切ってから**出す。探し切っていないなら、その旨を書いて選択肢として上げる。

### 適用範囲

本 ADR は **`e2e-live` (実環境に実トークンで当たるテスト)** の成果物を対象とする。mock E2E (`apps/frontend/e2e/`) や単体テストの成果物は実トークンを持たないため対象外。

## Links

- Issue: [#293](https://github.com/yomote/mind-inbox/issues/293) (誤った仮説で 1 日空転した実例) / [#262](https://github.com/yomote/mind-inbox/issues/262) / [#301](https://github.com/yomote/mind-inbox/issues/301) (封筒暗号への組み替え作業リスト) / [#302](https://github.com/yomote/mind-inbox/issues/302) (管理系 RG)
- PR: [#299](https://github.com/yomote/mind-inbox/pull/299) (スクリーンショット + error-context の artifact 化 — 本 ADR の前段) / [#326](https://github.com/yomote/mind-inbox/pull/326) (Accept) / [#332](https://github.com/yomote/mind-inbox/pull/332) (D5 改訂 = この記録) / [#385](https://github.com/yomote/mind-inbox/pull/385) (ADR 棚から退避) / [#419](https://github.com/yomote/mind-inbox/pull/419) (管理系 RG と非エクスポート鍵の宣言)
- **現行の正典**: [ADR 0056](../../0056-management-and-app-layers-with-backup-based-data-protection.md) D1 (層) / [`cicd/iac/main-mgmt.bicep`](../../../../cicd/iac/main-mgmt.bicep) (鍵の宣言) / [`docs/runbooks/mgmt-layer-apply.md`](../../../runbooks/mgmt-layer-apply.md) (適用) / [`docs/runbooks/e2e-trace-keys.md`](../../../runbooks/e2e-trace-keys.md) (鍵の運用手順) / [`cicd/keys/README.md`](../../../../cicd/keys/README.md) (鍵ファイルの置き場)
- Playwright: [#19992](https://github.com/microsoft/playwright/issues/19992) / [#31728](https://github.com/microsoft/playwright/issues/31728) / [#38673](https://github.com/microsoft/playwright/issues/38673) (組み込みリダクションが 3 年越しで未実装であることの根拠)
- GitHub Docs: [Downloading workflow artifacts](https://docs.github.com/en/actions/managing-workflow-runs/downloading-workflow-artifacts) (public リポジトリの artifact は署名済みユーザーなら誰でも取得できる)
