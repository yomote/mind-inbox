#!/usr/bin/env python3
"""subagent が未コミットの差分を作業ツリーに置き去りにしたまま終わるのを止める hook。

これが無いと何が静かに通るか:
    subagent が commit / push せずに終わると、**親からは「作業が終わった」としか
    見えない**まま差分だけが残る。置き去りの行き先は起動の仕方で 2 通りある:

    - **親と作業ツリーを共有する起動** (2026-08-13 実測: SubagentStop の `cwd` が
      親と同一で、subagent の書き込みが親の作業ツリーにそのまま出た) — 次に親が
      別の作業を始めると、そのコミットに他人の差分が紛れるか、誰のものか
      分からない差分として捨てられる。実際に 2026-08-13 に起きている
    - **`isolation: "worktree"` の起動** (下の実測を参照) — 差分は subagent 専用の
      worktree に残り、**親にも GitHub にも届かないまま誰にも読まれずに消える**

    どちらも GitHub 側には何の痕跡も残らないので CI では検出できない。

2 つのイベントで動く (settings.json で両方に登録している):
    PreToolUse (Agent/Task) — subagent を起こす直前の dirty な path と**その指紋**を控える
    SubagentStop            — 今の dirty と突き合わせ、**subagent が増やした分**だけ咎める

`isolation: "worktree"` の起動 (このリポジトリの既定) の扱い:
    実測 (2026-08-14 / Claude Code 2.1.231。親を dirty にしたラボ repo で計測):

        PreToolUse   cwd=/lab/repo                                      agent_id 無し
                     tool_input.isolation="worktree"
        SubagentStop cwd=/lab/repo/.claude/worktrees/agent-adebf040d4f4a3ab7
                     agent_id="adebf040d4f4a3ab7"

    **控えは親の cwd で積まれ、停止では worktree の cwd が渡る**ので cwd が一致しない。
    ここで控えを列に残すと、worktree subagent を使うたびに親側の控えが 1 件ずつ溜まり、
    後続の親 cwd 起動が古い控え (無関係な親の変更を含む) を基準に引いて咎め続け、
    64 件で新しい控えが保存されなくなる = **この hook が実質死ぬ**。
    そこで停止側で worktree 起動を機械判定し (`worktree_parent`)、親 cwd の控えを
    1 件回収する (`drop_worktree_entry`)。

    worktree の基準は**空**にする。同じ実測で、**親が dirty (`M README.md` /
    `?? parent-dirty.txt`) でも worktree 起動直後の `git status --porcelain` は空**
    だった (worktree は親の未コミット差分を引き継がない)。したがって停止時に worktree に
    残っている dirty は**全部その subagent のもの**で、`all` (混ざっているかもしれない) より
    強い判定ができる。

控えを path だけでなく指紋 (内容ハッシュ) で持つ理由:
    親が既に触っているファイルを subagent がさらに編集した場合、path 集合は
    起動前後で同じなので差集合が空になり、**置き去りを無警告で見逃す**。
    指紋が変わった path も咎めることでこの穴を塞ぐ (`fingerprint` を参照)。

指紋と排他で**残る**穴 (取れていないものを「異常なし」と書かないための明示):
    - 5MiB 超のファイルは内容ハッシュではなく `size:mtime_ns` — サイズと mtime が
      両方とも元に戻る書き換えは見えない
    - 未追跡ディレクトリの指紋は配下 500 件まで — 501 件目以降だけの変化は見えない
    - `fcntl` が無い環境 (Windows) では控えの排他が素通しになり、控えが 1 件消える
      (消えた側は "all" モードに落ちるので、文言には出る)

控えが取れている場合と取れていない場合を**必ず区別して出す**。控えが無いときに
作業ツリー全体を subagent のせいにすると、親自身の未コミット差分で subagent を
止めることになる (subagent には直しようがない)。

並列起動 (release-gate が 4 個同時に起こす) の扱い:
    控えを 1 セッション 1 個の上書きにすると、後から起きた subagent の控えに
    先行 subagent の差分が写り込み、**先行 subagent の置き去りを見落とす**。
    そこで控えは**起動ごとに 1 件ずつ積む列**にし、停止時は同じ cwd の**最も古い
    控え**を基準に取り出す。PreToolUse(Agent) には `agent_id` が入らない (2026-08-13 実測)
    ため、どの控えがどの subagent のものかは原理的に対応付けられない — 最古を採ると
    他の subagent の差分まで自分のものとして報告されうるが (過剰報告)、**見落としは
    起きない**。過剰であることは文言に出す。

無限ループ対策: `stop_hook_active` が true のときはブロックしない (実測で存在を確認)。

**この hook の生死は `watchers.json` (status ページ) では見ていない。**
hook は Claude Code セッションの中だけで動き、GitHub 側に run を 1 つも残さないので、
status ページに行を足しても「動いた形跡が無い」と「動いていない」が区別できず、
**取れていないものが緑で表示される** (CLAUDE.md 最優先の禁止事項に反する)。代わりに
`test_hook_wiring.py` が配線を検査し、`npm run test:scripts` → `npm run test:fast` →
CI job `test (L0 / L1+L2 / L3 / L3-real)` (required status check) で走る。
**配線が壊れるとマージが止まる。**
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_io  # noqa: E402

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows。このリポジトリの実行環境には無い
    # 見えなくなるもの: 控えの読み書きの排他。並列 hook が重なると後勝ちで控えが
    # 1 件消え、その subagent は "all" モード (作業ツリー全体) に落ちる。
    fcntl = None  # type: ignore[assignment]

_GIT_TIMEOUT_SEC = 10

# 積める控えの上限。超えたら**新しい方を捨てる** (古い方を捨てると、その subagent の
# 基準が新しい控えに化けて置き去りを見落とす)。捨てた分は控えが無い扱い = "all" に
# 落ちて文言に出るので、黙って精度が下がることはない。
_MAX_ENTRIES = 64

# 内容ハッシュを取る上限。超えたファイルは (サイズ, mtime) を指紋にする。
# 見えなくなるもの: 5MiB 超のファイルを、サイズと mtime が両方とも元に戻る形で
# 書き換えた場合 (実運用で出ない)。
_FINGERPRINT_MAX_BYTES = 5 * 1024 * 1024

# 未追跡ディレクトリ (`?? dir/`) の指紋を作るときに見る最大エントリ数。
# 見えなくなるもの: この数を超えるディレクトリの、501 件目以降だけの変化。
_DIR_ENTRY_LIMIT = 500


def snapshot_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:64] or "unknown"
    return Path(tempfile.gettempdir()) / f"claude-hooks-dirty-{safe}.json"


@contextlib.contextmanager
def locked(target: Path) -> Iterator[None]:
    """控えの read-modify-write を直列化する。

    これが無いと何が静かに通るか:
        並列起動 (release-gate は subagent を同時に起こす) では PreToolUse の hook が
        別プロセスとして同時に走る。排他なしだと双方が同じ旧列を読んで後勝ちで書き、
        **控えが 1 件消える**。消えた側の subagent は基準を失って "all" に落ちるか、
        他人の控えを最古として引く。`take_baseline` の「最古を採る」保証も、
        列そのものが壊れると成立しない。

    ロックは控え本体とは別ファイルに取る (控えは消費時に unlink するため、
    同じ inode に取ると次の待ち手が消えた inode をロックして排他にならない)。
    fcntl が無い環境では素通しする (上の import を参照 — 何が見えなくなるかも同じ)。
    """
    if fcntl is None:
        yield
        return
    lock_path = target.with_suffix(target.suffix + ".lock")
    handle_ = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle_ = lock_path.open("a+")
        fcntl.flock(handle_.fileno(), fcntl.LOCK_EX)
    except OSError:
        # ロックが取れなくても hook は動かす (止めると分配そのものが止まる)。
        # 見えなくなるもの: この 1 回の read-modify-write の排他。
        if handle_ is not None:
            handle_.close()
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle_.fileno(), fcntl.LOCK_UN)
        handle_.close()


def parse_porcelain(text: str) -> set[str]:
    """`git status --porcelain -z` の出力から path 集合を作る。

    **`-z` を使う理由** (これが無いと何が静かに通るか):
        既定の porcelain は非 ASCII や特殊文字を含む path を C 形式で引用する
        (`日本語.txt` → `"\\346\\227\\245..."`)。外側の引用符を外すだけでは
        **実在しない path** になり、`fingerprint()` が起動前後とも "missing" を返す。
        指紋が一致するので、そのファイルへの subagent の編集は**無警告で通る**。
        `-z` なら引用されず、path に改行が含まれていても取りこぼさない。

    形式: `XY <path>\\0` の並び。rename / copy (`R` / `C`) だけは
    `XY <new>\\0<old>\\0` と 2 フィールドで来るので、new を採って old は読み飛ばす。
    """
    fields = text.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1  # 直後の old path を読み飛ばす
        if path:
            paths.add(path)
    return paths


def fingerprint(root: str, rel: str) -> str:
    """dirty な 1 件の指紋。**内容が変われば必ず変わる**ことだけを満たせばよい。

    これが無いと何が静かに通るか:
        親が既に触っているファイルを subagent がさらに編集して未コミットのまま
        終わると、path 集合は起動前後で同じ (`{"foo.py"}`) なので差集合が空になり、
        **置き去りが無警告で通る**。パスだけでは同一ファイル内の追加変更を識別できない。
    """
    path = Path(root) / rel
    try:
        st = path.stat()
    except OSError:
        return "missing"  # 削除された / stat できない (復活すれば指紋が変わる)
    if path.is_dir():
        # `?? dir/` (未追跡ディレクトリ) は中身が増えても porcelain の 1 行のまま。
        # 中身の (相対パス, サイズ, mtime) を畳んで指紋にする (read はしない)。
        digest = hashlib.sha256()
        try:
            entries = sorted(p for p in path.rglob("*") if p.is_file())
        except OSError:
            return "unreadable"
        for child in entries[:_DIR_ENTRY_LIMIT]:
            try:
                cst = child.stat()
            except OSError:
                continue
            digest.update(f"{child.relative_to(path)}:{cst.st_size}:{cst.st_mtime_ns}\n".encode())
        truncated = "+" if len(entries) > _DIR_ENTRY_LIMIT else ""
        return f"dir:{digest.hexdigest()}{truncated}"
    if st.st_size > _FINGERPRINT_MAX_BYTES:
        return f"stat:{st.st_size}:{st.st_mtime_ns}"
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def git_dirty_paths(cwd: str) -> tuple[dict[str, str], str | None]:
    """({dirty な path: 指紋}, 失敗理由)。失敗理由が非 None なら**検査していない**。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-z"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,  # check=False にした分、非ゼロ終了は下で明示的に理由へ変換する
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, f"git status を実行できなかった ({exc!r})"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return {}, f"git status が失敗した ({detail[0] if detail else proc.returncode})"
    return {rel: fingerprint(cwd, rel) for rel in parse_porcelain(proc.stdout)}, None


