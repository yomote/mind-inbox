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

## package の可視性 — public であることを確認する

Container Apps は registry secret 無しで pull する設計なので、package が **public** である必要がある（image にシークレットは焼き込んでいない。実行時の認可は Container App の Managed Identity と env で担保）。

**この repo は public のため、`build-images` が作った package も public になっている**（2026-08-06 実測）。確認はこれで足りる:

```bash
# 匿名でマニフェストが取れれば public（= Container Apps も認証なしで pull できる）
for IMG in ai-agent voicevox-wrapper; do
  TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:yomote/mind-inbox/$IMG:pull&service=ghcr.io" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  echo -n "$IMG: "
  curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.oci.image.index.v1+json" \
    "https://ghcr.io/v2/yomote/mind-inbox/$IMG/manifests/latest"
done
# 期待: 200  /  401 なら private → 下の手順で public に切り替える
```

private だった場合（repo を private 化した・新しい package が private で作られた等）:

1. GitHub → プロフィール/Org の **Packages** → 対象の package を開く
2. **Package settings → Danger Zone → Change visibility → Public**
3. (任意) **Manage Actions access** で当該 repo に `Write` があることを確認

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
