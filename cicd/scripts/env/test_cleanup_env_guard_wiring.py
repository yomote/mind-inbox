"""[統合] cleanup-env.sh が持続層ガードに材料を渡し、判定に従うかを実際に走らせて見る。

`persistent_layer_guard.py` の pytest は**純粋関数の判定**しか見ていない。判定を
正しくしても、シェル側が材料 (これまでの判定コード) を渡さなくなれば、
「不在で通した RG が再出現しても消す」経路は黙って戻る — そこはユニットテストの
外側なので、az を差し替えて**スクリプトそのもの**を走らせる。

各テストの「無いと何が静かに通るか」:
  - test_refuses_when_rg_reappears_between_checks
      … cleanup-env.sh が `--previous-code` を渡すのをやめても誰も気づかず、
        中身を一度も検証していない RG に `az group delete` が飛ぶ
  - test_deletes_when_rg_is_present_throughout
      … 上のテストが「常に拒否」でも緑になる (= 拒否の証明にならない) のを防ぐ対照

az はスタブ。**通信も課金も発生しない**が、その代わり「az の返し方が実物と違えば
このテストは実物を保証しない」— 見ているのは az の挙動ではなく、
**呼ばれた az サブコマンドの並び**と終了コード。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "cleanup-env.sh"

# 1 回目の `az group exists` だけ false を返し、2 回目以降は true にする
# (= 判定と削除の間に provision が RG を作り直した状況)。
AZ_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$AZ_STUB_LOG"
case "$1 $2" in
  "account show")
    echo '{}'
    ;;
  "group exists")
    seen="$(grep -c '^group exists' "$AZ_STUB_LOG" || true)"
    if [[ "$AZ_STUB_MODE" == "reappear" && "$seen" -le 1 ]]; then
      echo false
    else
      echo true
    fi
    ;;
  "resource list")
    echo '[]'
    ;;
  "group delete")
    ;;
  *)
    echo '[]'
    ;;
esac
"""


def _run(
    tmp_path: Path, mode: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "az"
    stub.write_text(AZ_STUB, encoding="utf-8")
    stub.chmod(0o755)

    log = tmp_path / "az-calls.log"
    log.write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "AZ_STUB_LOG": str(log),
        "AZ_STUB_MODE": mode,
        "RG": "rg-dev-mind-inbox",
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    return proc, log.read_text(encoding="utf-8").splitlines()


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    # スキップは「通った」ではない: bash が無い環境ではこの層は**未検証**。
    reason="bash が無いのでスクリプトを走らせられない (この層は未検証)",
)


def test_refuses_when_rg_reappears_between_checks(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, mode="reappear")

    assert proc.returncode == 3, proc.stderr
    assert "rg-reappeared-after-absent" in (proc.stdout + proc.stderr)
    # 何も消していないこと。ここが本体 (メッセージだけ出して消していたら意味がない)。
    assert not any(c.startswith("group delete") for c in calls), calls


def test_deletes_when_rg_is_present_throughout(tmp_path: Path) -> None:
    """対照: 遷移が無ければ普通に消す (上の拒否が「常に拒否」でないことの証明)。"""
    proc, calls = _run(tmp_path, mode="present")

    assert proc.returncode == 0, proc.stderr
    assert any(c.startswith("group delete") for c in calls), calls
