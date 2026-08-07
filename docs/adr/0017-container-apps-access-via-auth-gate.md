# 0017. Container Apps の「第二の扉」は認証の門で閉じる (組み込み認証 + Managed Identity / voicevox は internal ingress)

- Status: Accepted (design-gate #2, 2026-08-07)
- Date: 2026-08-07
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: [Issue #86](https://github.com/yomote/mind-inbox/issues/86) — 常設 dev 構築時 (#70) に、Container Apps 3 つが `external: true`・認証なしで公開されていると判明。OpenAI の鍵 (Managed Identity) を持つ ai-agent に「第二の扉」が開いていた。応急処置 (手動 IP 許可リスト) は IaC 未反映で再デプロイで消えるため、恒久対策の方式を決める。

## Context and Problem Statement

Issue #69 は「課金の芯である OpenAI を守るため、認可の門を Functions (EasyAuth) に置く」設計だったが、OpenAI の鍵を実際に握るのは Functions ではなく ai-agent であり、その Container App が直接インターネットから叩けた。守るべき資源から見て**到達経路を全部数え、全経路に歯止めを置く**方式を決める必要がある。

## Decision Drivers

- **待機ほぼ¥0 の維持** — ADR 0013 の中核。常時課金が発生する構成は採れない (予算アラート月 ¥3,000)
- **静的シークレット 0 の系譜** — ADR 0006。API キーを増やさず identity ベースで守る
- **運用の堅牢性** — Functions の送信元 IP 変動や再デプロイで守りが剥がれない
- **検証可能性** — 「無認可で叩けないこと」を smoke-test で実測できる
- **工事の小ささ** — CAE は #76 で統合再作成したばかり。作り直しの繰り返しを避けたい

## Considered Options

- Option A: **認証の門** — ai-agent / vv-wrap に Container Apps 組み込み認証 (Entra) を有効化し、BFF が Managed Identity トークンを付けて呼ぶ。voicevox は同一 CAE 内からしか呼ばれないため internal ingress 化
- Option B: **内部 ingress + VNet** — 全 CA を `external: false` にし、Functions を Flex Consumption へ移行して VNet 統合で到達させる
- Option C: **段階 (A → 将来 B)** — A で閉じ、VNet 化を終形として別 Issue に積む
- Option D: **IP 許可リストの IaC 化** — 応急処置の恒久化

## Decision Outcome

Chosen option: **"Option A" (認証の門)**。design-gate #2 (2026-08-07) で user が決定。

- **ai-agent / vv-wrap**: Container Apps の組み込み認証 (Entra) を有効化し、無認可リクエストは 401。BFF (Functions) は自身の Managed Identity でトークンを取得して呼ぶ。「守るべき資源のすぐ手前に門を置く」という #69 の思想を、鍵を実際に持つサービスまで延長する
- **voicevox (engine)**: 呼び出し元 (vv-wrap) が同一 CAE 内のため、**VNet なしで internal ingress 化** (どの案でも無条件に正しい変更)
- **検証**: `smoke-test.sh` に「無認可の外部リクエストが 401/403 になること」の実測を追加 (無いと同じ穴の再発が緑で通る)

user は当初 Option B (ネットワークで閉じる方が設計原則として正しい) に傾いたが、現構成の Functions が **Y1 (Consumption) で VNet 統合非対応**であり、成立させるには EP1 (常時課金で Driver 1 に即死級に反する) か Flex Consumption 移行 + CAE の VNet 対応再作成 (工事最大・Driver 5 に反する) が必要という通行料を確認し、A を選択した。

Option C (将来 VNet を終形として予約) は採らない — 現時点で B へ移る決定的な理由がなく、「いつかやる」の予約はドリフトする。B が必要になったら (マルチユーザー化・コンプライアンス要件等) その時に新 ADR で判断する。Option D は IP 変動に脆く、GitHub Actions runner からの smoke 実測とも相性が悪い。

### Positive Consequences

- 無認可の到達経路が全 CA で閉じ、identity ベースなので IP 変動・再デプロイで剥がれない
- 待機コスト・プラン構成は不変 (ADR 0013 維持)。CAE の作り直しも不要
- シークレットを増やさない (Managed Identity トークン、ADR 0006 整合)
- smoke-test で「閉じていること」が毎デプロイ実測される

### Negative Consequences

- BFF 側にトークン取得 (+キャッシュ) の実装が増える (`aiAgentClient` / `voicevoxClient`)
- ローカル開発 (認証なし CA を立てない環境) 用のバイパス設定が要る
- ネットワーク露出自体は残る (401 を返すのは CA)。DoS 面では VNet 案に劣るが、dev 個人環境では許容
- CA 組み込み認証の設定 (アプリ登録 / allowed identities) が IaC + Runbook に増える

## Pros and Cons of the Options

### Option A: 認証の門 (採用)

- Good, because 待機¥0・工事最小・identity ベースで堅牢・#69 の思想と一貫
- Good, because voicevox internal 化と組み合わせて全経路が閉じる
- Bad, because トークン配線とローカルバイパスの実装が増える

### Option B: 内部 ingress + VNet (Flex Consumption 移行)

- Good, because ネットワークで閉じれば認証バグという故障モードごと消える (原則として最も正しい)
- Bad, because Y1 → Flex Consumption 移行 + CAE 再作成 + private DNS + smoke 経路再設計と工事が最大
- Bad, because EP1 で代替すると常時課金が予算 (月¥3,000) を大きく超える

### Option C: 段階 (A → 将来 B)

- Good, because 今すぐ閉じつつ終形も示せる
- Bad, because 「将来やる」の予約は根拠が無いままドリフトする。必要になった時に新 ADR で決める方が誠実

### Option D: IP 許可リストの IaC 化

- Good, because 工事が最小 (応急処置の写し)
- Bad, because Functions の送信元 IP 変動で壊れ、runner からの実測とも相性が悪い

## Links

- 発端: [Issue #86](https://github.com/yomote/mind-inbox/issues/86) (応急処置の内訳・露出面の点検記録)
- 関連 ADR: [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) (待機ほぼ¥0) / [0006](0006-azure-access-via-device-code.md) (静的シークレット 0) / [0002](0002-container-apps-not-aks.md) (scale-to-zero)
- 教訓: 「守るべき資源から見て、到達経路を全部数える」を design-gate の観点に追加 (#86 提案)
- 記録: [`docs/debrief/journal.md`](../debrief/journal.md) design-gate #2
