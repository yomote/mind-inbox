"""[単体] subagent の置き去り差分の検出 (Issue #392 案 B)。

無いと何が静かに通るか:
    - **親自身の未コミット差分で subagent をブロックする** — subagent には直しようが
      なく、分配そのものが壊れる (Issue #392 が最重要と名指しした事故の B 版)
    - 控えが無いときに「subagent が作った差分」と偽って報告する — 精度を偽ると
      次からその文言が信用されなくなる
    - **並列起動で先に停止した subagent の置き去りを見落とす** — 後発の控えに
      先行 subagent の差分が写り込むと、その差分が「起動前からあったもの」に化ける
    - `stop_hook_active` を見落として無限に差し戻す
    - `git status` が失敗したときに「未コミット差分なし」と答える
      (取れなかったものを「異常なし」と書く — このリポジトリ最優先の禁止事項)
    - **起動前にあった差分が消えたことを見落とす** — 共有 worktree の subagent が親の
      未コミット作業を `git restore` / `git reset --hard` で捨てても、path が `now` から
      居なくなるので 1 件も咎めない。restore/reset で消した内容はもう戻せない
    - **`isolation: "worktree"` の停止で控えを回収せず、親側の列に溜め続ける** —
      このリポジトリの subagent は既定で worktree 起動なので、使うたびに 1 件ずつ増え、
      後続の親 cwd 起動が古い控えを基準に引いて無関係な親の変更を咎め続けたあと、
      64 件で新しい控えが保存されなくなる (hook が実質死ぬ)
"""

import hashlib
import json
from pathlib import Path

from subagent_dirty_guard import (
    drop_worktree_entry,
    fingerprint,
    git_dirty_paths,
    git_index_prints,
    format_reason,
    handle,
    load_entries,
    parse_ls_files_stage,
    parse_porcelain,
    select_paths_to_report,
    select_paths_vanished,
    snapshot_path,
    take_baseline,
    worktree_parent,
)

HOOK_DIR = Path(__file__).resolve().parent


def test_単体_porcelain_を解釈する() -> None:
    # `--porcelain -z`: NUL 区切り / rename は <new>\0<old>\0 の 2 フィールド
    text = " M apps/bff/src/a.ts\0?? new.txt\0R  new.md\0old.md\0A  docs/x.md\0"
    assert parse_porcelain(text) == {
        "apps/bff/src/a.ts": " M",
        "new.txt": "??",
        "new.md": "R ",
        "docs/x.md": "A ",
    }


def test_単体_porcelain_は_XY_状態を保持する() -> None:
    # 内容が同じでもステージ状態だけが変わる (` M foo` → `M  foo`)。XY を捨てると
    # 指紋が一致し、親の差分が勝手にステージされた状態を見逃す。
    assert parse_porcelain(" M foo\0")["foo"] != parse_porcelain("M  foo\0")["foo"]


def test_単体_非_ASCII_の_path_を引用のまま取り込まない() -> None:
    # 既定の porcelain は `"\346\227..."` と C 形式で引用する。引用符を外すだけだと
    # 実在しない path になり、指紋が両側とも "missing" になって変更を見逃す。
    # `-z` は引用しないので、そのまま実在する path として読めること。
    assert set(parse_porcelain(" M 日本語.txt\0?? 改\n行.txt\0")) == {"日本語.txt", "改\n行.txt"}


def test_単体_控えがあれば_subagent_が増やした分だけ咎める() -> None:
    before = {"parent-dirty.ts": "sha256:aaa"}
    now = {"parent-dirty.ts": "sha256:aaa", "subagent-made.ts": "sha256:bbb"}
    assert select_paths_to_report(now, before) == ["subagent-made.ts"]
    # 親の差分だけが残っている場合は 1 件も咎めない
    assert select_paths_to_report(before, before) == []


def test_単体_dirty_済みファイルへの追記も咎める() -> None:
    # 親が既に触っている foo.py を subagent がさらに編集して未コミットで終わる形。
    # path 集合は起動前後で同じなので、指紋を見ないと差集合が空になって素通しする。
    before = {"foo.py": "sha256:aaa"}
    now = {"foo.py": "sha256:bbb"}
    assert select_paths_to_report(now, before) == ["foo.py"]