def select_paths_to_report(
    now: dict[str, str], snapshot: dict[str, str | None] | None
) -> list[str]:
    """咎める path。控えが無い (None) なら作業ツリー全体。

    咎めるのは **(a) 起動前に dirty でなかった path** と
    **(b) 起動前から dirty だったが指紋が変わった path**。(b) が無いと、
    親が既に触っているファイルへの subagent の追記が丸ごと見えない。

    控えの指紋が None のとき (旧形式の控え = path しか持たない) は (b) を判定できない
    ので path の増分だけを見る。**指紋が無いことを「変わっていない」と読み替えない**
    — 判定できないものを判定したことにしない、が優先。

    根拠の強さ ("diff" / "diff-concurrent" / "all") は take_baseline が決める —
    **呼ぶ側はそれを必ず文言に出すこと** (精度を偽らないため)。
    """
    if snapshot is None:
        return sorted(now)
    report = []
    for path, print_ in sorted(now.items()):
        if path not in snapshot:
            report.append(path)
            continue
        before = snapshot[path]
        if before is not None and before != print_:
            report.append(path)
    return report


def load_entries(path: Path) -> list[dict[str, Any]]:
    """控えの列を読む。読めない / 壊れているときは空リスト (= 控え無し扱い)。

    見えなくなるもの: 「控えの読み取りに失敗した」と「そもそも控えが無い」の区別。
    どちらも "all" に落ちて文言に出るので、黙って精度を偽ることはない。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):  # 旧形式 (1 セッション 1 控え) を 1 件の列として読む
        data = [data]
    if not isinstance(data, list):
        return []
    return [
        e
        for e in data
        if isinstance(e, dict) and (isinstance(e.get("prints"), dict) or isinstance(e.get("paths"), list))
    ]


def entry_prints(entry: dict[str, Any]) -> dict[str, str | None]:
    """控え 1 件を {path: 指紋} にする。指紋を持たない旧形式は値が None になる。"""
    prints = entry.get("prints")
    if isinstance(prints, dict):
        return {p: v for p, v in prints.items() if isinstance(p, str) and isinstance(v, str)}
    return {p: None for p in entry.get("paths", []) if isinstance(p, str)}


def take_baseline(
    entries: list[dict[str, Any]], cwd: str
) -> tuple[dict[str, str | None] | None, list[dict[str, Any]], str]:
    """今停止した subagent の基準を 1 件取り出す。(基準, 残りの列, 根拠) を返す。

    同じ cwd の控えのうち**最も古いもの**を基準にする。並列起動では控えと subagent を
    対応付けられない (PreToolUse(Agent) に agent_id が無い) ので、最古を採って
    **見落としを起こさない**側に倒す。cwd が違う控えは使わない
    (worktree 分離された subagent に親の控えを流用しないため)。

    **落とすのは最古ではなく最も新しい控え** (これが無いと何が静かに通るか):
        A 起動 → A が a.ts を変更 → B 起動 (B の控えには a.ts が入る) → **B が先に停止**、
        という順で最古を消費すると、後から止まる A には**a.ts を既に含む B の控え**が
        当たる。A の置き去りは指紋まで一致するので無警告で通る。最古を最後の停止まで
        残せば、並列グループの全員が同じ基準 (最初の起動前) と突き合わせるので、
        **停止の順序に依存しなくなる**。

    残した最古の控えには `concurrent` の印を付ける。これが無いと最後の停止が
    `mode="diff"` になり、他の subagent の差分が混ざっているのに
    「あなたが作った差分です」と**精度を偽る**。
    """
    matched = [i for i, entry in enumerate(entries) if entry.get("cwd") == cwd]
    if not matched:
        return None, entries, "all"
    oldest = matched[0]
    baseline = entry_prints(entries[oldest])
    concurrent = len(matched) > 1 or bool(entries[oldest].get("concurrent"))
    drop = matched[-1]
    remaining = [entry for i, entry in enumerate(entries) if i != drop]
    if concurrent and drop != oldest:
        keep = oldest if drop > oldest else oldest - 1
        remaining[keep] = {**remaining[keep], "concurrent": True}
    return baseline, remaining, "diff-concurrent" if concurrent else "diff"


def worktree_parent(cwd: str, agent_id: Any) -> str | None:
    """worktree 起動の subagent の停止なら**親の cwd** を返す。違えば None。

    判定は cwd の形だけで行う。実測 (2026-08-14) で worktree の場所は
    `<親の cwd>/.claude/worktrees/agent-<agent_id>` に固定されており、末尾の
    `agent_id` が**同じ stdin に入っている値と一致すること**まで見るので、
    たまたま似た名前のディレクトリを worktree と誤認しない。

    これが無いと何が静かに通るか:
        停止側が「cwd が違う控え」を単に無視するだけだと、親側の控えが
        回収されずに溜まり続ける (モジュール docstring の実測を参照)。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return None
    path = Path(cwd)
    if path.name != f"agent-{agent_id}":
        return None
    if path.parent.name != "worktrees" or path.parent.parent.name != ".claude":
        return None
    return str(path.parent.parent.parent)


