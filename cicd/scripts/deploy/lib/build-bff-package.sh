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

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
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

# ── Verify ────────────────────────────────────────────────────────────────────
# 「hoisted を指定したか」ではなく**実際のツリーを数えて**判定する。
echo ""
echo "=== Verifying deploy tree has no symlinks ==="
# `find | head` にしない — pipefail で find が SIGPIPE を受け、検査の失敗が
# 「141 で落ちた」に化けて理由が読めなくなる。配列に取ってから数える。
mapfile -t SYMLINKS < <(find node_modules -type l)
echo "symlinks in node_modules: ${#SYMLINKS[@]}"
if [[ "${#SYMLINKS[@]}" -ne 0 ]]; then
  echo "ERROR: node_modules に symlink が ${#SYMLINKS[@]} 本残っています (Functions の zip deploy で壊れます)" >&2
  printf '  %s\n' "${SYMLINKS[@]:0:20}" >&2
  exit 1
fi
# hoisted でも node_modules/.pnpm/lock.yaml (メタデータ 1 ファイル) は作られる。
# 壊れるのは**仮想ストアに実体が入り、top-level から symlink で参照される**形なので、
# 「.pnpm の存在」ではなく「.pnpm 配下にパッケージのディレクトリがあるか」で見る。
mapfile -t VIRTUAL_STORE < <(find node_modules/.pnpm -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
if [[ "${#VIRTUAL_STORE[@]}" -ne 0 ]]; then
  echo "ERROR: node_modules/.pnpm に仮想ストアが残っています (--node-linker=hoisted が効いていない)" >&2
  printf '  %s\n' "${VIRTUAL_STORE[@]:0:10}" >&2
  exit 1
fi
# 実体が入っていること自体も確認する — 「symlink 0 本」は node_modules が空でも成立する。
for dep in @azure/functions @azure/cosmos @trpc/server zod; do
  [[ -f "node_modules/$dep/package.json" ]] || {
    echo "ERROR: node_modules/$dep が実体で入っていません" >&2
    exit 1
  }
done
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

# ステージングが正しくても zip の作り方次第で symlink エントリは入りうるので、
# **送るファイルそのもの**も数える。zipinfo の権限欄が l で始まるものが symlink。
mapfile -t ZIP_SYMLINKS < <(zipinfo -l "$ZIP_PATH" | awk '$1 ~ /^l/ {print $NF}')
if [[ "${#ZIP_SYMLINKS[@]}" -ne 0 ]]; then
  echo "ERROR: zip に symlink エントリが ${#ZIP_SYMLINKS[@]} 件含まれています:" >&2
  printf '  %s\n' "${ZIP_SYMLINKS[@]:0:20}" >&2
  exit 1
fi
echo "zip symlink entries: 0"

ZIP_BYTES="$(stat -c%s "$ZIP_PATH" 2>/dev/null || stat -f%z "$ZIP_PATH")"
echo "Zip size: $ZIP_BYTES bytes ($ZIP_PATH)"