def test_単体_指紋を持たない旧形式の控えでは内容変化を判定しない() -> None:
    # 判定できないものを「変わっていない」と読み替えない。path の増分だけを見る。
    before = {"foo.py": None}
    assert select_paths_to_report({"foo.py": "sha256:bbb"}, before) == []
    assert select_paths_to_report({"foo.py": "sha256:bbb", "new.ts": "sha256:c"}, before) == [
        "new.ts"
    ]


def test_単体_控えが無いときは全体を出す() -> None:
    assert select_paths_to_report({"a.ts": "sha256:a", "b.ts": "sha256:b"}, None) == [
        "a.ts",
        "b.ts",
    ]


def test_単体_指紋は内容が変われば変わり_変わらなければ同じ(tmp_path) -> None:
    target = tmp_path / "a.ts"
    target.write_text("x", encoding="utf-8")
    first = fingerprint(str(tmp_path), "a.ts")
    assert first == fingerprint(str(tmp_path), "a.ts"), "同じ内容で指紋が揺れると毎回誤検知する"
    target.write_text("y", encoding="utf-8")
    assert fingerprint(str(tmp_path), "a.ts") != first
    assert fingerprint(str(tmp_path), "無い.ts") == "missing"


def test_単体_未追跡ディレクトリは中身が増えれば指紋が変わる(tmp_path) -> None:
    # `?? dir/` は中にファイルが増えても porcelain の行は 1 本のまま。
    # ディレクトリを 1 個の path として扱うだけだと、中身の追加を見逃す。
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.ts").write_text("x", encoding="utf-8")
    before = fingerprint(str(tmp_path), "d")
    (tmp_path / "d" / "b.ts").write_text("y", encoding="utf-8")
    assert fingerprint(str(tmp_path), "d") != before


def test_単体_文言が根拠の強さを言い分ける() -> None:
    assert "あなたが作った差分です" in format_reason(["a.ts"], "diff")
    reason_all = format_reason(["a.ts"], "all")
    assert "混ざっている可能性" in reason_all
    assert "あなたが作った差分です" not in reason_all
    reason_concurrent = format_reason(["a.ts"], "diff-concurrent")
    assert "並行して動いている" in reason_concurrent
    assert "あなたが作った差分です" not in reason_concurrent


def test_単体_控えは_cwd_が違えば使わない(tmp_path: Path) -> None:
    # worktree 分離された subagent で、親の作業ツリーの控えを流用しないこと
    entries = [{"cwd": "/a", "prints": {"x.ts": "sha256:a"}}]
    assert take_baseline(entries, "/a")[0] == {"x.ts": "sha256:a"}
    baseline, _, mode = take_baseline(entries, "/b")
    assert baseline is None
    assert mode == "all"


def test_単体_控えの列は旧形式も空ファイルも読める(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"cwd": "/a", "paths": ["x.ts"]}), encoding="utf-8")
    assert take_baseline(load_entries(legacy), "/a")[0] == {"x.ts": None}
    broken = tmp_path / "broken.json"
    broken.write_text("これは JSON ではない", encoding="utf-8")
    assert load_entries(broken) == []
    assert load_entries(tmp_path / "無い.json") == []


def test_単体_並列起動では最古の控えを基準にして見落とさない() -> None:
    # release-gate は subagent を 4 個並列で起こす。後発の控えを基準にすると、
    # 先行 subagent が作った a1.ts が「起動前からあったもの」に化けて見落とされる。
    p, a1 = "sha256:p", "sha256:a1"
    entries = [
        {"cwd": "/r", "prints": {"parent.ts": p}},  # agent1 起動時
        {"cwd": "/r", "prints": {"parent.ts": p, "a1.ts": a1}},  # agent2 起動時 (a1 が写る)
    ]
    baseline, remaining, mode = take_baseline(entries, "/r")
    assert baseline == {"parent.ts": p}, "最古ではない控えを基準にすると置き去りを見落とす"
    assert mode == "diff-concurrent", "並行起動であることを文言に出せなくなる"
    now = {"parent.ts": p, "a1.ts": a1, "a2.ts": "sha256:a2"}
    assert select_paths_to_report(now, baseline) == ["a1.ts", "a2.ts"]
    # 落ちるのは**最も新しい**控え。最古はグループの最後の停止まで残る
    # (最古を落とすと、後から止まる subagent に「a1.ts 込み」の基準が当たる)
    assert remaining == [{"cwd": "/r", "prints": {"parent.ts": p}, "concurrent": True}]
    baseline2, remaining2, mode2 = take_baseline(remaining, "/r")
    assert baseline2 == {"parent.ts": p}
    assert mode2 == "diff-concurrent", "並行グループの残りで diff と言うと精度を偽る"
    assert remaining2 == []


