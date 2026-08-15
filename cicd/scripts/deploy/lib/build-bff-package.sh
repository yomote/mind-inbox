#!/usr/bin/env bash
# BFF (Azure Functions v4) の zip deploy 用パッケージを作る。
#
# az を一切使わないので、**デプロイせずにローカルで実行して中身を検証できる**:
#   cicd/scripts/deploy/lib/build-bff-package.sh
#   unzip -l .local/functionapp.zip
#
# なぜ deploy-backend.sh から切り出しているか (#420):
#   apps/bff は pnpm 管理になった。pnpm 既定の node_modules は symlink + .pnpm ストア
#   構造で、Azure Functions の zip deploy はこれを復元しない — 「デプロイは成功したのに
#   require が解決できない」形で壊れる。壊れ方が実環境でしか出ないため、**zip を作る
#   工程だけを az 抜きで回せる形**にして、symlink が 1 本も無いことをローカルで
#   機械確認できるようにしている。
#
# 出力: $ZIP_PATH (既定 <repo>/.local/functionapp.zip)
set -euo pipefail

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}

need pnpm
need zip
# zip の中身の symlink 検査に使う。無いなら検査できない = 「確認できていない」ので、
# 黙って飛ばさずここで落とす。
need zipinfo
need python3 # 検査の判定 (verify_deploy_tree.py) を持つ

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
BFF_DIR="$ROOT_DIR/apps/bff"
ZIP_PATH="${ZIP_PATH:-$ROOT_DIR/.local/functionapp.zip}"
STAGE_DIR="${STAGE_DIR:-$ROOT_DIR/.local/functionapp-pkg}"

# ── Build ─────────────────────────────────────────────────────────────────────
echo "=== Building BFF ==="
cd "$BFF_DIR"
pnpm install --frozen-lockfile
pnpm run build

if [[ ! -d "$BFF_DIR/dist" ]]; then
  echo "ERROR: dist/ not found at $BFF_DIR/dist after build" >&2
  exit 1
fi

# ── Stage ─────────────────────────────────────────────────────────────────────
# apps/bff/node_modules は開発用 (symlink + .pnpm ストア) なので**そのままは zip しない**。
# 配布用に別ディレクトリを作り、prod 依存だけを --node-linker=hoisted で実体展開する。
echo ""
echo "=== Staging deployment tree ($STAGE_DIR) ==="
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp "$BFF_DIR/host.json" "$BFF_DIR/package.json" "$BFF_DIR/pnpm-lock.yaml" \
  "$BFF_DIR/pnpm-workspace.yaml" "$STAGE_DIR/"
cp -R "$BFF_DIR/dist" "$STAGE_DIR/dist"
# package.json の preinstall (npm 誤用ガード) が参照する。ここを --ignore-scripts で
# 回避すると、prod 依存が将来ビルドスクリプトを必要としたときに黙って壊れた
# パッケージを送ることになるので、スクリプトは殺さずファイルを持ってくる。
# zip には含めない (下の zip 対象に scripts/ は入れていない)。
mkdir -p "$STAGE_DIR/scripts"
cp "$BFF_DIR/scripts/only-pnpm.mjs" "$STAGE_DIR/scripts/"

cd "$STAGE_DIR"
pnpm install --frozen-lockfile --prod --node-linker=hoisted
# .bin は CLI へのリンク集で実行時には使われない。残すと symlink 検査に引っかかる
# だけなので落とす (npm 時代の zip でも -x で除外していた)。
rm -rf node_modules/.bin

# ── Verify (tree) ─────────────────────────────────────────────────────────────
# 「hoisted を指定したか」ではなく**実際のツリーを数えて**判定する。
# 判定そのものはシェルに埋めず verify_deploy_tree.py の純粋関数が持つ
# (cicd/CLAUDE.md「判定ロジックをシェルや workflow の中に埋めない」)。
# 回帰は cicd/scripts/deploy/test_verify_deploy_tree.py = `npm run test:scripts`。
echo ""
echo "=== Verifying deploy tree has no symlinks ==="
python3 "$SCRIPT_DIR/../verify_deploy_tree.py" tree "$STAGE_DIR"
echo "node_modules top-level entries: $(find node_modules -mindepth 1 -maxdepth 1 | wc -l)"

# ── Zip ───────────────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$ZIP_PATH")"
rm -f "$ZIP_PATH"

echo ""
echo "=== Creating deployment zip ==="
# Functions v4 (Node) zip layout:
#   /host.json
#   /package.json     (main = "dist/src/functions/*.js")
#   /dist/...         (compiled output)
#   /node_modules/... (production deps — 実体)
zip -qr "$ZIP_PATH" \
  host.json \
  package.json \
  pnpm-lock.yaml \
  dist \
  node_modules \
  -x "node_modules/.cache/*" \
  -x "**/*.map"

# 送るファイルそのものも数える。ただし**この検査が何を保証しないか**を明記しておく (#424):
#   保証しない — 「ツリーに symlink が無かったこと」。上の `zip -qr` は `-y` が無いので
#     symlink を辿って実体を格納する。symlink 入りツリーを同じフラグで固めても
#     エントリは 0 件になるため、ここの 0 件は tree 検査の裏取りにならない。
#     symlink が無いことを保証しているのは上の tree 検査 (実測でこちらが止める)。
#   保証する — **アーカイバ側の前提が変わったこと**の検出。`-y` を足す / zip を別実装に
#     替える / 実体化しないアーカイバに移る、といった変更で symlink が zip に入り始めたら
#     ここで落ちる。
# 検査自体が空振り (zipinfo の書式変更で恒久的に 0 件) になっていないことは、
# `zip -y` で作った実 fixture を食わせる test_verify_deploy_tree.py が押さえる。
python3 "$SCRIPT_DIR/../verify_deploy_tree.py" zip "$ZIP_PATH"

ZIP_BYTES="$(stat -c%s "$ZIP_PATH" 2>/dev/null || stat -f%z "$ZIP_PATH")"
echo "Zip size: $ZIP_BYTES bytes ($ZIP_PATH)"