def drop_worktree_entry(entries: list[dict[str, Any]], parent_cwd: str) -> list[dict[str, Any]]:
    """worktree 起動の停止に対応する控えを、親 cwd の列から 1 件回収する。

    落とすのは **`isolation` が `worktree` と記録された控え**を優先する
    (`_record_snapshot` が PreToolUse の `tool_input.isolation` を写している)。
    印のある控えは基準として誰にも使われないので、落としても他の subagent の
    判定は変わらない。

    印が 1 件も無いときは同じ親 cwd の**最も新しい**控えを落とす。控えに印が付くのは
    この修正以降なので、印を読めない形 (旧形式の控え / `tool_input` が渡らない起動) でも
    **必ず 1 件回収して蓄積を止める**ための保険。その代わり親 cwd を共有する別の
    subagent の基準を 1 件奪うので、残った控えに `concurrent` を立てて
    「他人の差分が混ざりうる」と申告する (奪われた側は基準を失って `all` に落ちる —
    どちらも過剰報告側で、置き去りの見落としにはならない)。
    """
    matched = [i for i, entry in enumerate(entries) if entry.get("cwd") == parent_cwd]
    if not matched:
        return entries
    marked = [i for i in matched if entries[i].get("isolation") == "worktree"]
    drop = (marked or matched)[-1]
    remaining = [entry for i, entry in enumerate(entries) if i != drop]
    if not marked:
        for i, entry in enumerate(remaining):
            if entry.get("cwd") == parent_cwd:
                remaining[i] = {**entry, "concurrent": True}
                break
    return remaining