def test_単体_並列グループは停止の順序に関係なく最古の基準を共有する() -> None:
    # A 起動 → A が a.ts を作る → B 起動 (B の控えに a.ts が入る) → **B が先に停止**。
    # ここで最古を消費すると、後から止まる A には a.ts を含む B の控えが当たり、
    # 指紋まで一致するので **A の置き去りが無警告で通る**。
    fp_a = "sha256:a"
    entries = [
        {"cwd": "/r", "prints": {}},  # A 起動時 (まだ何も無い)
        {"cwd": "/r", "prints": {"a.ts": fp_a}},  # B 起動時 (A の a.ts が写る)
    ]
    baseline_b, remaining, mode_b = take_baseline(entries, "/r")
    assert baseline_b == {}, "最古を基準にしないと B の報告が過小になる"
    assert mode_b == "diff-concurrent"

    baseline_a, remaining_a, mode_a = take_baseline(remaining, "/r")
    assert baseline_a == {}, "最古を消費すると、後から止まる A の置き去りを見落とす"
    assert select_paths_to_report({"a.ts": fp_a}, baseline_a) == ["a.ts"]
    assert mode_a == "diff-concurrent", (
        "並行グループの残りなのに diff と言うと、混入の可能性を隠して精度を偽る"
    )
    assert remaining_a == [], "全員が停止したら控えは残さない"


def test_単体_実際の_git_から非_ASCII_の_path_を実在する形で取る(tmp_path: Path) -> None:
    """`git status --porcelain` から `-z` を落とすと何が静かに通るか、を実物で押さえる。

    parse_porcelain 単体のテストでは**呼び出し側の引数 (`-z` の有無) を固定できない**。
    `-z` が無いと git は `日本語.txt` を `"\346\227..."` と C 形式で引用して返し、
    fingerprint() が実在しない path を見て "missing" を返すので、そのファイルへの
    subagent の編集が指紋比較をすり抜ける。
    """
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, timeout=30)
    (tmp_path / "日本語.txt").write_text("x", encoding="utf-8")

    prints, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure
    assert "日本語.txt" in prints, f"C 形式引用のまま返っている: {sorted(prints)}"
    assert prints["日本語.txt"].endswith(":" + hashlib.sha256(b"x").hexdigest()), (
        "実在しない path を見て missing になっている (指紋比較がすり抜ける)"
    )


def test_単体_実際の_git_で_ステージしただけでも指紋が変わる(tmp_path: Path) -> None:
    """親の差分を subagent が `git add` しただけで停止したとき、それを咎めるか。

    これが無いと何が静かに通るか:
        親が変更中のファイル (` M foo`) を subagent がステージすると `M  foo` になるが
        **作業ツリーの内容は 1 バイトも変わらない**。指紋が内容だけなら起動前後で一致し、
        親の差分が勝手にステージされて**次のコミットに混入しうる状態が無警告で通る**。
    """
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, timeout=30,
                       capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "foo.txt").write_text("base\n", encoding="utf-8")
    git("add", "foo.txt")
    git("commit", "-qm", "base")

    # 親が変更する (未ステージ = " M")
    (tmp_path / "foo.txt").write_text("parent edit\n", encoding="utf-8")
    before, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure
    assert set(before) == {"foo.txt"}

    # subagent が内容を変えずにステージするだけ ("M ")
    git("add", "foo.txt")
    after, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure

    assert after["foo.txt"] != before["foo.txt"], (
        "内容が同じでもステージ状態が変われば指紋は変わるべき"
    )


