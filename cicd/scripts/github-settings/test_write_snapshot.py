"""[L1] write-snapshot.sh の git 運搬 — #466 と同型のフック事故を防ぐ。

**無いと何が静かに通るか**: このスクリプトはデータブランチ `data/github-settings`
への唯一の書き込み口で、GitHub 設定ドリフトの記録 (`git log -p` がそのまま
「いつ設定が変わったか」) を作る。worktree の commit が呼び出し元 repo の
`core.hooksPath` を拾って落ちると、**観測は取れているのに記録だけが古いまま止まる**。
止まったことは誰にも見えない (点検 run は緑で終わる) ので、次に設定を見比べた人が
古いスナップショットを「現在の設定」として読む。

同型の事故は #466 (UX 観測側) で実際に起きており、本テストはその生存箇所を塞いだ
PR #475 の代役レビュー指摘から足したもの。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "write-snapshot.sh"
BRANCH = "data/github-settings"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    _git("config", "user.email", "t@example.test", cwd=work)
    (work / "app.txt").write_text("main の中身\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work)
    _git("push", "origin", "main", cwd=work)
    return origin, work


def _snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"observedAt": "2026-08-16T00:00:00Z", "settings": {}}),
        encoding="utf-8",
    )
    return path


def _run(work: Path, snapshot: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(snapshot), "yomote/mind-inbox"],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )


def test_l1_呼び出し元にpre_commitフックがあってもスナップショットはpushされる(
    tmp_path,
) -> None:
    """commit から `-c core.hooksPath=/dev/null` を外すとこのテストが落ちる。"""
    origin, work = _setup_repo(tmp_path)
    hooks = work / "fake-husky"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho 'lint-staged なんて無い' >&2\nexit 1\n")
    pre_commit.chmod(0o755)
    _git("config", "core.hooksPath", str(hooks), cwd=work)

    # 対照実験: フックが実際に効いている checkout であることを先に確かめる。
    # これを省くと「フックが元から無い」状態でも下の assert が緑になる
    (work / "canary.txt").write_text("x\n")
    _git("add", "-A", cwd=work)
    blocked = subprocess.run(
        ["git", "commit", "-m", "フックに止められるはず"],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode != 0, "pre-commit フックが効いていない — 前提が崩れている"

    r = _run(work, _snapshot(tmp_path))
    assert r.returncode == 0, r.stderr
    content = _git("show", f"{BRANCH}:snapshots/yomote/mind-inbox.json", cwd=origin)
    assert json.loads(content)["observedAt"] == "2026-08-16T00:00:00Z"


def test_l1_内容が同じならpushしない(tmp_path) -> None:
    """点検のたびにコミットを立てない (履歴が「変化の記録」であり続ける)。"""
    origin, work = _setup_repo(tmp_path)
    snapshot = _snapshot(tmp_path)
    assert _run(work, snapshot).returncode == 0
    before = _git("rev-list", "--count", BRANCH, cwd=origin)
    r = _run(work, snapshot)
    assert r.returncode == 0, r.stderr
    assert _git("rev-list", "--count", BRANCH, cwd=origin) == before
