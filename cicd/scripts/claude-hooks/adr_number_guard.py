#!/usr/bin/env python3
"""ADR を書き始める瞬間に採番の衝突を止める PreToolUse hook。

これが無いと何が静かに通るか:
    採番の規約 (`origin/main` の最大番号 +1 / 退役番号は再利用しない) は
    **記述だけでは 2 回破られている** (0015→0019 / 0026→0027 の衝突)。CI の
    `adr-number-guard` は落としてくれるが、気づくのは **ADR を書き切って PR を
    出した後**で、そこから番号を振り直すとファイル名・索引・本文中の相互参照を
    全部直すことになる。この hook はそれを「1 文字も書く前」に前倒しする。

**CI の置き換えではない。** hooks は Claude Code セッションの中でしか発火せず、
Codex も Actions も PO の直接編集も通らない。`.github/workflows/adr-number-guard.yml`
が門で、こちらは前倒しの警告にすぎない (Issue #392 の決定済み前提)。

判定は「未使用か」ではなく「**規約どおりの番号か**」で見る。規約は
「`origin/main` の最大番号 +1、欠番は埋めない」なので、未使用でも欠番 (0011) や
飛び番 (9999) は違反。CI の `adr-number-guard.yml` は**衝突しか見ていない**ため、
欠番・飛び番はそこも通り抜ける — つまりこの検査を緩めると誰も止めない。

費用: `docs/adr/NNNN-*.md` の**新規作成のときだけ** git を叩く。それ以外は即 return。
ネットワークは使わない (ローカルの `origin/main` ref を見るだけで fetch しない) ため、
ref が古ければ古いまま判定する — その限界は CI 側が塞ぐ。

**この hook の生死は `watchers.json` (status ページ) では見ていない。**
hook は Claude Code セッションの中だけで動き、GitHub 側に run を 1 つも残さないので、
status ページに行を足しても「動いた形跡が無い」と「動いていない」が区別できず、
**取れていないものが緑で表示される** (CLAUDE.md 最優先の禁止事項に反する)。代わりに
`test_hook_wiring.py` が配線を検査し、`npm run test:scripts` → `npm run test:fast` →
CI job `test (L0 / L1+L2 / L3 / L3-real)` (required status check) で走る。
**配線が壊れるとマージが止まる。**
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_io  # noqa: E402

_GIT_TIMEOUT_SEC = 10
_ADR_PATH = re.compile(r"^docs/adr/(\d{4})-[^/]*\.md$")
_ADR_ANY = re.compile(r"docs/adr/(\d{4})-")
_RETIRED_LINE = re.compile(r"^\s*(\d{4})\s*$")
RETIRED_FILE = "docs/adr/archive/retired-numbers.txt"


def adr_number_of(repo_relative_path: str) -> int | None:
    """`docs/adr/NNNN-*.md` なら番号。archive 配下や template.md は None。"""
    match = _ADR_PATH.match(repo_relative_path)
    return int(match.group(1)) if match else None


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


def numbers_in_names(names: list[str]) -> set[int]:
    return {int(m.group(1)) for name in names if (m := _ADR_ANY.search(name))}


def next_free(used: set[int]) -> int:
    """採番規約: 最大番号 +1。欠番は埋めない。"""
    return (max(used) + 1) if used else 1


def _git(args: list[str], cwd: str) -> tuple[str, str | None]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,  # check=False の分、非ゼロ終了は理由に変換して呼び元へ返す
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"git {args[0]} を実行できなかった ({exc!r})"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return "", f"git {' '.join(args)} が失敗した ({detail[0] if detail else proc.returncode})"
    return proc.stdout, None


def collect_used(repo_root: Path, cwd: str) -> tuple[set[int], str | None]:
    """使用済み番号 = origin/main の実ファイル + 作業ツリーの実ファイル + 退役一覧。

    origin/main が引けないときは理由を返す。**その場合に「衝突なし」と答えてはいけない**
    (作業ツリーだけを見ると、並行セッションが取った番号を見落とす = 衝突の再来)。
    """
    listing, failure = _git(["ls-tree", "-r", "--name-only", "origin/main", "docs/adr/"], cwd)
    if failure is not None:
        return set(), failure
    used = numbers_in_names(listing.splitlines())

    adr_dir = repo_root / "docs" / "adr"
    if adr_dir.is_dir():
        # numbers_in_names は `docs/adr/NNNN-` の形で照合するので、
        # ファイル名だけを渡すと**一致せず、作業ツリーの ADR が丸ごと漏れる**
        used |= numbers_in_names([f"docs/adr/{p.name}" for p in adr_dir.glob("*.md")])

    retired_text: str | None = None
    local_retired = repo_root / RETIRED_FILE
    try:
        retired_text = local_retired.read_text(encoding="utf-8")
    except OSError:
        blob, blob_failure = _git(["show", f"origin/main:{RETIRED_FILE}"], cwd)
        if blob_failure is not None:
            return used, f"{RETIRED_FILE} を作業ツリーからも origin/main からも読めなかった"
        retired_text = blob
    used |= parse_retired(retired_text)
    return used, None


def format_reason(number: int, free: int, used: set[int] | None = None) -> str:
    if used is not None and number not in used:
        head = (
            f"ADR 番号 {number:04d} は未使用ですが、採番規約に合いません "
            f"({'欠番を埋めようとしています' if number < free else '番号を飛ばしています'})。\n"
        )
    else:
        head = (
            f"ADR 番号 {number:04d} は既に使われています "
            f"(origin/main の実ファイル / 作業ツリー / {RETIRED_FILE} のいずれか)。\n"
        )
    # 根拠 (最大番号がどこから来たか) を必ず出す。2026-08-13 の実測で、根拠の無い
    # 「次は 0008」を **エージェントが信用せず従わなかった** (作業ツリーには 0003 までしか
    # 見えないため。退役番号を合算していることが伝わらない)。
    if used:
        basis = (
            f"使用済みの最大番号は {max(used):04d} です "
            f"(origin/main の実ファイル + 作業ツリー + {RETIRED_FILE} の退役番号を合算。"
            "退役番号はファイル名から消えているので `ls docs/adr` では見えません)。"
        )
    else:
        basis = "使用済みの番号は 1 つも見つかりませんでした。"
    return (
        f"{head}"
        f"採番規約は「origin/main の最大番号 +1、欠番は埋めない」。{basis}"
        f"よって次の番号は **{free:04d}** です。\n"
        f"この Write は実行しておらず、ファイルは作られていません。"
        f"ファイル名が {free:04d} であればこの検査は通ります。"
    )


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("tool_name") != "Write":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or "docs/adr/" not in file_path:
        return None

    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None

    root_out, failure = _git(["rev-parse", "--show-toplevel"], cwd)
    if failure is not None:
        return None  # git 管理下でない場所での Write は ADR ではない
    repo_root = Path(root_out.strip())

    try:
        relative = Path(file_path).resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None

    number = adr_number_of(relative)
    if number is None:
        return None
    if (repo_root / relative).exists():
        return None  # 既存 ADR の上書き。採番の話ではない

    used, collect_failure = collect_used(repo_root, cwd)
    if collect_failure is not None:
        return hook_io.passthrough(f"{collect_failure} ため、ADR 採番を照合していない")

    free = next_free(used)
    if number == free:
        return None
    # 未使用でも「最大 +1」でなければ規約違反 (欠番埋め / 飛び番)。CI は衝突しか
    # 見ていないので、ここを「未使用なら通す」に緩めると誰も止めなくなる。
    return hook_io.deny_pre_tool_use(format_reason(number, free, used))


if __name__ == "__main__":
    raise SystemExit(hook_io.run(handle))