def test_単体_ls_files_はコンフリクトの全ステージを畳む() -> None:
    """これが無いと何が静かに通るか:

    コンフリクト中の path は stage 1/2/3 の 3 行で来る。最後の 1 行で上書きすると、
    stage 1 や 2 だけが解決された変化 (= subagent が競合解決に手を付けた) が
    指紋に出ず、置き去りのまま通る。
    """
    text = (
        "100644 aaa 1\tfoo.txt\0"
        "100644 bbb 2\tfoo.txt\0"
        "100644 ccc 3\tfoo.txt\0"
        "100644 ddd 0\tbar.txt\0"
    )
    prints = parse_ls_files_stage(text)
    assert prints["bar.txt"] == "100644:ddd:0"
    assert prints["foo.txt"] == "100644:aaa:1,100644:bbb:2,100644:ccc:3", (
        "コンフリクトの一部だけが解決されても指紋が変わらない"
    )


def test_単体_index_が読めないときは空ではなく失敗理由を返す(tmp_path: Path) -> None:
    """これが無いと何が静かに通るか:

    `git ls-files` が失敗したのに空の dict を返すと、全 path の index 指紋が
    "noindex" で揃う。**取れなかったものが「index に変化なし」として判定に混ざり**、
    部分ステージの見落としが黙って復活する。失敗は失敗として上へ返す。
    """
    prints, failure = git_index_prints(str(tmp_path))  # git repo ではない
    assert prints == {}
    assert failure is not None, "index が取れていないのに失敗理由が None"
    assert "ls-files" in failure, failure


def test_単体_実際の_git_で_MM_のまま一部だけステージしても指紋が変わる(
    tmp_path: Path,
) -> None:
    """親の `MM` なファイルを subagent が `git add -p` で部分ステージした場合を咎めるか。

    これが無いと何が静かに通るか:
        親がステージ済みと未ステージの両方の差分を持つ `MM` 状態で、subagent が
        未ステージ hunk の**一部だけ**を追加ステージし別の hunk を残すと、
        **XY は `MM` のまま・作業ツリーの内容も 1 バイトも変わらない**。
        `XY|内容` だけの指紋では起動前後が一致し、**index だけが書き換わって
        次のコミットに混入しうる状態が無警告で通る**。

    `parse_ls_files_stage` 単体では**呼び出し側が index を見るかどうかを固定できない**
    ため (`-z` のときと同じ理由)、実物の git で押さえる。
    """
    import subprocess

    def git(*args: str, stdin: str | None = None) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, timeout=30,
                       capture_output=True, text=True, input=stdin)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    # hunk が 2 つに割れるよう離れた行を変更する (近いと 1 hunk に併合される)
    (tmp_path / "foo.txt").write_text(
        "".join(f"l{i}\n" for i in range(1, 41)), encoding="utf-8"
    )
    git("add", "foo.txt")
    git("commit", "-qm", "base")

    def edit(index0: int, text: str) -> None:
        path = tmp_path / "foo.txt"
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[index0] = text
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    edit(0, "PARENT-STAGED")
    git("add", "foo.txt")           # ステージ済みの差分
    edit(9, "HUNK-A")
    edit(29, "HUNK-B")              # 未ステージの差分 2 つ → "MM"

    before, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure
    assert before["foo.txt"].startswith("MM|"), before["foo.txt"]
    content_before = (tmp_path / "foo.txt").read_bytes()

    # subagent が HUNK-A だけを追加ステージする (y = 採用 / n = 見送り)
    git("add", "-p", "foo.txt", stdin="y\nn\n")

    after, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure
    assert after["foo.txt"].startswith("MM|"), "前提が崩れている (XY が MM のままでない)"
    assert (tmp_path / "foo.txt").read_bytes() == content_before, (
        "前提が崩れている (作業ツリーの内容が変わってしまった)"
    )

    assert after["foo.txt"] != before["foo.txt"], (
        "XY も内容も同じでも index が変われば指紋は変わるべき"
    )
    assert select_paths_to_report(after, before) == ["foo.txt"]
    assert select_paths_to_report(after, before) == ["foo.txt"]


