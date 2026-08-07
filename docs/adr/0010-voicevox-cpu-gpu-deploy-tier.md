# 0010. VOICEVOX を `voicevoxTier`(cpu/gpu) 単一スイッチで切替（既定 cpu）

- Status: Accepted (debrief #1, 2026-08-06)
- Date: 2026-06-28
- Deciders: omoteforlab
- Consulted: —
- Informed: —

関連: [ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md)（オンデマンド CD） / [ADR 0002](0002-container-apps-not-aks.md)（Container Apps scale-to-zero） / 実装追跡: [#47](https://github.com/yomote/mind-inbox/issues/47)

## Context and Problem Statement

オンデマンド CD（ADR 0009）で「使う時だけ立てて潰す」を実現したが、`up` に **初回 ~20〜40分**（[ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md) の記述に揃える）かかり、体験として「触りたくなってから待たされる」= オンデマンドの意味が薄い。

主因は 2 つ:

1. **VOICEVOX が GPU(T4) 固定** — 巨大イメージ（`voicevox/voicevox_engine:nvidia-latest`）+ GPU ワークロードプロファイル環境。構築が遅く、GPU 稼働は高い（ただし `minReplicas:0` で scale-to-zero）。
2. **未使用 SQL(S0) の常時作成** — BFF は現状 `InMemoryHistoryRepository` で SQL 不使用。

個人開発・デモ用途では **CPU で十分**と見込む（TTS レイテンシは GPU より高いが、体感を損なわない範囲と想定。**実測値は未取得で、#47 の検証で確認する**）。速く・安く立てられるモードを既定にしたい。一方で GPU（喋りが速い）も性能検証時に選びたい。

## Decision Drivers

- `up` を速くする（数分台）/ 撤収時 ¥0・GPU 課金なしを既定に
- 呼び出し側が**分かりやすい単一スイッチ**で切替（低レベル params を並べない）
- 既存の GPU 経路を壊さず、選択制で温存
- ACA の制約（Consumption プロファイルは環境に1つだけ）を正しく扱う

## Considered Options

- Option A: モジュールに **単一 `voicevoxTier: 'cpu' | 'gpu'`**（既定 `cpu`）を追加。image / workloadProfile / cpu / memory の差分は**モジュール内に閉じる**。
- Option B: 低レベル params（`voicevoxImage` / `voicevoxWorkloadProfileType` / `voicevoxCpu` / `voicevoxMemory`）を呼び出し側からバラで渡す。
- Option C: 現状維持（GPU 固定）。

## Decision Outcome

Chosen option: **Option A（単一 `voicevoxTier` スイッチ、既定 `cpu`）**。

呼び出し側（`provision.sh` / `deploy.yml` の入力）は `voicevoxTier=cpu` の一言で済み、意図が明確。CPU/GPU の具体（イメージ・プロファイル・リソース量）は**モジュールに閉じる**ので、利用者は中身を知らなくてよい。GPU 分岐が1箇所に集約され、ACA の「Consumption は環境に1つだけ」制約（cpu 時は GPU プロファイルを足さない）もモジュール内で正しく扱える。

Option B は柔軟だが呼び出し側が 4 つの低レベル値を正しく組み合わせる必要があり、「何を quick と呼ぶか分からない」問題を生む。Option C は遅い/高いままで要件を満たさない。

> **本 ADR は設計の記録（Proposed）**。実装（bicep のプロファイル条件分岐は ACA 依存でローカル単体テスト不能）は [#47](https://github.com/yomote/mind-inbox/issues/47) で、`az deployment group what-if` による検証を通してから行う。未使用 SQL の無効化（`enableSql`）も同じ「速く・安く」思想として #47 に含める。

### Positive Consequences

- `up` が **初回 ~20〜40分 → 数分〜10分**（GPU 環境 + 巨大イメージ + SQL を回避）
- 撤収時 ¥0 / GPU 課金なしが既定
- `gpu` は必要時だけ選択（性能検証・本番寄せ）
- 呼び出し側がシンプル（`voicevoxTier=cpu`）で、名前の曖昧さ（"quick" とは何か）が無い

### Negative Consequences

- bicep のワークロードプロファイルに条件分岐が必要（ACA の Consumption 単一制約のため純パラメータ化では不可）
- bicep はローカル単体テスト不能 → `what-if` 検証が実装の前提（[ADR 0006](0006-azure-access-via-device-code.md) の device-code で実施）
- cpu の TTS レイテンシは GPU より高い（個人利用では許容）

## Pros and Cons of the Options

### Option A: 単一 `voicevoxTier` スイッチ（採用）

- Good, because 呼び出し側が一言で切替でき意図が明確
- Good, because GPU/ACA の詳細をモジュールに閉じ込められる
- Bad, because モジュールに条件分岐（bicep コード変更）が要る

### Option B: 低レベル params をバラで渡す

- Good, because モジュール変更が最小（pass-through のみ）
- Bad, because 呼び出し側が 4 値を正しく組む必要／意図が伝わりにくい

### Option C: 現状維持（GPU 固定）

- Good, because 変更ゼロ
- Bad, because `up` が遅く高いまま。オンデマンドの利点を活かせない

## Links

- 実装追跡: [#47](https://github.com/yomote/mind-inbox/issues/47)
- 関連 ADR: [0009](0009-on-demand-cd-via-github-actions-oidc.md) / [0002](0002-container-apps-not-aks.md)
- スクリプト: `cicd/scripts/deploy/provision.sh` / IaC: `cicd/iac/`, `cicd/modules/bootstrap-core.bicep`