def format_reason(paths: list[str], mode: str) -> str:
    shown = paths[:20]
    listed = "\n".join(f"  {p}" for p in shown)
    if len(paths) > len(shown):
        listed += f"\n  ... 他 {len(paths) - len(shown)} 件"
    if mode == "worktree":
        # worktree は起動時に空 (親の未コミット差分を引き継がない) — 実測はモジュール
        # docstring。だから「作業ツリー全体」でも混入の可能性が無く、断定できる。
        return (
            f"未コミットの変更が {len(paths)} 件、あなたの worktree に残ったまま"
            "終わろうとしています。\n"
            f"{listed}\n"
            "この worktree は起動時に空だったので、**全部あなたが作った差分です**。\n"
            "worktree は親の作業ツリーとは別なので、ここで置き去りにすると"
            "この差分は親にも GitHub にも届かず、誰にも読まれないまま消えます。\n"
            "commit と push まで完遂してから終了してください。"
            "意図的に残すなら、何をなぜ残したかを最終回答に書いてください。"
        )
    if mode == "diff":
        basis = "これは subagent の起動前後の差分なので、あなたが作った差分です。"
    elif mode == "diff-concurrent":
        basis = (
            "これは**最初の subagent が起動する前**との差分です。並行して動いている "
            "subagent があるため、他の subagent が作った分が混ざっている可能性があります "
            "(あなたが作っていないものは、そう述べて先に進んでください)。"
        )
    else:
        basis = (
            "起動前の控えが取れていないため、これは**作業ツリー全体**の未コミット差分です。"
            "あなたが作っていないものが混ざっている可能性があります "
            "(その場合はそう述べて先に進んでください)。"
        )
    return (
        f"未コミットの変更が {len(paths)} 件、作業ツリーに残ったまま終わろうとしています。\n"
        f"{listed}\n"
        f"{basis}\n"
        "subagent は親と作業ツリーを共有します。ここで置き去りにすると、"
        "この差分の始末は親セッションに回り、誰の変更か分からないまま捨てられます。\n"
        "commit と push まで完遂してから終了してください。"
        "意図的に残すなら、何をなぜ残したかを最終回答に書いてください。"
    )


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    hook_event = event.get("hook_event_name")
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return hook_io.passthrough("cwd が渡らず、未コミット差分を検査していない")
    session_id = str(event.get("session_id") or "unknown")

    if hook_event == "PreToolUse":
        return _record_snapshot(event, cwd, session_id)
    if hook_event == "SubagentStop":
        return _check(event, cwd, session_id)
    return None


