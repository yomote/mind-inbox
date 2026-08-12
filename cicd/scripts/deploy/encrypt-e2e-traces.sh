#!/usr/bin/env bash
# 実環境 E2E (e2e-live) の trace を公開鍵で暗号化する。判断の正典は ADR 0045。
#
# なぜ暗号化なのか (スクラブではなく):
#   trace には実 BFF アクセストークンが入る (entra-login.ts が Entra の token
#   エンドポイントを偽装して実トークンを返すため。BFF の EasyAuth が本物なので
#   偽装できない)。**このリポジトリは public** で、Actions の artifact は
#   サインイン済みユーザーなら誰でも取れるため、素で上げられない。
#   2026-08-12 の実測で秘密は trace 内の 3 種類のエントリ (spec のソース /
#   アクション記録 / DOM スナップショット) に散らばっており、外科的な除去は
#   列挙漏れで静かに漏れる。暗号化は中身の形に依存しないので、その失敗モードが無い。
#
# 使い方: encrypt-e2e-traces.sh <公開鍵> <trace のルート> <出力ディレクトリ>
# 終了コード: 0 = 正常 (暗号化した / 対象なし / 鍵が無いので見送り) / 1 = 異常
set -uo pipefail

PUBKEY="${1:?公開鍵のパスを指定してください}"
TRACE_ROOT="${2:?trace のルートディレクトリを指定してください}"
OUT_DIR="${3:?出力ディレクトリを指定してください}"

# 公開鍵が未設置なら trace を残さず、必ず 1 行喋って続行する (ADR 0045 D6)。
# 「鍵が無いから平文で上げる」を構造的に不可能にするための分岐。沈黙もしない。
if [ ! -f "$PUBKEY" ]; then
  echo "::warning::公開鍵 $PUBKEY が無いため trace を残しません (cicd/keys/README.md の手順で設置してください)"
  exit 0
fi

shopt -s globstar nullglob
traces=("$TRACE_ROOT"/**/trace.zip)
if [ ${#traces[@]} -eq 0 ]; then
  echo "trace が生成されていません (retain-on-failure のため失敗時のみ)"
  exit 0
fi

if ! gpg --batch --import "$PUBKEY"; then
  echo "::error::公開鍵を取り込めませんでした ($PUBKEY)"
  exit 1
fi

RECIPIENT="$(gpg --with-colons --import-options show-only --import "$PUBKEY" \
              | awk -F: '/^fpr:/ { print $10; exit }')"
if [ -z "$RECIPIENT" ]; then
  echo "::error::公開鍵から指紋を取得できませんでした ($PUBKEY が壊れている可能性)"
  exit 1
fi
echo "暗号化の宛先: $RECIPIENT"

mkdir -p "$OUT_DIR"
encrypted=0
for t in "${traces[@]}"; do
  name="$(printf '%s' "${t#"$TRACE_ROOT"/}" | tr '/' '_')"
  out="$OUT_DIR/${name}.gpg"
  if ! gpg --batch --yes --trust-model always \
           --encrypt --recipient "$RECIPIENT" -o "$out" "$t"; then
    echo "::error::暗号化に失敗しました: $t"
    exit 1
  fi
  # 出力が本当に OpenPGP メッセージかを確かめる。gpg が失敗しても空ファイルが残る
  # ことがあり、拡張子だけの検査は素通りする (2026-08-12 に実際に踏んだ)。
  #
  # **終了コードで判定してはいけない** — `--list-packets` は秘密鍵があれば復号まで
  # 試みるため、**公開鍵しか持たない CI ランナーでは必ず非ゼロ**になる
  # (`decryption failed: No secret key`)。rc を見る実装は本番で 100% 落ち、trace が
  # 永久に上がらない。2026-08-12 に実鍵で踏んで気づいた。
  #
  # 代わりに stdout のパケットダンプを見る。`:pubkey enc packet:` はロケールに
  # 依存せず、平文ファイルに対しては 1 行も出ない (実測で確認済み)。
  packets="$(gpg --batch --list-packets "$out" 2>/dev/null || true)"
  if [ ! -s "$out" ] || ! printf '%s' "$packets" | grep -q ':pubkey enc packet:'; then
    echo "::error::出力が OpenPGP メッセージになっていません: $out"
    # **検証に落ちたファイルを残さない。** 残すと、後続の upload ステップが
    # `hashFiles` で「.gpg が 1 つでもあれば真」と判定して部分成果物を公開しうる
    # (Codex P1 / PR #300)。中身が平文の可能性があるものをディスクに置かない。
    rm -f "$out"
    exit 1
  fi
  encrypted=$((encrypted + 1))
  echo "暗号化: $t -> $out"
done

# 「暗号化したつもり」を成功パスの中で潰す (ADR 0045 D7)。
# 1) 平文が混ざっていないこと 2) trace の本数と暗号文の本数が一致すること
if find "$OUT_DIR" -type f ! -name '*.gpg' | grep -q .; then
  echo "::error::暗号化されていないファイルが混ざっています。アップロードを中止します"
  find "$OUT_DIR" -type f ! -name '*.gpg'
  exit 1
fi
if [ "$encrypted" -ne "${#traces[@]}" ]; then
  echo "::error::trace ${#traces[@]} 件に対して暗号文が $encrypted 件しかありません"
  exit 1
fi
echo "検証 OK: trace ${#traces[@]} 件すべてを OpenPGP メッセージとして出力しました"