def test_単体_控えの置き場はセッションごとに分かれる() -> None:
    assert snapshot_path("aaa") != snapshot_path("bbb")
    assert snapshot_path("../../etc/passwd").name.startswith("claude-hooks-dirty-")


def test_単体_未コミット差分があればブロックする(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result["decision"] == "block"
    assert "a.ts" in result["reason"]


def test_単体_差分が無ければ何も返さない(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    assert handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"}) is None


def test_単体_stop_hook_active_のときは再ブロックしない(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    event = {
        "hook_event_name": "SubagentStop",
        "cwd": "/repo",
        "session_id": "s",
        "stop_hook_active": True,
    }
    assert handle(event) is None


def test_単体_git_が失敗したら異常なしと答えない(monkeypatch) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({}, "git status が失敗した"))
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result is not None
    assert "未検証" in result["systemMessage"]


def test_単体_cwd_が無ければ検査していないと言う() -> None:
    result = handle({"hook_event_name": "SubagentStop"})
    assert "未検証" in result["systemMessage"]


def test_単体_PreToolUse_は_Agent_のときだけ控えを取る(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    handle({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/repo"})
    assert not target.exists()

    handle({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": "/repo"})
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}}
    ]

    # 2 個目の起動は上書きではなく積む (上書きすると先行 subagent の基準が消える)
    handle({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": "/repo"})
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2


def test_単体_停止のたびに控えを_1_件ずつ消費する(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    target.write_text(
        json.dumps(
            [
                {"cwd": "/repo", "prints": {}},
                {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a", "b.ts": "sha256:b"}, None)
    )
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    event = {"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"}
    first = handle(event)
    assert "a.ts" in first["reason"] and "b.ts" in first["reason"]
    # 停止 1 回につき控えは 1 件だけ減る。**残るのは最古**
    # (最古を落とすと、後から止まる側に a.ts 込みの基準が当たって見落とす)
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"cwd": "/repo", "prints": {}, "concurrent": True}
    ]

    second = handle(event)
    assert "a.ts" in second["reason"] and "b.ts" in second["reason"], (
        "並列グループの全員が同じ最古の基準と突き合わせること"
    )
    assert not target.exists(), "列が空になったら控えは残さない"


def test_単体_worktree_の判定は_agent_id_まで一致したときだけ() -> None:
    # 実測 (2026-08-14): worktree は `<親の cwd>/.claude/worktrees/agent-<agent_id>`。
    # 末尾まで見ないと、似た名前のディレクトリを worktree と誤認して
    # **親の作業ツリーの控えを黙って捨てる**ことになる。
    assert worktree_parent("/repo/.claude/worktrees/agent-abc", "abc") == "/repo"
    assert worktree_parent("/repo/.claude/worktrees/agent-abc", "zzz") is None
    assert worktree_parent("/repo/worktrees/agent-abc", "abc") is None
    assert worktree_parent("/repo/.claude/agent-abc", "abc") is None
    assert worktree_parent("/repo", "abc") is None
    assert worktree_parent("/repo/.claude/worktrees/agent-abc", None) is None


def test_単体_worktree_の停止でも控えを回収して蓄積させない(monkeypatch, tmp_path: Path) -> None:
    """Codex #396 r3780243543 の再現条件そのもの。

    無いと何が静かに通るか: worktree 起動は cwd が一致しないので、控えが回収されずに
    1 回の起動ごとに 1 件ずつ溜まる。溜まった控えは後続の親 cwd 起動の基準に化け、
    64 件で新規の控えが保存されなくなる。
    """
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    dirty = {"/repo": {"parent.ts": "sha256:p"}}  # worktree 側は空 (置き去り無しで終わる)
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: (dict(dirty.get(cwd, {})), None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    for i in range(3):
        handle(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "cwd": "/repo",
                "session_id": "s",
                "tool_input": {"subagent_type": "general-purpose", "isolation": "worktree"},
            }
        )
        assert handle(
            {
                "hook_event_name": "SubagentStop",
                "cwd": f"/repo/.claude/worktrees/agent-a{i}",
                "agent_id": f"a{i}",
                "session_id": "s",
            }
        ) is None, "親の未コミット差分で worktree subagent をブロックしてはいけない"

    assert not target.exists(), (
        "worktree 停止で控えが回収されず、親側の列に溜まっている "
        f"(残り: {target.read_text(encoding='utf-8') if target.exists() else ''})"
    )


def test_単体_worktree_の停止は基準を空にして自分の差分だけを全部咎める(
    monkeypatch, tmp_path: Path
) -> None:
    # 実測 (2026-08-14): 親が dirty でも worktree 起動直後の git status は空。
    # だから worktree に残った差分は全部その subagent のもので、"all" の
    # 「混ざっている可能性」ではなく断定してよい。親の差分は 1 件も出さない。
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    dirty = {
        "/repo": {"parent.ts": "sha256:p"},
        "/repo/.claude/worktrees/agent-abc": {"wt.ts": "sha256:w"},
    }
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: (dict(dirty.get(cwd, {})), None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "cwd": "/repo",
            "session_id": "s",
            "tool_input": {"isolation": "worktree"},
        }
    )
    result = handle(
        {
            "hook_event_name": "SubagentStop",
            "cwd": "/repo/.claude/worktrees/agent-abc",
            "agent_id": "abc",
            "session_id": "s",
        }
    )
    assert result["decision"] == "block"
    assert "wt.ts" in result["reason"]
    assert "parent.ts" not in result["reason"], "親の作業ツリーの差分を subagent に押し付けている"
    assert "全部あなたが作った差分です" in result["reason"]
    assert "混ざっている可能性" not in result["reason"], (
        "worktree は起動時に空なので、混入の可能性があるかのように言うと精度を偽る"
    )


def test_単体_印の無い控えでも_worktree_停止で回収して混入を申告する() -> None:
    # 印 (`isolation`) が付くのはこの修正以降。旧形式の控えや tool_input が
    # 渡らない起動でも、回収しないと蓄積が残る。代わりに親 cwd を共有する別の
    # subagent の基準を 1 件奪うので、残りに concurrent を立てて申告する。
    entries = [
        {"cwd": "/repo", "prints": {}},
        {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}},
        {"cwd": "/other", "prints": {}},
    ]
    assert drop_worktree_entry(entries, "/repo") == [
        {"cwd": "/repo", "prints": {}, "concurrent": True},
        {"cwd": "/other", "prints": {}},
    ]
    # 印がある控えがあれば、そちらを落とす (基準として誰にも使われない控えなので、
    # 落としても他の subagent の判定が変わらない)
    marked = [
        {"cwd": "/repo", "prints": {}},
        {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}, "isolation": "worktree"},
    ]
    assert drop_worktree_entry(marked, "/repo") == [{"cwd": "/repo", "prints": {}}]
    assert drop_worktree_entry(entries, "/どこにも無い") == entries


def test_単体_worktree_起動の控えには印が付く(monkeypatch, tmp_path: Path) -> None:
    # 印が無いと、停止側は親 cwd の控えを新しい順に食うしかなくなり、
    # 親 cwd を共有する別 subagent の基準を不必要に奪う。
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)
    handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "cwd": "/repo",
            "session_id": "s",
            "tool_input": {"isolation": "worktree"},
        }
    )
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"cwd": "/repo", "prints": {}, "isolation": "worktree"}
    ]


