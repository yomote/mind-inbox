"""[統合] cleanup-env.sh が層ガードに材料を渡し、判定に従うかを実際に走らせて見る。

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
  - test_default_mgmt_rg_survives_redirected_mgmt_rg_and_override
      … MGMT_RG を別名に向けて ALLOW_PROTECTED_DELETE=true にするだけで、
        管理系 RG 本体に `az group delete` が飛ぶ (2026-08-14 の内部 judge が実測した経路。
        シェルが --mgmt-rg を「置き換え」として渡す作りに戻ると再発する)

az はスタブ。**通信も課金も発生しない**が、その代わり「az の返し方が実物と違えば
このテストは実物を保証しない」— 見ているのは az の挙動ではなく、
**呼ばれた az サブコマンドの並び**と終了コード。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

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
    echo "${AZ_STUB_INVENTORY:-[]}"
    ;;
  "group delete")
    ;;
  *)
    echo '[]'
    ;;
esac
"""


def _run(
    tmp_path: Path,
    mode: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    # bash があることを**前提として assert する** (skip しない)。
    # skip だとこれが静かに通る: bash 欠如の環境で 4 本全部が skip に落ち、
    # cleanup-env.sh の配線を一度も走らせていない job が exit 0 で緑になる
    # (#445 — PR #437 で塞いだ穴と同型)。このテストが走る CI job
    # (test.yml の test / ubuntu-latest) には bash が常在し、本番側も
    # deploy.yml が bash 前提で cleanup-env.sh を呼ぶので、無いのは
    # 環境の欠陥として落とす。
    assert shutil.which("bash"), (
        "前提: bash がある (cleanup-env.sh の実行に必須。無い環境は未検証ではなく欠陥)"
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
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
        **(extra_env or {}),
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


# 管理系 RG に居る (= 層タグ付きの) Key Vault。override で通ってしまうと、
# 「どのフラグでも消せない」はずの RG が中身ごと消える。
MANAGEMENT_INVENTORY = (
    '[{"type":"Microsoft.KeyVault/vaults","name":"kv-dev-mindbox",'
    '"tags":{"mindInboxLayer":"management"}}]'
)


def test_default_mgmt_rg_survives_redirected_mgmt_rg_and_override(
    tmp_path: Path,
) -> None:
    """再現テスト: MGMT_RG を別名に向けて override しても管理系 RG は消えない。

    無いと: 2026-08-14 の内部 judge が実測した経路が戻る —
    `MGMT_RG=rg-somewhere-else ALLOW_PROTECTED_DELETE=true RG=rg-mgmt-mindbox` で
    `target-is-management-rg` に落ちず、層タグの findings は override で通り、
    `az group delete -n rg-mgmt-mindbox` が 1 回走って exit 0 になる。
    """
    proc, calls = _run(
        tmp_path,
        mode="present",
        extra_env={
            "RG": "rg-mgmt-mindbox",
            "MGMT_RG": "rg-somewhere-else",
            "ALLOW_PROTECTED_DELETE": "true",
            "AZ_STUB_INVENTORY": MANAGEMENT_INVENTORY,
        },
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "target-is-management-rg" in (proc.stdout + proc.stderr)
    # 本体はここ。メッセージだけ出して消していたら意味がない。
    assert not any(c.startswith("group delete") for c in calls), calls


def test_mgmt_rg_adds_protection_for_another_rg(tmp_path: Path) -> None:
    """対照: MGMT_RG は「足す」側としては効く (常に拒否ではないことの証明も兼ねる)。"""
    protected, protected_calls = _run(
        tmp_path / "protected",
        mode="present",
        extra_env={"RG": "rg-ops-mindbox", "MGMT_RG": "rg-ops-mindbox"},
    )
    assert protected.returncode == 3, protected.stdout + protected.stderr
    assert not any(c.startswith("group delete") for c in protected_calls)

    unprotected, unprotected_calls = _run(
        tmp_path / "unprotected",
        mode="present",
        extra_env={"RG": "rg-ops-mindbox"},
    )
    assert unprotected.returncode == 0, unprotected.stderr
    assert any(c.startswith("group delete") for c in unprotected_calls)
