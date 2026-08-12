"""golden-path.sh が「落ちた理由」を残すことを、curl をスタブして振る舞いで固定する (#310)。

なぜこのテストが要るか:
    このスクリプトが静かに間違うと **障害の理由がログから消える**。しかも消えたことは
    緑にも赤にもならない — NG 行は出るので「テストは動いている」ように見え、
    書いてある内容だけが無意味になる (2026-08-12 の実ログ:
    `NG  - tts が壊れている (status=000):` — コロンの後ろが空)。
    「壊れても例外が出ず、データが静かに間違う」の典型なので、テスト戦略 §2.2 の
    単体テスト入場条件を満たす。

    この失敗は間欠的でもある (同じ commit で 1.5 時間後の run は 22 秒で成功した)。
    **間欠障害こそ証拠が要る**のに、落ちるたびに理由が消えていた。

curl 本体も HTTP も検証しない (それは curl の仕事)。ここで固定するのは
**「接続レベルで失敗したとき、NG 行に curl の終了コードとその意味が出るか」** だけ。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "golden-path.sh"

# az スタブ。golden-path.sh は (1) deployment outputs (2) アクセストークン の順に叩く。
# ここを通さないとホップまで到達しないので、両方に固定値を返す。
AZ_STUB = """#!/usr/bin/env bash
case "$1 $2" in
  "deployment group")
    cat <<'JSON'
{"state":"Succeeded","outputs":{"functionAppDefaultHostname":{"value":"func-test.example.invalid"},"functionAuthEntraClientId":{"value":"00000000-0000-0000-0000-000000000000"}}}
JSON
    exit 0 ;;
  "account get-access-token") echo "STUB-TOKEN"; exit 0 ;;
esac
exit 1
"""

# curl スタブ。STUB_CURL_RC の終了コードで落ちる。
# 実 curl と同じく **接続に失敗したら http_code は 000、body は空** になるので、
# -w '%{http_code}' 指定時だけ 000 を出し、それ以外は何も出さない。
CURL_STUB = """#!/usr/bin/env bash
rc="${STUB_CURL_RC:-0}"
if [ "$rc" != "0" ]; then
  for a in "$@"; do
    case "$a" in *'%{http_code}'*) printf '000' ;; esac
  done
  exit "$rc"
fi
# rc=0 だが body が空 — 「通信は成立したが応答が空」を模す
for a in "$@"; do
  case "$a" in *'%{http_code}'*) printf '200' ;; esac
done
exit 0
"""


def _run(tmp_path: Path, curl_rc: int) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)  # 同じ tmp_path で rc を変えて何度も呼ぶ
    for name, body in (("az", AZ_STUB), ("curl", CURL_STUB)):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["STUB_CURL_RC"] = str(curl_rc)
    proc = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=120
    )
    return proc.stdout + proc.stderr


def test_単体_接続失敗時に_curl_の終了コードが_NG_行に出る(tmp_path: Path) -> None:
    """rc=52 (サーバ無応答) が理由として残ること。

    これが無いと `status=000` + 空 body だけが残り、7 / 28 / 35 / 52 / 56 の
    どれだったのかが永久に分からない (#310 の実害そのもの)。
    """
    out = _run(tmp_path, 52)

    assert "rc=52" in out, f"curl の終了コードが出ていない:\n{out}"
    assert "サーバが何も返さなかった" in out, (
        f"rc の数字だけでは読めない。意味が添えられていない:\n{out}"
    )


def test_単体_終了コードごとに違う理由が出る(tmp_path: Path) -> None:
    """5 つの rc が同じ 1 行に潰れないこと。

    #310 の核心は「意味の違う 5 つの失敗が全部 `status=000` に見える」ことだった。
    rc を出すだけでは不十分で、**rc ごとに違う文字列**でなければ切り分けにならない。
    """
    seen: dict[int, str] = {}
    for rc in (7, 28, 35, 52, 56):
        out = _run(tmp_path, rc)
        assert f"rc={rc}" in out, f"rc={rc} が出ていない:\n{out}"
        # NG 行だけを取り出して比較する (OK 行や見出しは rc に依らず同じ)
        seen[rc] = "\n".join(line for line in out.splitlines() if line.startswith("NG"))

    assert len(set(seen.values())) == len(seen), (
        "終了コードが違うのに NG 行が同じになっている — 切り分けにならない:\n"
        + "\n".join(f"rc={k}: {v}" for k, v in seen.items())
    )


def test_単体_全ホップが理由を残す(tmp_path: Path) -> None:
    """1 つのホップだけ直して他が残る、を防ぐ。

    ホップは 7 つあり、うち 5 つが NG になる時点で rc を持てる
    (`health.ping` は 000 で早期 exit するため、単独 run では 1 行しか出ない)。
    ここでは **rc を出さない NG 行が 1 つも無い** ことを見る。
    """
    out = _run(tmp_path, 7)
    ng_lines = [line for line in out.splitlines() if line.startswith("NG")]

    assert ng_lines, f"NG 行が 1 つも出ていない (スタブが効いていない):\n{out}"
    silent = [line for line in ng_lines if "rc=" not in line]
    assert not silent, "理由を残していない NG 行がある:\n" + "\n".join(silent)


def test_単体_通信が成立していれば内容の問題だと分かる(tmp_path: Path) -> None:
    """rc=0 のときに rc の話で終わらせないこと。

    「確かめられなかった」と「壊れている」を混ぜないのがこの Issue の趣旨なので、
    **逆方向 (通信は成立している) も言えなければ意味が半分**になる。
    """
    out = _run(tmp_path, 0)

    assert "rc=0" in out
    assert "通信は成立" in out, f"応答内容の問題であることが読み取れない:\n{out}"
