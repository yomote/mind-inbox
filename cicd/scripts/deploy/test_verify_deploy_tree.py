"""BFF 配布パッケージの検査 (verify_deploy_tree.py) の回帰テスト (#420)。

守っている仕様: pnpm の `node_modules` は既定で symlink + 仮想ストア構造になり、
**Azure Functions の zip deploy は symlink を復元しない**。この判定が緩むと、
デプロイ自体は成功したまま Functions 上で require が解決できないパッケージを
配信できてしまう (実環境でしか出ない壊れ方)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from verify_deploy_tree import (  # noqa: E402
    REQUIRED_DEPS,
    judge_tree,
    judge_zip,
    scan_tree,
)


def _make_stage(tmp_path: Path) -> Path:
    """symlink 無し・prod 依存が実体で入っている正常なツリーを作る。"""
    modules = tmp_path / "node_modules"
    for dep in REQUIRED_DEPS:
        pkg = modules / dep
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text("{}")
    (modules / ".pnpm").mkdir()
    (modules / ".pnpm" / "lock.yaml").write_text("")  # hoisted でも作られる
    return tmp_path


# ── judge_tree (純粋関数) ─────────────────────────────────────────────────────


def test_単体_symlink_も仮想ストアも欠品も無ければ配布してよい():
    """無いとこれが通る: 何も問題が無いのに配布を止める (逆に CD が死ぬ)。"""
    assert judge_tree([], [], []) == []


def test_単体_symlink_が_1_本でもあれば止める():
    """無いとこれが静かに通る: pnpm 既定の symlink ツリーがそのまま Azure へ行く。"""
    problems = judge_tree(["node_modules/zod"], [], [])
    assert len(problems) == 1
    assert "symlink が 1 本" in problems[0]
    assert "node_modules/zod" in problems[0]


def test_単体_仮想ストアにディレクトリが居たら止める():
    """無いとこれが静かに通る: --node-linker=hoisted が効いていない zip を送る。"""
    problems = judge_tree([], ["node_modules/.pnpm/zod@3.25.76"], [])
    assert len(problems) == 1
    assert "仮想ストア" in problems[0]


def test_単体_prod_依存が実体で入っていなければ止める():
    """無いとこれが静かに通る: 「symlink 0 本」は node_modules が空でも成立する。"""
    problems = judge_tree([], [], ["zod"])
    assert len(problems) == 1
    assert "zod" in problems[0]


def test_単体_問題は握り潰さず全件返す():
    """無いとこれが静かに通る: 最初の 1 件だけ直して残りに気づかない。"""
    assert len(judge_tree(["a"], ["b"], ["zod"])) == 3


def test_単体_明細は打ち切るが件数は隠さない():
    """無いとこれが静かに通る: 大量の symlink が 20 件に見え、規模を読み違える。"""
    problems = judge_tree([f"node_modules/p{i}" for i in range(99)], [], [])
    assert "symlink が 99 本" in problems[0]
    assert "ほか 79 件" in problems[0]


# ── judge_zip ────────────────────────────────────────────────────────────────


def test_単体_zip_に_symlink_エントリが無ければ配布してよい():
    """無いとこれが通る: 正常な zip を止める。"""
    assert judge_zip([]) == []


def test_単体_zip_に_symlink_エントリがあれば止める():
    """無いとこれが静かに通る: ツリーは実体でも zip の作り方で symlink が混ざる。"""
    problems = judge_zip(["node_modules/zod"])
    assert len(problems) == 1
    assert "symlink エントリが 1 件" in problems[0]


# ── scan_tree (IO) ───────────────────────────────────────────────────────────


def test_単体_正常なツリーの走査は何も問題を返さない(tmp_path: Path):
    """走査と判定を繋いだ経路が、実ファイルに対して緑になることを押さえる。"""
    stage = _make_stage(tmp_path)
    symlinks, store, missing = scan_tree(stage)
    assert (symlinks, store, missing) == ([], [], [])
    assert judge_tree(symlinks, store, missing) == []


def test_単体_仮想ストア構造を走査すると_symlink_を検出する(tmp_path: Path):
    """pnpm 既定 (isolated) の形を作って、走査が実際に symlink を数えることを確認する。"""
    stage = _make_stage(tmp_path)
    modules = stage / "node_modules"
    real = modules / ".pnpm" / "left-pad@1.3.0" / "node_modules" / "left-pad"
    real.mkdir(parents=True)
    (real / "package.json").write_text("{}")
    (modules / "left-pad").symlink_to(real)

    symlinks, store, missing = scan_tree(stage)
    assert symlinks == ["node_modules/left-pad"]
    assert store == ["node_modules/.pnpm/left-pad@1.3.0"]
    assert judge_tree(symlinks, store, missing)


def test_単体_node_modules_が無いのを_0_件と混同しない(tmp_path: Path):
    """無いとこれが静かに通る: 走査できなかったツリーが「symlink 0 本」に化ける。"""
    with pytest.raises(FileNotFoundError):
        scan_tree(tmp_path)
