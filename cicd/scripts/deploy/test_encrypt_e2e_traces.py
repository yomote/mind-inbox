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
for a in "$@"; do
  [ "$a" = "--list-packets" ] && exit "${STUB_LIST_PACKETS_RC:-0}"
done
# --encrypt 経路: -o の次の引数が出力先
out=""
prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
done
case "${STUB_MODE:-ok}" in
  ok)      printf 'PGP-CIPHERTEXT' > "$out"; exit 0 ;;
  fail)    exit 2 ;;
  empty)   : > "$out"; exit 0 ;;   # ← rc=0 なのに中身が空 (今日踏んだ失敗)
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


def test_単体_OpenPGP_として読めない出力は落とす(tmp_path: Path):
    """無いと何が静かに通るか: 中身が平文のままでも拡張子が .gpg なら通ってしまう。
    サイズだけでなく **中身が暗号文か** を確かめる必要がある。
    """
    key, env = _setup(tmp_path)
    env["STUB_LIST_PACKETS_RC"] = "2"
    r = _run(tmp_path, key, env)
    assert r.returncode == 1
    assert "OpenPGP" in r.stdout


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