def _record_snapshot(event: dict[str, Any], cwd: str, session_id: str) -> dict[str, Any] | None:
    if event.get("tool_name") not in ("Agent", "Task"):
        return None
    prints, failure = git_dirty_paths(cwd)
    if failure is not None:
        # 控えが取れなくても subagent の起動は止めない。取れなかった事実は
        # SubagentStop 側が "all" モードとして文言に出す。
        return None
    target = snapshot_path(session_id)
    # 読んで足して書くまでを 1 つのロックの中でやる。並列起動でここが割り込まれると
    # 後勝ちで控えが 1 件消える (locked の docstring 参照)。
    with locked(target):
        entries = load_entries(target)
        if len(entries) >= _MAX_ENTRIES:
            # 新しい方を捨てる (理由は _MAX_ENTRIES のコメント)。この subagent は "all" に落ちる。
            return None
        entry: dict[str, Any] = {"cwd": cwd, "prints": dict(sorted(prints.items()))}
        tool_input = event.get("tool_input")
        if isinstance(tool_input, dict) and tool_input.get("isolation") == "worktree":
            # 停止側で回収するときの目印 (drop_worktree_entry)。停止では worktree の
            # cwd しか来ないので、控えの側に「worktree 起動だった」と書いておく。
            entry["isolation"] = "worktree"
        entries.append(entry)
        try:
            target.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        except OSError:
            # 書けなくても起動は止めない。結果は "all" モードに落ちて文言に出る。
            return None
    return None