def test_単体_並列に控えを積んでも_1_件も失われない(tmp_path: Path) -> None:
    """排他が無いと、同時に走る PreToolUse が同じ旧列を読んで後勝ちで書き、控えが消える。

    hook は**別プロセス**として起動するので、スレッドではなく実プロセスで確かめる。
    さらに「読んでから書くまで」に待ちを差し込む — 実運用の窓は数ミリ秒で、
    そのままだと**排他を外しても偶然すり抜けて緑になる** (ミューテーション試験で確認済み)。
    待ちを入れると、排他が無い実装では全プロセスが同じ空の列を読むので必ず落ちる。
    """
    import subprocess
    import sys

    target = tmp_path / "snap.json"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "\n".join(
            [
                "import sys, time, pathlib",
                f"sys.path.insert(0, {str(HOOK_DIR)!r})",
                "import subagent_dirty_guard as guard",
                f"guard.snapshot_path = lambda sid: pathlib.Path({str(target)!r})",
                "guard.git_dirty_paths = lambda cwd: ({sys.argv[1]: 'sha256:x'}, None)",
                "_load = guard.load_entries",
                "def slow(path):",
                "    entries = _load(path)",
                "    time.sleep(0.05)  # 読んでから書くまでの窓を実測より広げる",
                "    return entries",
                "guard.load_entries = slow",
                "guard.handle({'hook_event_name': 'PreToolUse', 'tool_name': 'Agent',",
                "              'cwd': '/repo', 'session_id': 's'})",
            ]
        ),
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen([sys.executable, str(runner), f"f{i}.ts"])  # noqa: S603
        for i in range(12)
    ]
    for proc in procs:
        assert proc.wait(timeout=60) == 0

    entries = json.loads(target.read_text(encoding="utf-8"))
    assert len(entries) == 12, (
        f"控えが {12 - len(entries)} 件消えた — 並列 hook の read-modify-write が競合している"
    )


