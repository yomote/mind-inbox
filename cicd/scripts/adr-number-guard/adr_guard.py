#!/usr/bin/env python3
"""ADR 採番の衝突ガード — 判定の純関数と CI 用エントリ (ADR 0028 D3 / Issue #159 / #381)。

使い方:
    python3 cicd/scripts/adr-number-guard/adr_guard.py --head <sha|ref>
      # .github/workflows/adr-number-guard.yml から呼ばれる。
      # 要: git repo の中 (fetch-depth: 0 相当の履歴と origin remote)

何を検査するか (旧 workflow のシェル 4 検査を純関数に移したもの):
    0. 退役番号の一覧 (docs/adr/archive/retired-numbers.txt) から行が消えている
       (1 行だけ消して同じ PR でその番号を足す経路を塞ぐ)
    1. PR 側で同一番号の ADR が複数ある (自分の中で衝突している)
    2. PR の番号が **今の main** に別名で存在する (並行採番で衝突している)
    3. PR が退役番号 (docs/adr/archive/ へ退避済み) を再利用している

比較先は「今の origin/main」(Issue #381 の本体):
    旧実装は `github.event.pull_request.base.sha` (= PR イベント発火時点の base) と
    比較していた。これは**その時点のスナップショット**であり、guard が緑を出した後に
    並行 PR が同じ番号を main に着地させても再判定されない — PR #222 では ADR 0048 が
    二重化する寸前まで行き、guard は緑のままだった。ここでは run のたびに
    `git fetch origin main` してその tip と比較する。re-run すれば必ず「その時点の
    main」で判定し直される。

    ただし検査 0 (退役一覧からの行削除) だけは **merge-base** と比較する。
    今の main と比べると「PR が branch した後に main へ足された退役番号」が
    『PR が消した』と誤検知される (PR は触っていない = マージしても消えない)。
    「PR が何を消したか」は分岐点基準、「PR が何とぶつかるか」は今の main 基準。

それでも「guard 実行後〜マージ前」の窓は残る:
    マージ直前の再判定は review-gate が担う — `cicd/scripts/review-gate/check.py`
    が pm-accept のたび (issue_comment / sweep / マージ直前の再評価) に、この
    module の gate_conflicts() で **checkout 済みの main 作業ツリー** (review-gate
    は常に default branch を checkout する) と照合する。役割分担:
    ここ (CI) = push 時の早期フィードバック、review-gate = マージ門。
    `cicd/scripts/claude-hooks/adr_number_guard.py` はさらに前倒しの
    「書き始める前」の警告で、CI の置き換えではない (Issue #392)。

判定は純関数 (judge / gate_conflicts)、git・GitHub との入出力は main() に隔離。
テストは test_adr_guard.py (`npm run test:scripts` に登録済み)。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_ADR_PATH = re.compile(r"^docs/adr/(\d{4})-[^/]*\.md$")
_RETIRED_LINE = re.compile(r"^\s*(\d{4})\s*$")
RETIRED_FILE = "docs/adr/archive/retired-numbers.txt"
_GIT_TIMEOUT_SEC = 60


# ---- 判定 (純関数) ----


def adr_number_of(path: str) -> int | None:
    """`docs/adr/NNNN-*.md` なら番号。archive 配下 / template.md / README.md は None。"""
    match = _ADR_PATH.match(path)
    return int(match.group(1)) if match else None


def adr_numbers(paths: list[str]) -> dict[int, tuple[str, ...]]:
    """番号 → その番号を持つファイル一覧 (ソート済み)。番号なしのパスは無視。"""
    by_number: dict[int, list[str]] = {}
    for path in paths:
        number = adr_number_of(path)
        if number is not None:
            by_number.setdefault(number, []).append(path)
    return {n: tuple(sorted(files)) for n, files in by_number.items()}


def parse_retired(text: str) -> set[int]:
    """退役番号の一覧。`#` 始まりはコメント。"""
    numbers: set[int] = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _RETIRED_LINE.match(line)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


@dataclass(frozen=True)
class Violation:
    kind: str  # "retired-removed" | "dup" | "collision" | "retired-reuse"
    number: int
    head_paths: tuple[str, ...] = ()
    base_paths: tuple[str, ...] = ()


def judge(
    head_paths: list[str],
    main_paths: list[str],
    head_retired: set[int],
    main_retired: set[int],
    ancestor_retired: set[int],
) -> list[Violation]:
    """採番検査の本体。head (PR 側) を main (今の合流先) と照合する。

    ancestor_retired は merge-base 時点の退役一覧 — 検査 0 (行の削除) だけは
    これと比較する (docstring 冒頭参照。今の main と比べると、PR 分岐後に main へ
    足された退役番号を「PR が消した」と誤検知する)。
    """
    violations: list[Violation] = []
    head = adr_numbers(head_paths)
    main = adr_numbers(main_paths)

    # 0. 退役一覧から行が消えていないか (基準は merge-base = PR が消したものだけ)
    for n in sorted(ancestor_retired - head_retired):
        violations.append(Violation("retired-removed", n))

    # 1. head 内での重複 (同じ番号のファイルが 2 つある)
    for n, files in sorted(head.items()):
        if len(files) > 1:
            violations.append(Violation("dup", n, head_paths=files))

    # 2. head の番号が今の main に**別名で**存在する (並行採番の衝突。
    #    同名なら「main に既にある同じ ADR」なので衝突ではない)
    for n, files in sorted(head.items()):
        base_files = main.get(n)
        if not base_files:
            continue
        strays = tuple(f for f in files if f not in base_files)
        if strays:
            violations.append(
                Violation("collision", n, head_paths=strays, base_paths=base_files)
            )

    # 3. 退役番号の再利用。照合は main と head の**和集合** (head 側だけだと
    #    「一覧から 1 行消してその番号を足す」で開き、main 側だけだと一覧を
    #    導入する PR 自身で効かない。削除そのものは検査 0 が別途赤にする)
    retired_all = main_retired | head_retired
    for n, files in sorted(head.items()):
        if n in retired_all:
            violations.append(Violation("retired-reuse", n, head_paths=files))

    return violations


def next_number(main_paths: list[str], main_retired: set[int]) -> int | None:
    """次に使える番号 = main の実ファイル + 退役番号の最大 +1。材料ゼロなら None。"""
    used = set(adr_numbers(main_paths)) | main_retired
    return (max(used) + 1) if used else None


def gate_conflicts(
    changed_files: list[tuple[str, str]],
    main_paths: list[str],
    main_retired: set[int],
) -> list[str]:
    """review-gate 用: PR の変更ファイル (filename, status) を今の main と照合する。

    ここが #381 の 2 段目 — CI の guard が緑を出した**後**に main へ同じ番号が
    着地しても、review-gate は pm-accept のたびに再評価されるため、マージ直前に
    ここで捕まる。判定は judge() と同一 (検査 0 だけは PR 側の退役一覧が
    変更ファイルからは取れないため対象外 — そちらは CI 側 guard が push 時に見る)。

    戻り値は Verdict.missing に載せる短文 (status description は 140 字で切れる)。
    """
    present = [
        f
        for f, status in changed_files
        if status != "removed" and adr_number_of(f) is not None
    ]
    if not present:
        return []
    # head_retired = ancestor_retired = main_retired: 検査 0 を無効化しつつ
    # (main - main = 空)、検査 3 の和集合を「今の main の退役一覧」にする
    violations = judge(present, main_paths, main_retired, main_retired, main_retired)
    messages: list[str] = []
    for v in violations:
        if v.kind == "dup":
            messages.append(f"ADR {v.number:04d} が PR 内で重複")
        elif v.kind == "collision":
            messages.append(f"ADR {v.number:04d} が今の main と衝突 ({v.base_paths[0]})")
        elif v.kind == "retired-reuse":
            messages.append(f"ADR {v.number:04d} は退役番号 (再利用不可)")
    return messages


class SnapshotError(RuntimeError):
    """main 側 ADR 一覧の読み取り失敗。呼び出し側は**合格扱いにしてはいけない**。"""


def load_main_snapshot(repo_root: str = ".") -> tuple[list[str], set[int]]:
    """作業ツリー (review-gate では常に main の checkout) から ADR 一覧と退役一覧を読む。

    読めないときは SnapshotError — 空リストを返して「衝突なし」に化けさせない。
    docs/adr に番号付き ADR が 1 本も見えない場合も同じ (main には常に数十本ある。
    見えない = cwd が repo root でない / checkout が壊れているのどちらかで、
    そのまま照合すると**全 PR が無条件で衝突なしになる**)。
    """
    root = Path(repo_root)
    adr_dir = root / "docs" / "adr"
    if not adr_dir.is_dir():
        raise SnapshotError("docs/adr が見えない (cwd が repo root でない?)")
    paths = sorted(f"docs/adr/{p.name}" for p in adr_dir.glob("*.md"))
    if not adr_numbers(paths):
        raise SnapshotError("docs/adr に番号付き ADR が 1 本も無い (checkout 異常?)")
    retired_path = root / RETIRED_FILE
    try:
        retired = parse_retired(retired_path.read_text(encoding="utf-8"))
    except OSError as exc:
        # main には退役一覧が常在する。読めない = 検査 3 が黙って素通りするので落とす
        raise SnapshotError(f"{RETIRED_FILE} を読めない ({exc.__class__.__name__})") from exc
    return paths, retired


# ---- ここから下は git との入出力 (テスト対象は上の純関数) ----


def _git(args: list[str]) -> str:
    """git を叩く。失敗は CalledProcessError のまま上へ — 取れなかったのに緑を出さない。"""
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
        check=True,
    )
    return proc.stdout


def _ref_adr_paths(ref: str) -> list[str]:
    listing = _git(["ls-tree", "-r", "--name-only", ref, "docs/adr/"])
    return [line for line in listing.splitlines() if line]


def _ref_retired(ref: str) -> set[int]:
    """ref の退役一覧。**ref にファイルが無い場合だけ**空集合 (一覧の導入前の
    履歴では正しく空)。存在確認と読み取りを分けているのは、`git show` の失敗を
    一括で握り潰すと「読めなかった」まで空 = 検査素通りに化けるため。
    「base にあったのに PR 側で消えた」は検査 0 が merge-base 基準で赤にする。"""
    exists = _git(["ls-tree", "--name-only", ref, "--", RETIRED_FILE]).strip()
    if not exists:
        return set()
    return parse_retired(_git(["show", f"{ref}:{RETIRED_FILE}"]))


@dataclass
class _Report:
    lines: list[str] = field(default_factory=list)

    def emit(self, text: str) -> None:
        self.lines.append(text)
        print(text)


def _annotate(report: _Report, v: Violation) -> None:
    n = f"{v.number:04d}"
    if v.kind == "retired-removed":
        report.emit(
            f"::error title=退役番号が一覧から消えている::{n} が {RETIRED_FILE} から"
            "削除されています。退役番号は再利用しません — 消す理由があるなら PO 裁定を取ってください"
        )
    elif v.kind == "dup":
        report.emit(f"::error title=ADR 番号の重複::番号 {n} の ADR が複数あります")
        for path in v.head_paths:
            report.emit(f"    {path}")
    elif v.kind == "collision":
        report.emit(
            f"::error file={v.head_paths[0]},title=ADR 番号が今の main と衝突::"
            f"番号 {n} は main に既に存在します (この PR を出した後に着地した可能性があります)"
        )
        for path in v.head_paths:
            report.emit(f"    PR:   {path}")
        for path in v.base_paths:
            report.emit(f"    main: {path}")
    elif v.kind == "retired-reuse":
        report.emit(
            f"::error file={v.head_paths[0]},title=退役した ADR 番号の再利用::"
            f"番号 {n} は docs/adr/archive/ へ退避済みです ({RETIRED_FILE})"
        )
        for path in v.head_paths:
            report.emit(f"    PR: {path}")


def _write_summary(main_sha: str, violations: list[Violation], nxt: int | None) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["## adr-number-guard", ""]
    lines.append(f"- 比較先: **今の origin/main** (`{main_sha[:7]}`) — run 時点で fetch (#381)")
    if nxt is not None:
        lines.append(f"- 次に使える番号: `{nxt:04d}` (main の実ファイル + 退役番号の最大 +1)")
    if violations:
        lines += [
            "",
            "🔴 **番号が衝突しています。** 採番は `ls docs/adr/` のローカル最大値ではなく、",
            "**origin/main の最大番号 +1** で取り直してください (README.md 参照)。",
            "ファイル名・本文の見出し・README の索引・参照している全ファイルを揃えて直すこと。",
        ]
    else:
        lines += ["", "🟢 衝突なし"]
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", default="HEAD", help="判定対象 (PR head の sha/ref)")
    parser.add_argument(
        "--base-ref",
        default=None,
        help="比較先 ref (省略時: origin/main を fetch してその tip と比較する)",
    )
    args = parser.parse_args(argv)

    if args.base_ref is None:
        # #381 の本体: イベント時点の base.sha ではなく**今の** main を取りに行く。
        # fetch が失敗したら CalledProcessError で run が赤になる (古い ref で
        # 判定して緑を出すよりよい)
        _git(["fetch", "--no-tags", "--quiet", "origin", "main"])
        base_ref = "FETCH_HEAD"
    else:
        base_ref = args.base_ref
    main_sha = _git(["rev-parse", base_ref]).strip()
    merge_base = _git(["merge-base", args.head, main_sha]).strip()

    head_paths = _ref_adr_paths(args.head)
    main_paths = _ref_adr_paths(main_sha)
    main_retired = _ref_retired(main_sha)
    violations = judge(
        head_paths=head_paths,
        main_paths=main_paths,
        head_retired=_ref_retired(args.head),
        main_retired=main_retired,
        ancestor_retired=_ref_retired(merge_base),
    )

    report = _Report()
    for v in violations:
        _annotate(report, v)
    _write_summary(main_sha, violations, next_number(main_paths, main_retired))

    if violations:
        return 1
    print("::notice title=adr-number-guard::ADR 番号の衝突なし (今の main と照合)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
