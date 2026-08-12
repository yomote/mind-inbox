"""encrypt-e2e-traces.sh の分岐を、gpg をスタブして振る舞いで固定する (ADR 0045 / 0018)。

なぜこのテストが要るか:
    このスクリプトが静かに間違うと **実 BFF アクセストークンを含む trace が public
    リポジトリの artifact として公開される**。しかも「暗号化したつもり」の失敗は
    例外を出さない — 空ファイルでも拡張子は .gpg のままなので、目視でも CI でも
    素通りする (2026-08-12 に実際に踏んだ)。壊れても静かに間違う典型なので、
    テスト戦略 §2.2 の単体テスト入場条件を満たす。

gpg 本体は検証しない (それは gpg の仕事)。ここで固定するのは
**「どういうときに残し、どういうときに残さず落とすか」** という判断だけ。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "encrypt-e2e-traces.sh"

# gpg スタブの雛形。MODE で振る舞いを切り替える。
# --with-colons --import-options show-only --import は指紋を返す経路なので、
# どのモードでも fpr 行を返す (呼び出し側が指紋を取れないと別の分岐に落ちる)。
GPG_STUB = """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "--with-colons" ]; then
    echo "fpr:::::::::DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF:"
    exit 0
  fi
done
case "$1 $2" in
  *"--import"*) exit 0 ;;
esac
# --list-packets: **実 gpg の振る舞いを模す**。公開鍵しか無い環境では復号を試みて
# 失敗するので rc は非ゼロ (既定 2)。ただし暗号化パケットの情報は stdout に出る。
# 中身が暗号文のときだけダンプを出し、平文・空には出さない。
for a in "$@"; do
  if [ "$a" = "--list-packets" ]; then
    target="${!#}"
    if grep -q 'PGP-CIPHERTEXT' "$target" 2>/dev/null; then
      echo "# off=0 ctb=85 tag=1 hlen=3 plen=524"
      echo ":pubkey enc packet: version 3, algo 1, keyid DEADBEEFDEADBEEF"
      # 実データのパケットは「切り詰められていない」ときだけ出る (実 gpg と同じ)
      grep -q 'DATA' "$target" 2>/dev/null && echo ":aead encrypted packet: cipher=9 aead=2 cb=16"
    fi
    exit "${STUB_LIST_PACKETS_RC:-2}"
  fi
done
# --encrypt 経路: -o の次の引数が出力先
out=""
prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
done
case "${STUB_MODE:-ok}" in
  ok)        { printf 'PGP-CIPHERTEXT+DATA'; cat "${!#}"; } > "$out"; exit 0 ;;
  truncated) printf 'PGP-CIPHERTEXT+DATA' > "$out"; exit 0 ;;  # ← パケット名は揃うがサイズが足りない
  pkesk)     printf 'PGP-CIPHERTEXT' > "$out"; exit 0 ;;       # ← PKESK だけの部分書き込み
  fail)      exit 2 ;;
  empty)     : > "$out"; exit 0 ;;   # ← rc=0 なのに中身が空 (2026-08-12 に踏んだ失敗)
  plaintext) cp "${!#}" "$out"; exit 0 ;;  # ← 暗号化せず平文を置く最悪ケース