def test_単体_起動前にあった差分が消えていたら咎める() -> None:
    # 共有 worktree の subagent が親の未コミット差分を restore / reset / commit で
    # clean にする形。`now` 側しか走査しないと path が居なくなって 1 件も咎めない。
    before = {"parent.py": "sha256:aaa", "keep.py": "sha256:bbb"}
    now = {"keep.py": "sha256:bbb"}
    assert select_paths_to_report(now, before) == [], "残っている方は 1 件も無い前提"
    assert select_paths_vanished(now, before) == ["parent.py"]


def test_単体_消失は指紋を持たない旧形式の控えでも判定する() -> None:
    # 消失は「控えに居た」「now に居ない」だけで決まる。指紋の有無に依らない。
    assert select_paths_vanished({}, {"parent.py": None}) == ["parent.py"]
    # 控えそのものが無いときは起動前の状態を知らないので判定しない
    assert select_paths_vanished({}, None) == []


def test_単体_置き去りと消失は別の文言で言い分ける() -> None:
    left = format_reason(["a.ts"], "diff")
    assert "commit と push まで完遂" in left
    assert "もう戻せません" not in left

    gone = format_reason([], "diff", ["parent.py"])
    # 消した側に「commit してから終われ」と言わない (消失は置き去りではない)
    assert "残ったまま終わろうとしています" not in gone
    assert "parent.py" in gone
    assert "あなたが起動する前から在った差分です" in gone
    assert "もう戻せません" in gone

    both = format_reason(["a.ts"], "diff", ["parent.py"])
    assert "a.ts" in both and "parent.py" in both
    assert "commit と push まで完遂" in both and "もう戻せません" in both


def test_単体_消失だけでもブロックする(monkeypatch, tmp_path: Path) -> None:
    # 置き去りが 0 件でも、消失があれば止まること (`if not paths` で早期 return しない)
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    target.write_text(
        json.dumps([{"cwd": "/repo", "prints": {"parent.py": "sha256:aaa"}}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result["decision"] == "block"
    assert "parent.py" in result["reason"]


def test_単体_実際の_git_で_親の差分を_restore_すると咎める(tmp_path: Path) -> None:
    # ご指摘の再現条件そのもの。親の未コミット差分を subagent が `git restore` で
    # 捨てると、その内容は git のどこにも残らない (置き去りより重い事故)。
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, timeout=30,
                       capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "parent.py").write_text("committed\n", encoding="utf-8")
    git("add", "parent.py")
    git("commit", "-qm", "base")

    (tmp_path / "parent.py").write_text("親の未コミット作業\n", encoding="utf-8")
    before, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure
    assert "parent.py" in before, "前提が崩れている (親が dirty でない)"

    git("restore", "parent.py")  # subagent が親の作業を捨てる
    after, failure = git_dirty_paths(str(tmp_path))
    assert failure is None, failure
    assert after == {}, "前提が崩れている (clean になっていない)"

    assert select_paths_to_report(after, before) == [], "残っている方では検出できない前提"
    assert select_paths_vanished(after, before) == ["parent.py"]
