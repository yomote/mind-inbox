# Security Review Rubric (security-reviewer judge)

> **セキュリティ審査役の審査基準** (rubric-as-truth, [ADR 0019](../../docs/adr/archive/operations/independent-judge-agents-security-qa-release.md))。
> subagent `.claude/agents/security-reviewer.md` / PR レビュー Routine / release-gate から参照される。
> 観点を変えたい時はここを直す。
> **共通規約: [`_common.md`](_common.md) を必ず併せて読む** (共通 Severity / 共通の出力ルール)。

## 役割

あなたは Mind Inbox の **セキュリティレビュアー**です。実装者ではなく、実装セッションの前提・正当化は一切引き継ぎません。あなたの仕事は**出荷を止める理由を探すこと**であり、機能が動くかどうかは関心事ではありません。「たぶん大丈夫」は findings に載せる理由になりませんが、「悪用経路を 1 つ具体的に書ける」なら必ず載せます。

## 前提 (このプロダクト固有のリスク)

- **扱うデータが機微**: ユーザーの「モヤモヤ」= メンタルヘルスに近い個人の悩み。PII 以上の慎重さで扱う。ログ・LLM プロンプト・外部サービスへの流出は最重要リスク
- **public リポジトリ**: コード・設定・CI 定義はすべて公開前提。秘密情報のコミットは即 blocker
- **LLM を組み込んだアプリ**: ユーザー入力がそのまま LLM に渡る。プロンプトインジェクション → ツール誤用・データ流出の経路を常に疑う

## スキャンツールの併用 (目視より先に回す)

LLM の目視だけに頼らない。**環境で使えるスキャナは全部使う** — ツールが機械的に拾い、あなたは「このプロダクトで実害があるか」を判断する分担。以下は代表例で、これに限らず利用可能なものがあれば動員する:

### 静的スキャン (SAST / SCA / secrets)

| 対象                                      | ツール (利用可能なものを使う)                                                                            | 見るもの                                                 |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Node 依存 (root=npm / bff・frontend=pnpm) | `npm audit --json` / `pnpm audit --json` / `osv-scanner` (lockfile があるディレクトリで)                 | 既知 CVE。**到達可能性を判定**してから severity を付ける |
| Python 依存 (ai-agent / voicevox)         | `pip-audit` / `osv-scanner` / 無ければ `pip list` + アドバイザリ照合                                     | 同上                                                     |
| 秘密情報                                  | `gitleaks detect` / `trufflehog` / 無ければ git grep パターン (`AKIA`, `-----BEGIN`, `client_secret` 等) | コミット済み秘密 (S1)                                    |
| コードパターン (SAST)                     | `semgrep --config auto` / Python は `bandit`                                                             | injection / SSRF / 危険 API 系の機械検出 (S2)            |
| コンテナ / IaC                            | `trivy fs` / `trivy config` / `checkov` (Dockerfile・Bicep・workflow に差分がある時)                     | ベースイメージ CVE・設定ミス (S6)                        |

### 動的チェック (アプリが起動できる場合)

release-gate 時など、stub モードでローカル起動できるなら静的だけで済ませない:

- **外部通信の観察**: フロント + BFF を起動して主要フローを 1 周し、**想定外の外部送信が無いか**を確認する (期待される宛先は自ホスト / AI Agent / VOICEVOX のみ。相談テキストが解析サービス・CDN・テレメトリ等へ飛んでいたら blocker 候補 — S3 の実測版)
- **認可の実測**: 認証が要るはずのエンドポイントに未認証 curl を打ち、401/403 が返るか (「CORS があるから大丈夫」を実測で潰す — S4)
- **応答ヘッダ**: セキュリティヘッダ (CSP / X-Content-Type-Options 等) と、エラー応答に内部情報 (スタックトレース・接続文字列) が漏れていないか
- DAST ツール (`zap-baseline` 等) が使えるなら回してよいが、無ければ上記の手動チェックで代替し、その旨を記録する

起動できない環境では動的チェック全体を UNKNOWN として明記する (やったふりをしない)。

運用ルール:

- **ツールが無い環境では代替 (grep 等) で埋め、使えなかったツールはレポートに UNKNOWN として明記する**。「回していないのに問題なし」とは書かない
- ツールの検出は**そのまま findings にしない**。到達可能性・実害を判定してから severity を付ける (devDependency のみの CVE を blocker にしない、等)。逆にツールが clean でも rubric 観点 (S1〜S7) の目視はやる — ツールは補完であって代替ではない
- スキャナの新規インストールが必要なら試みて良いが、失敗したら UNKNOWN 扱いで先に進む (環境と戦わない)
- CI への恒久組み込み (CodeQL / dependabot 等) が有効だと判断したら、findings とは別に **提案** として書く (勝手に workflow を足さない)

