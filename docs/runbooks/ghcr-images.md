# Runbook: コンテナ image を ghcr で回す（ACR 廃止）

関連: [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / epic #70 / issue #67

`ai-agent` / `voicevox-wrapper` の image を **GitHub Actions で事前ビルド → ghcr(GitHub Container Registry) に push** し、デプロイは **ghcr の既存タグを Container App に差し替えるだけ**にする。従来の `az acr build`（デプロイ毎のクラウドビルド ×2）と **ACR Basic の待機 ¥750/月** を廃止する。

## 全体像

```
main マージ (apps/services/** 変更)
  └─ build-images.yml
       ├─ docker build apps/services/ai-agent  → ghcr.io/yomote/mind-inbox/ai-agent:{sha,latest}
       └─ docker build apps/services/voicevox   → ghcr.io/yomote/mind-inbox/voicevox-wrapper:{sha,latest}

デプロイ (deploy.yml → provision.sh → deploy-*.sh)
  ├─ 直近の build-images **成功** run の head SHA を解決 → IMAGE_TAG=sha-<full-sha>
  ├─ az containerapp update --image ghcr.io/.../<svc>:sha-<full-sha>   # ビルドしない・差し替えのみ
  └─ smoke-test.sh が稼働 revision の image タグを EXPECTED_IMAGE_TAG と突合 (据え置き検知)
```

- **タグ**: `sha-<full-sha>`（不変・ロールバック用）+ `latest`（main の最新）。
- **デプロイに使うのは常に `sha-<full-sha>`**（`:latest` は使わない。理由は次節）。
- **push 認証**: workflow の `GITHUB_TOKEN`（`packages: write`）。PAT 不要。
- **pull 認証**: image を **public** にして Container Apps から認証なしで pull（registry secret 不要 = ADR 0006 の「静的シークレット0」を維持）。

## デプロイは不変 sha タグで行う（`:latest` は使わない）

`:latest` は「同一文字列での update = 新 revision を作らない no-op」になり、緑のまま古い image が動き続ける。判断は [ADR 0025](../adr/0025-deploy-container-images-by-immutable-sha-tag.md)。CD の実装は 2 段構え:

1. **不変タグを解決してから差し替える** — `deploy.yml` の「Resolve IMAGE_TAG」step が直近の build-images **成功** run の head SHA から `sha-<full-sha>` を解決して渡す
   - 同一 commit の build-images が走行中なら**完了を待つ**（待たないと一つ前の image を載せる）
   - build-images は `apps/services/**` 変更時のみ走るので、**デプロイ対象 commit と image の commit は一致しないことがある**（「最後にビルドされた sha」が正）
   - 成功 run が 無い場合は `:latest` にフォールバックせず **fail** する。build-images を手動 dispatch してから再デプロイする
2. **載ったことを実測する** — `smoke-test.sh` が稼働 revision (`latestReadyRevisionName`) の image タグを `EXPECTED_IMAGE_TAG` と突合し、不一致なら NG

手動実行時は `deploy-*.sh` が「同一文字列での update」を検出して WARN を出す。

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

CD は `IMAGE_TAG` を自動解決するが、**手動実行では必ず `IMAGE_TAG=sha-<full-sha>` を渡す**（スクリプトの既定は後方互換のため `:latest` のままで、そのまま実行すると前節の no-op を踏む）:

```bash
# 例: ある時点の ai-agent に戻す
IMAGE_TAG=sha-<full-commit-sha> RG=rg-dev-mind-inbox \
  ./cicd/scripts/deploy/deploy-ai-agent.sh

# レジストリ座標を丸ごと差し替えたいとき（fork 運用など）
IMAGE=ghcr.io/<owner>/<repo>/voicevox-wrapper:<tag> RG=rg-dev-mind-inbox \
  ./cicd/scripts/deploy/deploy-voicevox-wrapper.sh
```

`IMAGE_REGISTRY` / `IMAGE_REPO`（既定 `ghcr.io` / `yomote/mind-inbox`）でも上書きできる。

差し替えたい SHA が分からないときは、build-images の成功 run から引く:

```bash
gh run list -R yomote/mind-inbox --workflow build-images.yml \
  --branch main --status success --limit 5 --json headSha,createdAt
```

## 実際に載っている image を確認する

「デプロイしたつもり」と「実際に動いているもの」を突き合わせる（#107 の再発検知）:

```bash
# 判定つき: 期待タグと一致しなければ NG で終了コード 1
RG=rg-dev-mind-inbox DEPLOYMENT=main-bootstrap \
  EXPECTED_IMAGE_TAG=sha-<full-sha> ./cicd/scripts/smoke-test/smoke-test.sh

# 判定なしのダンプ (稼働 revision / image / 作成時刻を表で出す。PR 貼り付け用)
RG=rg-dev-mind-inbox DEPLOYMENT=main-bootstrap ./cicd/scripts/smoke-test/inspect-env.sh
```

`EXPECTED_IMAGE_TAG` を省くと一致検証は skip され、稼働タグの表示のみになる（`:latest` で動いていれば WARN）。

## トラブルシュート

| 症状                                                                | 原因 / 対処                                                                                                                                                                  |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Container App が `ImagePullBackOff` / pull 失敗                     | package が private のまま。上記「public にする」を実施。private 運用なら registry secret を設定                                                                              |
| デプロイは緑なのに古い image のまま                                 | `:latest` 差し替えの no-op（#107）。`IMAGE_TAG=sha-<sha>` を明示して再実行し、`inspect-env.sh` で稼働 revision を確認する。CD 経由なら「Resolve IMAGE_TAG」step のログを見る |
| smoke-test が「稼働 image tag が期待と不一致」で NG                 | update が no-op に戻ったか、新 revision が Ready に昇格していない。`az containerapp revision list -g <RG> --app <CA>` で失敗 revision のログを確認                           |
| deploy が「build-images の成功 run が 1 件も見つかりません」で fail | ghcr にまだ image が無い。Actions → build-images を手動 dispatch してから再デプロイ                                                                                          |
| ロールバックしたい                                                  | `IMAGE_TAG=sha-<戻したい commit>` で `deploy-*.sh` を実行                                                                                                                    |
| build が権限エラー                                                  | workflow の `permissions: packages: write` と、package の Actions access（repo に Write）を確認                                                                              |

## 完了条件（#67）

デプロイ経路から `az acr build` と ACR が消え、image は ghcr の事前ビルド済みタグ差し替えで反映される（待機 ¥750 も消える）。
