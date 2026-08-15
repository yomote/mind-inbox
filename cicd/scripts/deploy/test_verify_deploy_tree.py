"""BFF 配布パッケージの検査 (verify_deploy_tree.py) の回帰テスト (#420 / #424)。

守っている仕様: pnpm の `node_modules` は既定で symlink + 仮想ストア構造になり、
**Azure Functions の zip deploy は symlink を復元しない**。この判定が緩むと、
デプロイ自体は成功したまま Functions 上で require が解決できないパッケージを
配信できてしまう (実環境でしか出ない壊れ方)。

#424 で足した網 (検査そのものが空振りしていないかを見る層):
- `scan_zip` は唯一の外部コマンド (`zipinfo`) 出力パーサなので、**実 zip** を食わせる。
  合成データの assert だけでは、書式が変わって恒久的に 0 件を返しても緑のまま。
- prod 依存の一覧は手写しをやめて package.json から導出した (`declared_deps`)。
  導出が空振り / key 取り違えを起こしていないことをここで押さえる。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from verify_deploy_tree import (  # noqa: E402
    declared_deps,
    judge_tree,
    judge_zip,
    scan_tree,
    scan_zip,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BFF_MANIFEST = REPO_ROOT / "apps" / "bff" / "package.json"

# fixture の zip は本物の zip/zipinfo で作る (書式パーサを押さえるのが目的なので
# 自前生成では意味が無い)。


def _assert_zip_tools() -> None:
    """zip / zipinfo があることを**前提として assert する** (skip しない)。

    無いとこれが静かに通る: ツール欠如の環境で skip に落とすと、パーサを一度も
    通していない job が exit 0 で緑になり、#424 で塞いだ「検査が空振りしていても
    気づけない」穴が環境依存で開き直る。build-bff-package.sh が `need zip` /
    `need zipinfo` で必須宣言しているものなので、無いのは環境の欠陥として落とす。
    """
    for tool in ("zip", "zipinfo"):
        assert shutil.which(tool), (
            f"前提: {tool} がある (build-bff-package.sh が need で必須宣言している)"
        )

_STAGE_DEPS = ("@azure/functions", "@azure/cosmos", "@trpc/server", "zod")


def _make_stage(tmp_path: Path, deps: tuple[str, ...] = _STAGE_DEPS) -> Path:
    """symlink 無し・prod 依存が実体で入っている正常なツリーを作る。"""
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {d: "^1.0.0" for d in deps}}),
        encoding="utf-8",
    )
    modules = tmp_path / "node_modules"
    for dep in deps:
        pkg = modules / dep
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text("{}")
    (modules / ".pnpm").mkdir()
    (modules / ".pnpm" / "lock.yaml").write_text("")  # hoisted でも作られる
    return tmp_path


def _make_symlink_tree(tmp_path: Path) -> Path:
    """pnpm 既定 (isolated) の形 — `node_modules/left-pad` が仮想ストアへの symlink。"""
    modules = tmp_path / "node_modules"
    real = modules / ".pnpm" / "left-pad@1.3.0" / "node_modules" / "left-pad"
    real.mkdir(parents=True)
    (real / "package.json").write_text("{}")
    (modules / "left-pad").symlink_to(real)
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
    stage = _make_symlink_tree(_make_stage(tmp_path))

    symlinks, store, missing = scan_tree(stage)
    assert symlinks == ["node_modules/left-pad"]
    assert store == ["node_modules/.pnpm/left-pad@1.3.0"]
    assert judge_tree(symlinks, store, missing)


def test_単体_node_modules_が無いのを_0_件と混同しない(tmp_path: Path):
    """無いとこれが静かに通る: 走査できなかったツリーが「symlink 0 本」に化ける。"""
    with pytest.raises(FileNotFoundError):
        scan_tree(tmp_path)


# ── declared_deps (prod 依存の導出) ───────────────────────────────────────────
# 手写しの REQUIRED_DEPS を廃した経緯は #424。足した依存が静かに網から外れ、
# 改名した依存で CD が落ちる二重管理を、送る package.json からの導出で消している。


def test_単体_prod_依存は_package_json_から導出し_dev_依存は含めない(tmp_path: Path):
    """無いとこれが静かに通る: devDependencies まで必須扱いになり、--prod の zip が常に落ちる。"""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"zod": "^3.23.8", "@azure/functions": "^4.16.2"},
                "devDependencies": {"vitest": "^3.2.6"},
            }
        ),
        encoding="utf-8",
    )
    assert declared_deps(tmp_path) == ["@azure/functions", "zod"]


def test_単体_package_json_に足した依存はそのまま検査対象になる(tmp_path: Path):
    """無いとこれが静かに通る: 一覧の手写しを忘れた新規依存が、欠品でも止まらない。"""
    stage = _make_stage(tmp_path, deps=("zod",))
    manifest = stage / "package.json"
    manifest.write_text(
        json.dumps({"dependencies": {"zod": "^3.23.8", "@azure/cosmos": "^4.10.0"}}),
        encoding="utf-8",
    )

    _, _, missing = scan_tree(stage)
    assert missing == ["@azure/cosmos"]  # 実体が無いので止まる


def test_単体_package_json_が無いのを_欠品_0_件と混同しない(tmp_path: Path):
    """無いとこれが静かに通る: 依存一覧を読めなかったツリーが「欠品なし」で通る。"""
    (tmp_path / "node_modules").mkdir()
    with pytest.raises(FileNotFoundError):
        scan_tree(tmp_path)


@pytest.mark.parametrize(
    "manifest_text",
    ['{"dependencies": {}}', '{"devDependencies": {"vitest": "1"}}', "{ not json"],
    ids=["空", "dependencies_無し", "壊れている"],
)
def test_単体_依存を読めない_manifest_は落とす(tmp_path: Path, manifest_text: str):
    """無いとこれが静かに通る: 壊れた/空の manifest が「必須依存 0 件」に化けて素通りする。"""
    (tmp_path / "package.json").write_text(manifest_text, encoding="utf-8")
    with pytest.raises(ValueError):
        declared_deps(tmp_path)


def test_単体_実際の_bff_の_package_json_から_prod_依存を導出できる():
    """staging に cp される実物 (apps/bff/package.json) で導出が成立することを押さえる。

    無いとこれが静かに通る: 実際の manifest の形 (dependencies の位置や型) が変わって
    導出が空振りしても、合成 fixture のテストだけ緑のまま CD の欠品検査が死ぬ。
    """
    manifest = json.loads(BFF_MANIFEST.read_text(encoding="utf-8"))
    derived = declared_deps(BFF_MANIFEST.parent)

    assert derived == sorted(manifest["dependencies"])
    assert derived, "prod 依存が 1 件も導出できていない (検査が空振りする)"
    assert not set(derived) & set(manifest["devDependencies"])


# ── scan_zip (外部コマンド zipinfo の出力パーサ) ──────────────────────────────
# 実 zip を食わせる理由 (#424): ここは唯一の外部コマンド出力パーサで、合成データの
# assert だけだと zipinfo の書式が変わっても恒久的に 0 件を返して緑のままになる。


def _zip(cwd: Path, name: str, *flags: str) -> Path:
    zip_path = cwd / name
    subprocess.run(
        ["zip", "-qr", *flags, str(zip_path), "node_modules"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    return zip_path


def test_単体_symlink_を保持した_zip_から_symlink_エントリを拾える(tmp_path: Path):
    """無いとこれが静かに通る: zipinfo の書式変更でパーサが恒久的に 0 件を返し、
    zip 検査が「常に合格」になっても誰も気づかない。"""
    _assert_zip_tools()
    stage = _make_symlink_tree(tmp_path)
    zip_path = _zip(stage, "with-symlink.zip", "-y")  # -y = symlink を実体化しない

    entries = scan_zip(zip_path)
    assert entries == ["node_modules/left-pad"]
    assert judge_zip(entries)  # 判定まで繋いで止まることを見る


def test_単体_実体化する_zip_フラグでは_symlink_エントリが出ないことを明示する(
    tmp_path: Path,
):
    """build-bff-package.sh と同じフラグ (`-y` 無し) では symlink が実体化されるため、
    zip 側の 0 件は「ツリーに symlink が無かった」証拠にならない — この非保証を
    仕様として固定する。無いとこれが静かに通る: 0 件を tree 検査の裏取りだと誤読し、
    tree 検査を弱めても気づけなくなる。"""
    _assert_zip_tools()
    stage = _make_symlink_tree(tmp_path)
    zip_path = _zip(stage, "dereferenced.zip")

    assert scan_zip(zip_path) == []
    # 実体化されているので、リンク先の中身は zip に入っている
    listed = subprocess.run(
        ["zipinfo", "-1", str(zip_path)], check=True, capture_output=True, text=True
    ).stdout
    assert "node_modules/left-pad/package.json" in listed.splitlines()


def test_単体_symlink_の無い_zip_は_何も検出しない(tmp_path: Path):
    """無いとこれが通る: 正常な zip を止める (パーサが行を取り違えている)。"""
    _assert_zip_tools()
    stage = _make_stage(tmp_path)
    assert scan_zip(_zip(stage, "clean.zip")) == []


def test_単体_zip_が無いのを_0_件と混同しない(tmp_path: Path):
    """無いとこれが静かに通る: 作られなかった zip が「symlink エントリ 0 件」で通る。"""
    with pytest.raises(FileNotFoundError):
        scan_zip(tmp_path / "missing.zip")
