# 0025. コンテナ image のデプロイは :latest ではなく不変 sha タグの解決 + 稼働検証で行う

- Status: Proposed
- Date: 2026-08-08
- Deciders: yomote (承認待ち), Claude (起案)

Technical Story: <https://github.com/yomote/mind-inbox/issues/107> / PR #131

## Context and Problem Statement

ADR 0013 で「image は ghcr に事前ビルドし、デプロイはタグ差し替えのみ」とした。しかし
Azure Container Apps は **image 参照文字列が変わったときだけ**新リビジョンを作るため、
`az containerapp update --image ...:latest` は ghcr 側で `latest` の指し先が進んでいても
ARM 的に変更なし = no-op になる。実際に 2026-08-07、新 image がビルド済みなのに実環境が
前日のリビジョンのまま据え置かれた (#107)。デプロイが緑のまま実環境に届かない構造なので、
「どの参照でデプロイし、載ったことをどう確かめるか」を決める必要がある。

## Decision Drivers

- **確実性**: main にマージしたコードが必ず実環境に届く。届かない場合は赤で気づける
- **追跡性**: 稼働中のリビジョンがどのコミットの image かを一意に辿れる (ロールバック含む)
- **静的シークレット 0 / 低コスト** (ADR 0006 / 0013) を崩さない
- **検証まで含める** (ADR 0018): 「設定した」ではなく「載った」を実測する

## Considered Options

- Option A: CD が build-images 成功 run の sha タグを解決して渡し、smoke-test が稼働 revision の image を検証する
- Option B: タグを manifest digest に解決し `...@sha256:...` で update する (ghcr Registry API 直)
- Option C: `--revision-suffix` 等で毎デプロイ強制的に新リビジョンを作る (タグは :latest のまま)
- Option D: 運用ルールで「コンテナ変更時は IMAGE_TAG=sha-… を手動指定」とする (コード変更なし)

## Decision Outcome

Chosen option: **"Option A"** (PR #131 で実装)。参照文字列そのものを不変タグにすることで
「image が進めば必ず新リビジョン」が構造的に成立し、さらに稼働 revision の実測検証で
no-op への退行も検知できるため。具体的には:

1. `deploy.yml` の「Resolve IMAGE_TAG」step が、直近の build-images **成功** run の
   head SHA から `sha-<full-sha>` を解決して `provision.sh` → `deploy-*.sh` に渡す。
   同一 commit の build-images run が走行中なら `gh run watch` で完了を待つ
   (待たないと一つ前の image を載せてしまう)。成功 run が無ければ :latest に
   フォールバックせず fail する
2. `smoke-test.sh` が稼働 revision (`latestReadyRevisionName`) の image タグを
   `EXPECTED_IMAGE_TAG` と突合し、不一致なら NG (デプロイが赤くなる)。
   `inspect-env.sh` は稼働 image / revision / 作成時刻をダンプする
3. 手動実行の安全網として、`deploy-*.sh` は「現在の image と同一文字列での update」を
   検出して WARN を出す

### Positive Consequences

- main マージ → 実環境反映が構造的に保証され、届かなければ CD が赤で止まる
- 稼働 image がコミット (sha タグ) で一意に追跡でき、ロールバックも同じ経路で行える
- image が変わらないデプロイは正しく no-op のまま (scale-to-zero 環境で無駄な再起動をしない)
- 追加の静的シークレット無し (GITHUB_TOKEN の `actions: read` のみ)

### Negative Consequences

- 解決経路が GitHub Actions API (`gh run list/watch`) に依存する (API 障害時はデプロイが
  赤になる — ただし「黙って古いまま」よりよい)
- 手動のスクリプト直叩きは既定が :latest のままで、安全網は WARN 止まり (no-op を構造的には
  塞がない)。塞ぎたくなったら Option B (digest 解決) をスクリプト側に足すのが次の一手
- build-images の対象パスと「デプロイ対象 commit」は一致しないことがある
  (apps/services/\*\* 変更時のみビルドされるため、「最後にビルドされた sha」が正)

## Pros and Cons of the Options

### Option A: build-images 成功 run の sha タグ + 稼働検証 (採用)

- Good, because 参照が不変タグなので「image が進めば必ず新リビジョン」が構造的に正しい
- Good, because git コミットで直接追跡できる (人間が読める参照)
- Good, because 「成功 run の sha」を使うことで build 失敗中は古い正常 image を維持できる
- Bad, because GitHub Actions API への依存が増える

### Option B: digest 解決 (`...@sha256:...`)

- Good, because レジストリの実体を厳密に固定でき、gh の権限も不要 (匿名 pull トークンで可)
- Good, because 手動実行 (:latest) でも構造的に no-op を塞げる
- Bad, because digest は人間に読めず、コミットへの逆引きに一手間かかる
- Bad, because 今回は sha タグ解決で十分で、二重の解決機構は複雑さに見合わない (必要になれば追補)

### Option C: 強制新リビジョン (--revision-suffix)

- Good, because 実装が最小
- Bad, because image が変わらなくても毎回リビジョンが増え、無駄な再起動と履歴ノイズが出る
- Bad, because :latest のままでは「どのコミットが動いているか」の追跡性が回復しない

### Option D: 運用ルールで手動指定

- Good, because コード変更ゼロ
- Bad, because 人間の注意力頼みで、忘れた時の症状が「デプロイ緑・実環境は古いまま」と最悪
- Bad, because ADR 0013 の「main マージで自動反映」が実質崩れたままになる

## Links

- 関連 ADR: [0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) (本 ADR はその
  「タグ差し替えのみ」の参照方式を具体化・修正する) / [0018](0018-runtime-verification-in-the-loop.md)
- Runbook: [ghcr-images.md](../runbooks/ghcr-images.md)
- 実装: PR #131 (Fixes #107)