def _check(event: dict[str, Any], cwd: str, session_id: str) -> dict[str, Any] | None:
    if event.get("stop_hook_active"):
        # 一度差し戻した後の再停止。ここで再びブロックすると無限ループになる。
        return None

    now, failure = git_dirty_paths(cwd)
    if failure is not None:
        return hook_io.passthrough(f"{failure} ため、未コミット差分を検査していない")

    target = snapshot_path(session_id)
    parent = worktree_parent(cwd, event.get("agent_id"))
    # 取り出しと書き戻しの間に別の停止が割り込むと、同じ控えを 2 つの subagent が引く。
    with locked(target):
        entries = load_entries(target)
        if parent is None:
            baseline, remaining, mode = take_baseline(entries, cwd)
        else:
            # worktree 起動: 基準は空 (起動時の worktree は空) で、控えは親 cwd の列から回収する。
            baseline, remaining, mode = {}, drop_worktree_entry(entries, parent), "worktree"
        _consume(target, remaining)
    paths = select_paths_to_report(now, baseline)
    if not paths:
        return None
    return hook_io.block(format_reason(paths, mode))


def _consume(target: Path, remaining: list[dict[str, Any]]) -> None:
    """取り出した控えを列から落とす。並列起動で同じ控えを 2 回使わないため。

    書き戻しに失敗しても停止判定は済んでいるので続行する。見えなくなるもの:
    次の SubagentStop が同じ控えを再利用する (= 過剰報告側にずれる) 可能性。
    """
    try:
        if remaining:
            target.write_text(json.dumps(remaining, ensure_ascii=False), encoding="utf-8")
        else:
            target.unlink(missing_ok=True)
    except OSError:
        return


if __name__ == "__main__":
    raise SystemExit(hook_io.run(handle))
