#!/usr/bin/env python3
"""BFF の配布パッケージが Azure Functions の zip deploy に耐える形かを判定する (#420)。

なぜ要るか: `apps/bff` は pnpm 管理で、既定の `node_modules` は symlink + `.pnpm`
仮想ストア構造になる。**Azure Functions の zip deploy は symlink を復元しない**ので、
そのまま送ると「デプロイは成功したのに require が解決できない」形で壊れる。
壊れ方が実環境でしか出ないため、送る前にツリーと zip を数えて止める。

判定をシェルに埋めない (cicd/CLAUDE.md): 走査 (IO) と判定 (純粋関数) を分け、
判定だけを `judge_tree` / `judge_zip` に切り出してテストで押さえる。依存追加や
pnpm の出力構造が変わったときに、CI の `test:scripts` で先に落ちる。

使い方 (build-bff-package.sh から呼ばれる):
    verify_deploy_tree.py tree <stage_dir>
    verify_deploy_tree.py zip  <zip_path>

終了コード: 0 = 送ってよい / 1 = 問題あり (理由を stderr に列挙) / 2 = 使い方が違う
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 明細をここまで出す。超過分は件数を明示して省略する (黙って切らない)。
MAX_LISTED = 20


def _listed(paths: list[str]) -> str:
    head = "\n".join(f"    {p}" for p in paths[:MAX_LISTED])
    rest = len(paths) - MAX_LISTED
    if rest > 0:
        head += f"\n    … ほか {rest} 件"
    return head


def judge_tree(
    symlinks: list[str],
    virtual_store_dirs: list[str],
    missing_deps: list[str],
) -> list[str]:
    """配布用ツリーの走査結果から問題を列挙する。空リスト = 送ってよい。

    無いと何が静かに通るか: pnpm 既定の symlink ツリーがそのまま zip に入り、
    デプロイは成功するのに Functions 上で require が解決できない状態で配信される。

    - symlinks: `find node_modules -type l` の結果
    - virtual_store_dirs: `node_modules/.pnpm` 直下のディレクトリ
      (hoisted でも `lock.yaml` は作られるので、**ファイルではなくディレクトリ**で見る)
    - missing_deps: package.json の `dependencies` のうち実体が無かったもの
    """
    problems: list[str] = []
    if symlinks:
        problems.append(
            f"node_modules に symlink が {len(symlinks)} 本残っています "
            f"(Functions の zip deploy で壊れます):\n{_listed(symlinks)}"
        )
    if virtual_store_dirs:
        problems.append(
            "node_modules/.pnpm に仮想ストアが残っています "
            f"(--node-linker=hoisted が効いていない):\n{_listed(virtual_store_dirs)}"
        )
    if missing_deps:
        problems.append(
            "prod 依存が実体で入っていません: " + ", ".join(sorted(missing_deps))
        )
    return problems


def judge_zip(symlink_entries: list[str]) -> list[str]:
    """zip の中身の走査結果から問題を列挙する。空リスト = 送ってよい。

    この検査が保証すること / しないこと (#424): 現在の `zip -qr` は `-y` が無いので
    symlink を**辿って実体を格納する** — つまり 0 件は「ツリーに symlink が無かった」
    証拠ではない (symlink 入りツリーを同じフラグで固めても 0 件になる)。symlink が
    無いことを保証しているのは tree 側の検査で、ここは**アーカイバ側の前提が変わった
    とき** (`-y` の追加 / 別アーカイバへの差し替え) に気づくための保険。

    無いと何が静かに通るか: 上の前提が変わって zip に symlink エントリが入るように
    なっても、ツリーだけ緑のまま Functions 上で require が解決できない zip を配信する。
    """
    if not symlink_entries:
        return []
    return [
        f"zip に symlink エントリが {len(symlink_entries)} 件含まれています:\n"
        f"{_listed(symlink_entries)}"
    ]


def declared_deps(stage_dir: Path) -> list[str]:
    """配布用ツリーの package.json から prod 依存名を読む (IO のみ)。

    なぜ一覧を持たないか (#424): 以前はここに 4 件を手で写していたため、
    `apps/bff/package.json` 側で依存を**足した**ぶんは検査の網から静かに外れ、
    **改名・削除**したぶんは実体があるのに CD が落ちた。**送る package.json そのもの**
    から導出すれば、この二重管理ごと消える (staging には build-bff-package.sh が
    `apps/bff/package.json` をそのまま cp している)。

    「symlink 0 本」は node_modules が空でも成立するため、**中身があること**は
    この一覧との突き合わせ (scan_tree の missing) で見る。読めなかった / 空だった
    ときに黙って「欠品 0 件」に化けないよう、ここで落とす。
    """
    manifest = stage_dir / "package.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"package.json がありません: {manifest}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # 壊れた manifest を「欠品 0 件」にしない
        raise ValueError(f"package.json を読めません: {manifest}: {exc}") from exc
    deps = data.get("dependencies")
    if not isinstance(deps, dict) or not deps:
        raise ValueError(
            "package.json に dependencies がありません "
            f"(prod 依存の突き合わせができない): {manifest}"
        )
    return sorted(deps)


def scan_tree(stage_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """配布用ツリーを走査する (IO のみ。判定は judge_tree)。"""
    modules = stage_dir / "node_modules"
    if not modules.is_dir():
        # 「走査できなかった」を「symlink 0 本」に化けさせない
        raise FileNotFoundError(f"node_modules がありません: {modules}")
    symlinks = sorted(
        str(p.relative_to(stage_dir)) for p in modules.rglob("*") if p.is_symlink()
    )
    store = modules / ".pnpm"
    virtual_store_dirs = (
        sorted(
            str(p.relative_to(stage_dir))
            for p in store.iterdir()
            if p.is_dir() and not p.is_symlink()
        )
        if store.is_dir()
        else []
    )
    missing = [
        d
        for d in declared_deps(stage_dir)
        if not (modules / d / "package.json").is_file()
    ]
    return symlinks, virtual_store_dirs, missing


def scan_zip(zip_path: Path) -> list[str]:
    """zip の symlink エントリを走査する (zipinfo の権限欄が l で始まるもの)。"""
    if not zip_path.is_file():
        raise FileNotFoundError(f"zip がありません: {zip_path}")
    out = subprocess.run(
        ["zipinfo", "-l", str(zip_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    entries = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith("l"):
            entries.append(fields[-1])
    return entries


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("tree", "zip"):
        print(__doc__, file=sys.stderr)
        return 2
    mode, target = argv[1], Path(argv[2])
    if mode == "tree":
        symlinks, store, missing = scan_tree(target)
        print(f"symlinks in node_modules: {len(symlinks)}")
        problems = judge_tree(symlinks, store, missing)
    else:
        entries = scan_zip(target)
        print(f"zip symlink entries: {len(entries)}")
        problems = judge_zip(entries)
    for p in problems:
        print(f"ERROR: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
