# Runbook: クラウドセッション環境の設定

エージェントが動く実行環境 (claude.ai/code の cloud environment) の設定手順。
**この設定は web UI にしかなく、エージェントからは変更できない** — user が 1 回だけ操作する。

判断の背景: [ADR 0035](../adr/archive/operations/role-split-across-agents-and-actions.md) / 2026-08-10 の実測。

## なぜ設定するのか

2026-08-10 に、セッションの無駄を実測した結果:

| 無駄 | 原因 | この設定で消えるか |
| --- | --- | --- |
| 報告会のたびに VOICEVOX image (2GB) を pull し直す | 使い捨てコンテナ + セットアップスクリプト未設定 | ✅ 消える |
| `gh` / `codex` が入っておらず毎回入れる | 同上 | ✅ 消える |
| Codex を対話中に呼べない | ネットワークが `Trusted` で OpenAI 系を遮断 | ✅ 消える |
| Azure を読むのに main へのマージが要る | セッションに Azure 資格情報が無い (設計どおり / [ADR 0006](../adr/0006-azure-access-via-device-code.md)) | ❌ ここでは消えない → [#209](https://github.com/yomote/mind-inbox/issues/209) の read-only OIDC |

**セットアップスクリプトの結果はスナップショットされ、次のセッションはそこから始まる**
(pull 済みの docker image も含む)。約 7 日、またはスクリプト / 許可ドメインを変えると再構築される。

## 手順

1. [claude.ai/code](https://claude.ai/code) を開き、メッセージ入力欄の上にある**雲アイコン** (環境名が出ている) を選ぶ
2. 使っている環境にカーソルを合わせ、右に出る**歯車**を選ぶ
3. **Network access** を `Custom` にし、**Allowed domains** に以下を 1 行ずつ入れる

   ```text
   auth.openai.com
   chatgpt.com
   api.openai.com
   ```

   **「Also include default list of common package managers」に必ずチェックを入れる**
   (外すと npm / PyPI / apt が死に、セットアップスクリプトが機能しなくなる)

4. **Setup script** に [`cicd/scripts/dev/cloud-setup.sh`](../../cicd/scripts/dev/cloud-setup.sh) の中身を貼る
5. 保存する。次に**新しく**始めたセッションから効く (実行中のセッションには反映されない)

## 動作検証 (設定できたと言える条件)

新しいセッションで、エージェントに次を実行させる。

```bash
command -v gh codex                                   # どちらもパスが出ること
docker images | grep voicevox                         # pull 済みで並ぶこと (待ち時間なし)
curl -sS -o /dev/null -w '%{http_code}\n' https://auth.openai.com/  # 000 でないこと
```

3 つとも通れば設定できている。1 つでも欠けたらこの Runbook の手順に戻ること
(**「たぶん効いている」で先に進まない** — 沈黙と正常を区別しないのが今回の反省点)。

## Codex にログインする (PM セッションを入れ替えたとき 1 回)

コンテナは使い捨てなので、**新しい PM セッションを立てたら 1 回だけ**ログインが要る。
ブラウザは不要で、スマホで完結する。

```bash
codex login --device-auth      # 表示された URL とコードをスマホで入力
codex login status             # ログインできたか確認
```

- **API キー (`--with-api-key`) は使わない** — 従量課金になる。サブスクリプションの枠で回す
- **アクセストークンを環境変数に置かない** — 環境変数は「その環境を使う全員に見える」と
  公式に明記されており、長期クレデンシャルの保管場所として不適切 ([ADR 0009](../adr/0009-on-demand-cd-via-github-actions-oidc.md))

## やらないこと

- **常時起動の VM を借りる** — 2026-08-10 に検討したが不要と判断した。3 つの痛み
  (image / 依存 / ログイン) はどれも「動き続けるプロセス」ではなく「ディスクの状態」が
  欲しかっただけで、スナップショットで足りる
- **Azure の資格情報を環境変数に置く** — [ADR 0006](../adr/0006-azure-access-via-device-code.md)。
  外の事実は GitHub Actions 経由で取る ([ADR 0031](../adr/archive/operations/agent-reaches-outside-via-github-actions.md))
