# 0023. 音声入力の精度・長時間対応のためサーバー STT に Azure Speech (F0) を採用し、Web Speech をフォールバックに残す

- Status: Accepted (briefing #1, 2026-08-08)
- Date: 2026-08-08
- Deciders: user (PO 決定: Issue #121 2026-08-08 コメント), 実装セッション (起案)
- Consulted: —
- Informed: —

Technical Story: <https://github.com/yomote/mind-inbox/issues/121>

## Context and Problem Statement

「モヤモヤを 1〜2 分話し続ける」はプロダクトの中核ユースケースだが、現状の音声入力はブラウザの
Web Speech API のみに依存しており、(1) 無音・時間で数十秒で認識が止まる、(2) 認識精度が
ブラウザ実装依存で環境差が大きい、という 2 つの問題がある (Issue #121, PO 実使用フィードバック)。

途切れは `onend` 自動再開 + interim の縫い合わせという無料の工学で解決できるが、**精度はブラウザ
API の中に打ち手が無い**。根本改善にはサーバー側 STT が必要で、これはクラウドサービス追加
(= 課金リソース追加・ADR 級判断) にあたるため、採用サービスと運用制約をここで決める。

## Decision Drivers

- **常時課金ゼロの維持** (ADR 0013): dev 環境は待機コスト最小が原則。予算事故を構造的に起こせない形にする
- **静的シークレット 0** (ADR 0006 系譜): サブスクリプションキーを SPA やリポジトリに置かない
- **精度と長時間対応の根本改善**: ブラウザ差を消し、1〜2 分以上の連続発話を安定させる
- **可用性**: STT が使えない環境 (ローカル・未プロビジョニング・障害時) でも音声入力自体は失わない

## Considered Options

- Option A: Web Speech API のみ (自動再開 + 縫い合わせの工学で粘る)
- Option B: Azure Speech Service (F0 無料枠) + Web Speech フォールバック
- Option C: Whisper API (OpenAI / Azure OpenAI) への録音アップロード
- Option D: セルフホスト STT (faster-whisper 等) を Container Apps に追加

## Decision Outcome

Chosen option: **"Option B: Azure Speech (F0) + Web Speech フォールバック"**。
F0 は月 5 時間まで無料で、**超過時は課金ではなく停止 (429)** のため予算事故が構造的に起きない。
リアルタイムのストリーミング認識で長時間発話・途中経過表示の両方に対応でき、既存の
Managed Identity / Entra トークン基盤 (ADR 0017 / #104) に乗るため静的シークレットも増えない。

運用制約 (PO 決定 2026-08-08 に従う):

- **F0 (無料枠 月 5h) から開始**。有料化 (S0) は別途 design-gate を通す
- 既存の**月次予算アラートの監視対象** (リソースグループ全体の budget が Speech も包含する)
- **認証は Managed Identity / Entra トークンベースで静的シークレット 0**:
  - Speech リソースは `disableLocalAuth: true` (サブスクリプションキー無効化) + custom subdomain
  - BFF (Functions) の System Assigned MI に `Cognitive Services Speech User` ロールを付与
  - BFF が MSI エンドポイントで Entra トークンを取得し、`aad#{resourceId}#{entraToken}` 形式の
    一時 authorization token を tRPC (`speech.issueToken`) でフロントへ渡す
  - フロントは Speech SDK (`microsoft-cognitiveservices-speech-sdk`) にこのトークンを渡して
    ブラウザ → Speech の WebSocket ストリーミング認識を行う。**キーは一切配らない**
- **Web Speech フォールバックは常に残す**: トークン発行不可 (ローカル / 未プロビジョニング /
  F0 停止 / 障害) の場合は自動でブラウザ認識に切り替わり、音声入力機能自体は失わない

### Positive Consequences

- 認識精度・長時間発話がブラウザ非依存で根本改善する (途切れ問題も同時に消える)
- 個人 dev の利用量 (月 5h 以内) なら実質 ¥0。超過しても停止のみで課金なし
- 静的シークレット 0 を維持 (SPA に渡るのは短寿命の Entra トークン由来 authorization token のみ)
- IaC (bicep) に載るので環境再現・削除が既存フローのまま

### Negative Consequences

- フロントに Speech SDK 依存が増える (バンドルは dynamic import で分離するが保守対象は増える)
- F0 は同時接続 1 などの制約があり、月 5h を超えると当月中は Web Speech 品質に落ちる
- BFF にトークン発行エンドポイントが増え、認可の考慮点 (EasyAuth の内側であること) が 1 つ増える
- Entra トークンの寿命 (約 1h) を超える連続利用ではトークン再発行が必要 (v1 は認識開始ごとに発行)

## Pros and Cons of the Options

### Option A: Web Speech API のみ

無料。`onend` 自動再開 + interim 縫い合わせで途切れはほぼ解決できる。

- Good, because 追加リソース・依存が一切ない
- Good, because 実装が小さい (本 ADR と同じ PR で実施済みの下地)
- Bad, because 精度はブラウザ実装依存のままで、環境差が消えない (Issue #121 の論点 2 が未解決)
- Bad, because 再開境界で稀に単語欠落が残る (エンジン再起動の隙間は埋められない)

### Option B: Azure Speech (F0) + Web Speech フォールバック

リアルタイムストリーミング STT。無料枠 5h/月、超過時は停止で課金なし。

- Good, because 精度・長時間対応・途中経過表示を同時に満たす唯一の無料構成
- Good, because MI / Entra トークン基盤 (ADR 0017) をそのまま流用でき、静的シークレット 0
- Good, because 超過時停止 = 予算事故が構造的に不可能で ADR 0013 と両立
- Bad, because フロントに SDK 依存が増える
- Bad, because F0 の制約 (同時接続・月 5h) がある

### Option C: Whisper API (録音アップロード)

録音完了後にファイルを投げてテキストを得るバッチ型。~¥1/分。

- Good, because 精度が高い
- Bad, because ストリーミングでないため途中経過が出せず、「話しながら文字が積み上がる」UX が作れない
- Bad, because 従量課金で常時課金ゼロ原則から外れる (無料枠が無い)
- Bad, because 録音データを一旦丸ごと送る形になりプライバシー面の検討が増える

### Option D: セルフホスト STT (faster-whisper 等)

Container Apps に STT コンテナを追加する。

- Good, because 外部課金なし・データが閉じる
- Bad, because CPU では実時間認識が厳しく、GPU は待機コストが跳ねる (ADR 0010 の教訓)
- Bad, because 運用・保守対象のサービスが 1 つ増える (v1 の規模に対して過剰)

## Links

- 関連 Issue: [#121](https://github.com/yomote/mind-inbox/issues/121)
- 関連 ADR: [0006](0006-azure-access-via-device-code.md) (静的シークレットを増やさない系譜) /
  [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) (常時課金ゼロ) /
  [0017](0017-container-apps-access-via-auth-gate.md) (MI / Entra トークン基盤)
- Microsoft Docs: [Speech Service — Entra ID 認証 (aad# authorization token)](https://learn.microsoft.com/azure/ai-services/speech-service/how-to-configure-azure-ad-auth)