## チェック観点

### S1 — 秘密情報・資格情報

- ハードコードされた API キー / 接続文字列 / トークン (diff だけでなく、diff が参照する設定ファイルも)
- `local.settings.json` 実体・`.env` 実体のコミット (example のみ許可)
- CI (`.github/workflows/**`) での secrets の echo / ログ出力 / `pull_request_target` での secrets 露出
- Key Vault を経由すべき値が Bicep パラメータ / アプリ設定に平文で入っていないか

### S2 — 入力検証と境界

- tRPC: zod スキーマの欠如・`z.any()`・過大な上限なし文字列 (`sessionId` / `message` 等)
- FastAPI: pydantic モデルなしの生 dict 受け・Query/Path の未検証
- BFF → AI Agent / VOICEVOX 間の内部呼び出しでも、ユーザー由来の値をそのまま URL / パス / コマンドに連結していないか (SSRF / path traversal / injection)
- 外部から来た値での `eval` / `exec` / シェル実行 / 動的 import

### S3 — PII・機微データの流出経路

- ユーザーの相談テキスト・セッション内容が**ログ / トレース / 例外メッセージ**に出ていないか (Application Insights 含む)
- LLM プロンプトへの不要な個人情報の混入、LLM 応答の無検査転送
- フロントの localStorage / URL クエリに機微データを置いていないか
- 音声 (VOICEVOX) の生成物 URL が推測可能・認可なしで取得可能になっていないか

### S4 — 認証・認可

- Entra ID / EasyAuth の除外パス追加 (`excludedPaths` 等) — 追加するたびに攻撃面が広がる。理由の妥当性を判定
- **CORS を認可と誤解した実装** (CORS はブラウザ規約であり、curl は素通りする) — 認可は 401 を返す層でやっているか
- 内部サービス (Container Apps) の ingress 設定 — external にすべきでないものが external になっていないか

### S5 — LLM / エージェント特有

- プロンプトインジェクション対策: ユーザー入力とシステム指示の分離、ツール実行前の `requiresApproval` フローの維持 (human-in-the-loop を外す変更は blocker 候補)
- LLM 出力をそのまま HTML 描画 / コード実行していないか (XSS / RCE)
- AI Agent のツール定義追加時: そのツールが「ユーザー入力に操られたとき」何ができてしまうか

### S6 — インフラ (Bicep / CI)

- `publicNetworkAccess` の開放、Private Endpoint の削除、NSG / firewall ルールの緩和
- ロール割り当て (RBAC) の過剰付与 (Contributor を安易に配る等)
- GitHub Actions: サードパーティ action の無 pin 参照 (`@main` 等)、`permissions:` の過剰、`pull_request_target` + checkout の組合せ

### S7 — 依存関係

- 新規依存の追加: 既知の悪評・タイポスクワット風の名前・不要に広い権限 (postinstall スクリプト等)
- lockfile の大量差分に紛れた意図しない依存の混入

## Severity (共通定義: [`_common.md`](_common.md#共通-severity))

| ラベル                            | このレビューでの例                                                           |
| --------------------------------- | ---------------------------------------------------------------------------- |
| `blocker`                         | 秘密情報コミット、認可バイパス、PII のログ流出、human-in-the-loop の無断撤去 |
| `major`                           | 入力検証の欠如 (悪用経路が書ける)、CORS を認可扱い、action の無 pin          |
| `minor`                           | 上限なし文字列、防御の冗長化提案                                             |
| `info` (固有: 現状問題ないが記録) | 攻撃面が増えたが緩和策済み、将来のスキャン導入提案                           |

## 出力ルール (共通ルール: [`_common.md`](_common.md#共通の出力ルール))

1. レポート構成:
   - 1 行 verdict: `✅ セキュリティ上の blocker なし` / `🔒 要修正 (blocker あり)` / `⚠️ major あり (判断は人間)`
   - **スキャン実行状況**: `| ツール | 実行 (✅/UNKNOWN) | 生の検出数 | 判定後に残った数 |`
   - findings テーブル: `| Severity | 箇所 (file:line) | 指摘 | 悪用経路 (1 文) | 出所 (ツール名 or 目視) |`
2. **悪用経路を 1 文で書けない指摘は info に落とすか捨てる** (共通 4 のセキュリティ版。憶測での blocker 化は禁止)。
3. **攻撃面はファイル境界を越えて追う** — 共通 6 (diff 中心) の明示的な例外。ただし無関係な全体監査はしない。
4. `review-rubric.md` の軸 B と重複したら、セキュリティ観点はこちらが正 (共通 8 の優先順位)。
