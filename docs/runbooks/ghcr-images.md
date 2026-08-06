# Runbook: コンテナ image を ghcr で回す（ACR 廃止）

関連: [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / epic #70 / issue #67

`ai-agent` / `voicevox-wrapper` の image を **GitHub Actions で事前ビルド → ghcr(GitHub Container Registry) に push** し、デプロイは **ghcr の既存タグを Container App に差し替えるだけ**にする。従来の `az acr build`（デプロイ毎のクラウドビルド ×2）と **ACR Basic の待機 ¥750/月** を廃止する。

## 全体像

```
main マージ (apps/services/** 変更)
  └─ build-images.yml
       ├─ docker build apps/services/ai-agent  → ghcr.io/yomote/mind-inbox/ai-agent:{sha,latest}
       └─ docker build apps/services/voicevox   → ghcr.io/yomote/mind-inbox/voicevox-wrapper:{sha,latest}

デプロイ (provision.sh / deploy-*.sh)
  └─ az containerapp update --image ghcr.io/.../<svc>:<tag>   # ビルドしない・差し替えのみ
```

- **タグ**: `sha-<full-sha>`（不変・ロールバック用）+ `latest`（main の最新）。
- **push 認証**: workflow の `GITHUB_TOKEN`（`packages: write`）。PAT 不要。
- **pull 認証**: image を **public** にして Container Apps から認証なしで pull（registry secret 不要 = ADR 0006 の「静的シークレット0」を維持）。

## 一度きりの初期設定 — package を public にする

`GITHUB_TOKEN` で最初に push された ghcr package は既定で **private**。Container Apps が registry secret 無しで pull できるよう **public に切り替える**（image にはシークレットを焼き込んでいないので公開して問題ない。実行時の認可は Container App の Managed Identity と env で担保）。

1. `build-images` workflow を一度成功させる（main マージ or Actions から手動 dispatch）。これで package が作られる。
2. GitHub → プロフィール/Org の **Packages** → `ai-agent` と `voicevox-wrapper` をそれぞれ開く。
3. **Package settings → Danger Zone → Change visibility → Public**。
4. (任意) **Package settings → Manage Actions access** で当該 repo に `Write` を確認（同一 repo からの push は既定で許可）。

> private のまま運用したい場合は、Container App に GitHub PAT（`read:packages`）を registry secret として設定し、`deploy-*.sh` の `az containerapp create/update` に `--registry-server ghcr.io --registry-username <user> --registry-password <pat>` を足す。ただし静的シークレットが増えるため既定は public を推奨。

## 手動でビルドを回す

```bash
# GitHub Actions の "build-images" を手動 dispatch（ブランチ指定可）
# UI: Actions → build-images → Run workflow
```

`apps/services/ai-agent/**` または `apps/services/voicevox/**` を触る PR を main にマージすると自動で走る。

## 特定コミットの image をデプロイ / ロールバック

`deploy-*.sh` は既定で `:latest` を差し替える。**特定 SHA に固定**したいとき（ロールバック含む）:

```bash
# 例: ある時点の ai-agent に戻す
IMAGE_TAG=sha-<full-commit-sha> RG=rg-dev-mind-inbox \
  ./cicd/scripts/deploy/deploy-ai-agent.sh

# レジストリ座標を丸ごと差し替えたいとき（fork 運用など）
IMAGE=ghcr.io/<owner>/<repo>/voicevox-wrapper:<tag> RG=rg-dev-mind-inbox \
  ./cicd/scripts/deploy/deploy-voicevox-wrapper.sh
```

`IMAGE_REGISTRY` / `IMAGE_REPO`（既定 `ghcr.io` / `yomote/mind-inbox`）でも上書きできる。

## トラブルシュート

| 症状 | 原因 / 対処 |
| --- | --- |
| Container App が `ImagePullBackOff` / pull 失敗 | package が private のまま。上記「public にする」を実施。private 運用なら registry secret を設定 |
| `deploy-*.sh` が古い image のまま | `:latest` はビルド完了後に更新される。`build-images` の成功を待つ。急ぐなら `IMAGE_TAG=sha-<sha>` で明示 |
| ロールバックしたい | `IMAGE_TAG=sha-<戻したい commit>` で `deploy-*.sh` を実行 |
| build が権限エラー | workflow の `permissions: packages: write` と、package の Actions access（repo に Write）を確認 |

## 完了条件（#67）

デプロイ経路から `az acr build` と ACR が消え、image は ghcr の事前ビルド済みタグ差し替えで反映される（待機 ¥750 も消える）。