esac
exit 0
"""


def _setup(tmp_path: Path, *, pubkey: bool = True, traces: int = 1) -> tuple[Path, dict]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gpg = bin_dir / "gpg"
    gpg.write_text(GPG_STUB)
    gpg.chmod(0o755)

    root = tmp_path / "test-results-live"
    for i in range(traces):
        d = root / f"spec-{i}" / "chromium-live"
        d.mkdir(parents=True)
        (d / "trace.zip").write_text("PLAINTEXT-WITH-SECRET")

    key = tmp_path / "pub.asc"
    if pubkey:
        key.write_text("-----BEGIN PGP PUBLIC KEY BLOCK-----\n")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return key, env


def _run(tmp_path: Path, key: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            str(SCRIPT),
            str(key),
            str(tmp_path / "test-results-live"),
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_単体_公開鍵が無いときは_trace_を残さず警告して続行する(tmp_path: Path):
    """無いと何が静かに通るか: 鍵の準備前に **平文の trace が public な artifact として
    上がる**。ここを exit 0 かつ出力なしに倒すのが唯一の安全な既定。
    """
    key, env = _setup(tmp_path, pubkey=False)
    r = _run(tmp_path, key, env)
    assert r.returncode == 0, r.stderr
    assert "::warning::" in r.stdout, "鍵が無いことを黙って握り潰している"
    assert not (tmp_path / "out").exists(), "鍵が無いのに出力を作っている"


def test_単体_暗号化が失敗したら落とす(tmp_path: Path):
    """無いと何が静かに通るか: 暗号化に失敗しても後続の upload が走り、
    中途半端な (あるいは平文の) ファイルが公開されうる。
    """
    key, env = _setup(tmp_path)
    env["STUB_MODE"] = "fail"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1
    assert "::error::" in r.stdout


def test_単体_出力が空なら_成功扱いにせず落とす(tmp_path: Path):
    """2026-08-12 に実際に踏んだ失敗の回帰テスト。

    無いと何が静かに通るか: gpg が rc=0 を返しつつ空ファイルを残すと、拡張子だけを
    見る検査は通ってしまう。「暗号化したつもり」で upload まで進む。
    """
    key, env = _setup(tmp_path)
    env["STUB_MODE"] = "empty"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1, "空の .gpg を正常扱いしている"
    assert "OpenPGP" in r.stdout


def test_単体_平文がそのまま置かれたら落とす(tmp_path: Path):
    """無いと何が静かに通るか: 中身が平文のままでも拡張子が .gpg なら通ってしまう。
    サイズだけでなく **中身が暗号文か** を確かめる必要がある。ここが素通りすると
    実トークンを含む trace が public な artifact として公開される。
    """
    key, env = _setup(tmp_path)
    env["STUB_MODE"] = "plaintext"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1
    assert "OpenPGP" in r.stdout


def test_単体_検証に落ちたファイルはディスクに残さない(tmp_path: Path):
    """Codex P1 (PR #300) の回帰テスト。

    無いと何が静かに通るか: 検証に落ちたファイルを残すと、後続の upload ステップが
    `hashFiles` で「.gpg が 1 つでもあれば真」と判定して**部分成果物を公開**する。
    残っているのが平文だった場合、実トークンがそのまま公開される。
    """
    key, env = _setup(tmp_path)
    env["STUB_MODE"] = "plaintext"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1
    leftovers = list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else []
    assert leftovers == [], f"検証に落ちたファイルが残っている: {leftovers}"


def test_単体_PKESK_だけに切り詰められた暗号文は落とす(tmp_path: Path):
    """Codex P2 (PR #300) の回帰テスト。実鍵でも再現を確認済み。

    暗号文を先頭のセッション鍵パケットだけに切り詰めても `gpg --list-packets` は
    rc=0 で `:pubkey enc packet:` を出す。セッション鍵パケットだけを見る検査は
    **復号不能な部分書き込みを通してしまう**。

    無いと何が静かに通るか: 「暗号化済み」として artifact に上がるが、いざ調査
    しようとすると復号できない。証拠が無いことに気づくのは必要になった瞬間で、
    しかも元の trace は既に消えている。
    """
    key, env = _setup(tmp_path)
    env["STUB_MODE"] = "pkesk"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1, "切り詰められた暗号文を正常扱いしている"
    assert "OpenPGP" in r.stdout
    leftovers = list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else []
    assert leftovers == [], f"検証に落ちたファイルが残っている: {leftovers}"


def test_単体_パケット名が揃っていてもサイズが足りなければ落とす(tmp_path: Path):
    """Codex P2 2 巡目 (PR #300) の回帰テスト。

    実測: 7196 byte の暗号文を 529 / 700 / 3000 byte に切り詰めても
    `gpg --list-packets` は**同じ rc・同じパケット名**を出し、警告も出さない。
    つまり**パケット名の検査では切り詰めを検出できない**。

    `-z 0` (無圧縮) なら暗号文は必ず平文以上のサイズになるので、その不変条件で補う。

    無いと何が静かに通るか: 「暗号化済み」として artifact に残るのに復号できない。
    気づくのは証拠が必要になった瞬間で、そのとき元の trace はもう無い。
    """
    key, env = _setup(tmp_path)
    env["STUB_MODE"] = "truncated"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1, "パケット名だけ揃った短いファイルを通している"
    leftovers = list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else []
    assert leftovers == [], f"検証に落ちたファイルが残っている: {leftovers}"


def test_単体_秘密鍵が無くて_list_packets_が非ゼロでも暗号化できていれば通す(tmp_path: Path):
    """2026-08-12 に実鍵で踏んだ失敗の回帰テスト。

    `gpg --list-packets` は秘密鍵があれば復号まで試みるため、**公開鍵しか持たない
    CI ランナーでは必ず非ゼロ**で終わる。終了コードで判定する実装は本番で 100%
    落ち、trace が永久にアップロードされない (しかもテストは緑のまま)。

    無いと何が静かに通るか: 「安全側に倒れている」ように見えて、実際は機能が
    まるごと死んでいる状態に気づけない。
    """
    key, env = _setup(tmp_path)
    env["STUB_LIST_PACKETS_RC"] = "2"  # 公開鍵しか無いランナーと同じ条件
    r = _run(tmp_path, key, env)
    assert r.returncode == 0, f"秘密鍵が無い環境で誤って落ちている: {r.stdout}"
    assert (tmp_path / "out").exists()


def test_単体_trace_が複数でも全件暗号化される(tmp_path: Path):
    """無いと何が静かに通るか: glob や件数照合を間違えると一部だけ暗号化され、
    残りが黙って落ちる (証拠の欠落は「異常なし」に見える)。
    """
    key, env = _setup(tmp_path, traces=3)
    r = _run(tmp_path, key, env)
    assert r.returncode == 0, r.stdout + r.stderr
    out = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(out) == 3, out
    assert all(n.endswith(".gpg") for n in out), out
    assert all("trace.zip" in n for n in out), "元のファイル名が失われている"


def test_単体_trace_が無ければ何も作らずに正常終了する(tmp_path: Path):
    """無いと何が静かに通るか: 対象ゼロを異常扱いすると、E2E が別の理由で落ちた
    (trace を出さない) run まで赤くなり、本当の失敗が埋もれる。
    """
    key, env = _setup(tmp_path, traces=0)
    r = _run(tmp_path, key, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (tmp_path / "out").exists()


def test_単体_スクリプトは実行可能ビットを持つ(tmp_path: Path):
    """無いと何が静かに通るか: workflow から直接呼ぶので、実行権が無いと
    「設定したのに動かない」が CI でしか分からない。
    """
    assert shutil.which("bash"), "前提: bash がある"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} に実行権がない"
