# Runbook: Container Apps の認証の門 (ADR 0017 / #86)

関連: [ADR 0017](../adr/0017-container-apps-access-via-auth-gate.md) / Issue #86 / PR #104 (BFF 側)

OpenAI の鍵 (Managed Identity) を持つ ai-agent と、その隣の vv-wrap を、**Container Apps 組み込み認証 (Entra)** で守る。BFF (Functions) は自身の Managed Identity でトークンを取得して通る。voicevox 本体は internal ingress (#101) のため門は不要。

## なぜ IP 許可リストではないのか (実測記録 2026-08-08)

応急処置だった IP 許可リストは**原理的に機能しない**ことが実測で確定した:

- Functions (Consumption) の実送信元 IP が、`possibleOutboundIpAddresses` の 20 個の**どれでもない** IP から出ており、リストをいくら同期しても 403 が再発した
- 許可リストを一時的に全開放すると全経路が疎通 (実 AI 応答まで確認) → 詰まりの犯人が IP 制限だと確定
- ADR 0017 が Option D (IP 許可リスト) を「IP 変動に脆い」と退けた判断の実地裏付け

> **切り分けに使ったプローブ**: この開発環境からは Functions/Container Apps に直接届かないため、
> ACI (curlimages/curl) を RG 内に使い捨てで立て、`az account get-access-token --scope api://<spa-client-id>/.default`
> で取った実トークンを付けて BFF を叩き、`az container logs` で結果を読む。ログは ARM 経由なので
> egress 制限下でも読める。終わったら `az container delete`。

## 一度きりのセットアップ

### 1. 門用アプリ登録 (audience) を作る

```bash
GATE_ID="$(az ad app create --display-name "mind-inbox-dev-ca-gate" \
  --sign-in-audience AzureADMyOrg --query appId -o tsv)"
az ad app update --id "$GATE_ID" --identifier-uris "api://$GATE_ID"

# ★ SP (実体) を忘れない — 無いとトークン要求が AADSTS500011 で死ぬ
#   (entra-spa-auth-and-budget.md の 2026-08-07 事象と同じ罠)
az ad sp create --id "$GATE_ID"

# ★ v2 トークンにする (組み込み認証の issuer は login.microsoftonline.com/<tenant>/v2.0)
OBJ="$(az ad app show --id "$GATE_ID" --query id -o tsv)"
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$OBJ" \
  --body '{"api": {"requestedAccessTokenVersion": 2}}'
echo "GATE_ID=$GATE_ID"
```

このアプリ登録は**トークンの宛先 (audience) を表すだけ**の存在で、シークレットもリダイレクト URI も持たない (静的シークレット 0、ADR 0006)。

### 2. client ID を IaC に固定する

`cicd/iac/main-bootstrap.parameters.json` の `containerAppsGateClientId` に値を commit する
(公開識別子なので commit してよい — #78 と同じ理屈で、**渡し忘れ再デプロイが門なし公開を出荷するのを防ぐ**)。

これで:

- bicep が Functions の `AI_AGENT_AUDIENCE` / `VOICEVOX_AUDIENCE` を宣言し、BFF (#104 の `serviceToken.ts`) が Managed Identity トークンを付けて下流を呼ぶ
- `deploy-ai-agent.sh` / `deploy-voicevox-wrapper.sh` が Container Apps の組み込み認証を冪等に適用する (未認証は 401)

## 検証 — ここが本体

「設定したか」ではなく**振る舞い**で確認する (ADR 0018):

```bash
# (a) 匿名の直叩き → 401 (門が閉じている)
curl -s -o /dev/null -w '%{http_code}\n' "https://<ai-agent-fqdn>/health"   # 期待: 401
curl -s -o /dev/null -w '%{http_code}\n' "https://<vv-wrap-fqdn>/health"    # 期待: 401

# (b) BFF 経由で実 AI 応答が返る (ログイン済みブラウザ、または上記 ACI プローブ)
#     → consultation.sendMessage が 200 で reply を返し、/api/tts が audio/wav を返す
```

`smoke-test.sh` は (a) を毎デプロイ実測する (401/403 以外 = 露出、で NG)。

## トラブルシュート

| 症状                                        | 原因 / 対処                                                                                                                                                                                         |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| BFF 経由の対話が 500 (`/chat failed — 401`) | BFF がトークンを付けていない。Functions の `AI_AGENT_AUDIENCE` が空 (bicep の `containerAppsGateClientId` 未設定) か、Functions の Managed Identity が無効                                          |
| BFF 経由の対話が 500 (`/chat failed — 403`) | (旧) IP 許可リストが残っている。`az containerapp show` の `ipSecurityRestrictions` を確認し全撤去する。認証の門と IP 制限は併用しない (IP 制限が門より手前で評価され、Functions の実 IP は予測不能) |
| 匿名直叩きが 200                            | 門が立っていない。deploy スクリプトの `Applying auth gate` ログを確認。`containerAppsGateClientId` 未設定なら WARN が出ているはず                                                                   |
| トークン取得が失敗 (AADSTS500011)           | gate アプリの SP 未作成。`az ad sp show --id <GATE_ID>` で確認                                                                                                                                      |

## ローカル開発

門は実環境の Container Apps にだけ立つ。ローカルは `IDENTITY_ENDPOINT` が無いので `serviceToken.ts` が null を返し、Authorization なしでローカルサービスを叩く (門も無いので通る)。
